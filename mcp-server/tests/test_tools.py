import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from azure.core.exceptions import HttpResponseError
import function_app as tools

FUNCTIONS = {function.get_function_name(): function for function in tools.app.get_functions()}


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def call_tool(self, name, arguments=None, **bindings):
        result = await FUNCTIONS[name].get_user_function()(
            context=json.dumps({"arguments": arguments or {}}), **bindings
        )
        result = json.loads(result)
        if result.get("type") == "call_tool_result":
            envelope = json.loads(result["content"])
            if envelope.get("isError"):
                raise RuntimeError(envelope["content"][0]["text"])
            return envelope["structuredContent"]
        return result

    def clients(self, messages=None, clear_error=None, peek_error=None):
        factory = MagicMock()
        clients = {}
        for name in tools.DECISION_QUEUES:
            client = MagicMock()
            client.__enter__.return_value = client
            client.peek_messages.return_value = (messages or {}).get(name, [])
            if name == clear_error:
                client.clear_messages.side_effect = HttpResponseError("clear failed")
            if name == peek_error:
                client.peek_messages.side_effect = HttpResponseError("peek failed")
            clients[name] = client
        factory.side_effect = lambda name: clients[name]
        manager = MagicMock()
        manager.__enter__.return_value = factory
        return manager, factory, clients

    async def test_create_preserves_original_text_and_generates_id(self):
        request = "Hotel email: €480,00; amount in words: four hundred euros.\nexpenseId: old-id"
        output = MagicMock()
        result = await self.call_tool("create_expense_request", {"request": request}, message=output)
        payload = json.loads(output.set.call_args.args[0])
        self.assertEqual(payload["requestText"], request)
        self.assertEqual(payload["expenseId"], result["expenseId"])
        self.assertRegex(result["expenseId"], r"^EXP-[0-9a-f]{32}$")
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["queue"], "expense-requests")

    async def test_retries_create_distinct_requests(self):
        output = MagicMock()
        first = await self.call_tool("create_expense_request", {"request": "Taxi $25"}, message=output)
        second = await self.call_tool("create_expense_request", {"request": "Taxi $25"}, message=output)
        self.assertNotEqual(first["expenseId"], second["expenseId"])

    async def test_invalid_requests_never_set_output(self):
        for request in ("", "  \n", None, 123, {}):
            with self.subTest(request=request):
                output = MagicMock()
                with self.assertRaises(ValueError):
                    await self.call_tool("create_expense_request", {"request": request}, message=output)
                output.set.assert_not_called()

    async def test_encoded_message_limit_includes_envelope(self):
        for character in ("x", "\u20ac"):
            with self.subTest(character=character):
                output = MagicMock()
                with self.assertRaises(ValueError):
                    await self.call_tool(
                        "create_expense_request",
                        {"request": character * tools.MAX_MESSAGE_BYTES},
                        message=output,
                    )
                output.set.assert_not_called()
        overhead = len(json.dumps(
            {"expenseId": f"EXP-{'0' * 32}", "requestText": ""},
            ensure_ascii=False, separators=(",", ":"),
        ).encode())
        output = MagicMock()
        request = "x" * (tools.MAX_MESSAGE_BYTES - overhead)
        await self.call_tool("create_expense_request", {"request": request}, message=output)
        self.assertEqual(len(output.set.call_args.args[0].encode()), tools.MAX_MESSAGE_BYTES)
        output = MagicMock()
        with self.assertRaises(ValueError):
            await self.call_tool("create_expense_request", {"request": request + "x"}, message=output)
        output.set.assert_not_called()

    async def test_listing_is_peek_only_and_grouped(self):
        messages = {
            name: [
                SimpleNamespace(id=f"id-{outcome}", content=json.dumps({"expenseId": f"EXP-{outcome}"}))
            ]
            for name, outcome in tools.DECISION_QUEUES.items()
        }
        manager, factory, clients = self.clients(messages)
        with patch.object(tools, "expense_queues", return_value=manager):
            result = await self.call_tool("list_expense_decisions")
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["limitPerQueue"], 32)
        self.assertFalse(result["messagesConsumed"])
        self.assertEqual([group["outcome"] for group in result["queues"]], list(tools.DECISION_QUEUES.values()))
        self.assertEqual(factory.call_count, 3)
        for client in clients.values():
            client.peek_messages.assert_called_once_with(max_messages=32)
            client.receive_messages.assert_not_called()
            client.delete_message.assert_not_called()
            client.clear_messages.assert_not_called()

    async def test_empty_queues_are_not_reported_as_pending(self):
        manager, _, _ = self.clients()
        with patch.object(tools, "expense_queues", return_value=manager):
            result = await self.call_tool("list_expense_decisions")
        self.assertEqual(result["total"], 0)
        self.assertTrue(all(not group["decisions"] for group in result["queues"]))

    async def test_bad_decision_payloads_are_explicit(self):
        manager, _, _ = self.clients({
            "expense-approved": [
                SimpleNamespace(id="bad-json", content="not json"),
                SimpleNamespace(id="not-object", content="[]"),
            ]
        })
        with patch.object(tools, "expense_queues", return_value=manager):
            result = await self.call_tool("list_expense_decisions")
        for decision in result["queues"][0]["decisions"]:
            self.assertIn("error", decision)
            self.assertIn("raw", decision)

    async def test_base64_json_decisions_match_script_compatibility(self):
        decision = {"expenseId": "EXP-encoded", "amount": 450}
        encoded = base64.b64encode(json.dumps(decision).encode()).decode()
        manager, _, _ = self.clients({
            "expense-approved": [SimpleNamespace(id="encoded", content=encoded)]
        })
        with patch.object(tools, "expense_queues", return_value=manager):
            result = await self.call_tool("list_expense_decisions")
        self.assertEqual(result["queues"][0]["decisions"][0]["decision"], decision)

    async def test_peek_failure_is_not_an_empty_success(self):
        manager, _, _ = self.clients(peek_error="expense-review")
        with patch.object(tools, "expense_queues", return_value=manager):
            with self.assertRaises(HttpResponseError):
                await self.call_tool("list_expense_decisions")

    async def test_reset_requires_boolean_true(self):
        with patch.object(tools, "expense_queues") as manager:
            for confirm in (False, None, "true", 1):
                with self.subTest(confirm=confirm), self.assertRaisesRegex(RuntimeError, "confirm=true"):
                    await self.call_tool("reset_demo", {"confirm": confirm})
            manager.assert_not_called()

    async def test_reset_clears_only_allowlisted_outputs(self):
        manager, factory, clients = self.clients()
        with patch.object(tools, "expense_queues", return_value=manager):
            result = await self.call_tool("reset_demo", {"confirm": True})
        self.assertEqual(result["clearedQueues"], list(tools.DECISION_QUEUES))
        self.assertFalse(result["inputQueueCleared"])
        self.assertEqual([call.args[0] for call in factory.call_args_list], list(tools.DECISION_QUEUES))
        for client in clients.values():
            client.clear_messages.assert_called_once_with()
            client.delete_queue.assert_not_called()

    async def test_partial_reset_failure_reports_cleared_and_failed_queues(self):
        manager, _, clients = self.clients(clear_error="expense-review")
        with patch.object(tools, "expense_queues", return_value=manager):
            with self.assertRaises(RuntimeError) as error:
                await self.call_tool("reset_demo", {"confirm": True})
        details = json.loads(str(error.exception))
        self.assertEqual(details["clearedQueues"], ["expense-approved", "expense-flagged"])
        self.assertEqual(list(details["errors"]), ["expense-review"])
        clients["expense-flagged"].clear_messages.assert_called_once()


class ConfigurationTests(unittest.TestCase):
    def test_exactly_three_mcp_tools_and_queue_output_binding(self):
        self.assertEqual(set(FUNCTIONS), {"create_expense_request", "list_expense_decisions", "reset_demo"})
        bindings = json.loads(FUNCTIONS["create_expense_request"].get_function_json())["bindings"]
        output = next(binding for binding in bindings if binding["type"] == "queue")
        self.assertEqual(output["queueName"], "expense-requests")
        self.assertEqual(output["connection"], "ExpenseInputStorage")
        self.assertEqual(output["name"], "message")
        self.assertEqual(output["direction"].lower(), "out")
        for name, argument, property_type in (
            ("create_expense_request", "request", "string"),
            ("reset_demo", "confirm", "boolean"),
        ):
            trigger = FUNCTIONS[name].get_trigger()
            properties = json.loads(trigger.tool_properties)
            self.assertEqual(len(properties), 1)
            self.assertEqual(properties[0]["propertyName"], argument)
            self.assertEqual(properties[0]["propertyType"], property_type)
            self.assertTrue(properties[0]["isRequired"])

    def test_host_allows_platform_auth_and_preserves_raw_message_encoding(self):
        settings = json.loads((Path(__file__).parents[1] / "host.json").read_text())
        self.assertEqual(settings["extensions"]["mcp"]["system"]["webhookAuthorizationLevel"], "Anonymous")
        self.assertEqual(settings["extensions"]["queues"]["messageEncoding"], "none")

    def test_local_settings_send_to_the_processors_azurite_and_read_from_azure(self):
        root = Path(__file__).resolve().parents[2]
        mcp = json.loads((root / "mcp-server/local.settings.json.sample").read_text())["Values"]
        processor = json.loads((root / "src/local.settings.json.sample").read_text())["Values"]
        self.assertEqual(mcp["ExpenseInputStorage"], processor["AzureWebJobsStorage"])
        self.assertEqual(mcp["ExpenseInputStorage"], "UseDevelopmentStorage=true")
        self.assertTrue(mcp["ExpenseStorage__queueServiceUri"].startswith("https://"))
        for prefix in ("ExpenseInputStorage", "ExpenseStorage"):
            self.assertNotIn(f"{prefix}__credential", mcp)
            self.assertNotIn(f"{prefix}__clientId", mcp)

    def test_sdk_uses_expense_identity_and_not_host_storage(self):
        with patch.dict(os.environ, {
            "ExpenseStorage__queueServiceUri": "https://expenses.queue.core.windows.net",
            "ExpenseStorage__clientId": "mcp-identity",
            "AzureWebJobsStorage": "wrong-account",
        }, clear=True), patch.object(tools, "DefaultAzureCredential") as credential, patch.object(tools, "QueueClient") as client:
            with tools.expense_queues() as factory:
                factory("expense-approved")
            credential.assert_called_once_with(managed_identity_client_id="mcp-identity")
            client.assert_called_once_with(
                account_url="https://expenses.queue.core.windows.net",
                queue_name="expense-approved",
                credential=credential.return_value.__enter__.return_value,
            )

    def test_missing_expense_settings_fail_instead_of_using_host_storage(self):
        with patch.dict(os.environ, {"AzureWebJobsStorage": "wrong-account"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ExpenseStorage__queueServiceUri"):
                with tools.expense_queues():
                    pass

    def test_local_sdk_uses_developer_credentials_and_closes_them(self):
        with patch.dict(os.environ, {
            "ExpenseStorage__queueServiceUri": "https://expenses.queue.core.windows.net",
            "AzureWebJobsStorage": "UseDevelopmentStorage=true",
            "ExpenseInputStorage": "UseDevelopmentStorage=true",
        }, clear=True), patch.object(tools, "DefaultAzureCredential") as credential, patch.object(tools, "QueueClient") as client:
            with tools.expense_queues() as factory:
                factory("expense-review")
            credential.assert_called_once_with(managed_identity_client_id=None)
            client.assert_called_once_with(
                account_url="https://expenses.queue.core.windows.net",
                queue_name="expense-review",
                credential=credential.return_value.__enter__.return_value,
            )
            credential.return_value.__exit__.assert_called_once_with(None, None, None)
            client.from_connection_string.assert_not_called()

    def test_connection_string_does_not_replace_identity_endpoint(self):
        with patch.dict(os.environ, {"ExpenseStorage": "UseDevelopmentStorage=true"}, clear=True), patch.object(tools, "QueueClient") as client:
            with self.assertRaisesRegex(RuntimeError, "ExpenseStorage__queueServiceUri"):
                with tools.expense_queues():
                    pass
            client.from_connection_string.assert_not_called()


if __name__ == "__main__":
    unittest.main()
