import base64
import binascii
import json
import logging
import os
from collections.abc import Callable, Generator
from contextlib import contextmanager
from uuid import uuid4

import azure.functions as func
from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.storage.queue import QueueClient
from mcp.types import CallToolResult, TextContent

logging.getLogger("azure").setLevel(logging.WARNING)

app = func.FunctionApp()

INPUT_QUEUE = "expense-requests"
DECISION_QUEUES = {
    "expense-approved": "approved",
    "expense-review": "needs_review",
    "expense-flagged": "flagged",
}
PEEK_LIMIT = 32
MAX_MESSAGE_BYTES = 64 * 1024


def parse_decision(content: str) -> dict[str, object] | None:
    try:
        decision = json.loads(content)
    except json.JSONDecodeError:
        try:
            decision = json.loads(base64.b64decode(content, validate=True).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
            return None
    return decision if isinstance(decision, dict) else None


@contextmanager
def expense_queues() -> Generator[Callable[[str], QueueClient], None, None]:
    """Use developer credentials locally and managed identity in Azure."""
    endpoint = os.environ.get("ExpenseStorage__queueServiceUri")
    if not endpoint:
        raise RuntimeError("Configure ExpenseStorage__queueServiceUri for the expense queue account.")
    client_id = os.environ.get("ExpenseStorage__clientId")
    with DefaultAzureCredential(managed_identity_client_id=client_id) as credential:
        yield lambda name: QueueClient(
            account_url=endpoint, queue_name=name, credential=credential
        )


@app.mcp_tool(metadata=json.dumps({"readOnly": False, "idempotent": False}))
@app.mcp_tool_property(
    arg_name="request",
    description="The user's original expense text, email, key-value request or JSON. Preserve wording and amount notation; do not invent missing details.",
    is_required=True,
)
@app.queue_output(arg_name="message", queue_name=INPUT_QUEUE, connection="ExpenseInputStorage")
def create_expense_request(message: func.Out[str], request: str) -> str:
    """Submit one expense for asynchronous processing, not approval.

    Returns the generated expenseId. Submit only when the user requests it.
    Retrying creates another request; listing decisions does not track pending requests.
    """
    if not isinstance(request, str) or not request.strip():
        logging.warning("Rejected an empty or non-text expense request.")
        raise ValueError("request must be non-empty text.")

    expense_id = f"EXP-{uuid4().hex}"
    payload = json.dumps(
        {"expenseId": expense_id, "requestText": request},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(payload.encode("utf-8")) > MAX_MESSAGE_BYTES:
        logging.warning("Rejected an expense request exceeding the queue message limit.")
        raise ValueError("The encoded request must fit within the 64 KiB queue message limit.")

    message.set(payload)
    logging.info("Submitting expense %s.", expense_id)
    return json.dumps({
        "expenseId": expense_id,
        "status": "submitted",
        "queue": INPUT_QUEUE,
        "message": "Submitted for asynchronous evaluation. Use list_expense_decisions later.",
    })


@app.mcp_tool(metadata=json.dumps({"readOnly": True}))
def list_expense_decisions() -> str:
    """List available decisions grouped as approved, needs_review and flagged.

    Peeks at at most 32 visible messages per output queue without consuming them.
    This is a snapshot, not all requests, request history or pending status.
    Repeated peeks do not advance to another page.
    """
    groups = []
    with expense_queues() as queue_client:
        for queue_name, outcome in DECISION_QUEUES.items():
            try:
                with queue_client(queue_name) as queue:
                    messages = list(queue.peek_messages(max_messages=PEEK_LIMIT))
            except AzureError:
                logging.exception("Failed to peek decision queue %s.", queue_name)
                raise

            decisions = []
            for message in messages:
                decision = parse_decision(message.content)
                if decision is None:
                    logging.warning("Unrecognized decision message %s in %s.", message.id, queue_name)
                    decisions.append({
                        "messageId": message.id,
                        "error": "Unrecognized decision format.",
                        "raw": message.content,
                    })
                else:
                    decisions.append({"messageId": message.id, "decision": decision})
            groups.append({"queue": queue_name, "outcome": outcome, "decisions": decisions})

    return json.dumps({
        "queues": groups,
        "total": sum(len(group["decisions"]) for group in groups),
        "limitPerQueue": PEEK_LIMIT,
        "messagesConsumed": False,
        "notice": "Only currently visible decisions are shown, up to 32 per queue. Missing requests may still be processing or beyond this snapshot; there is no pagination.",
    })


@app.mcp_tool(metadata=json.dumps({"destructive": True, "scope": "demo-output-queues"}))
@app.mcp_tool_property(
    arg_name="confirm",
    description="Must be true only after the user explicitly approves deleting all demo decisions for all users. Input requests and policies will not be deleted.",
    is_required=True,
)
def reset_demo(confirm: bool) -> CallToolResult:
    """Destructively clear all messages from the three decision output queues.

    Obtain explicit user approval before calling with confirm=true.
    Leaves input requests, poison queues and policies untouched. Does not cancel
    running requests, which may produce new decisions after reset.
    """
    if confirm is not True:
        logging.warning("Refused a demo reset without explicit confirmation.")
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text="Reset requires confirm=true after explicit user approval.")],
        )

    cleared = []
    errors = {}
    with expense_queues() as queue_client:
        for queue_name in DECISION_QUEUES:
            try:
                with queue_client(queue_name) as queue:
                    queue.clear_messages()
            except AzureError:
                logging.exception("Failed to clear demo decision queue %s.", queue_name)
                errors[queue_name] = "Clear failed; this queue may have been partially cleared. Inspect server logs before retrying."
            else:
                cleared.append(queue_name)
                logging.info("Cleared demo decision queue %s.", queue_name)

    if errors:
        details = {
            "message": "Demo reset was not fully successful.",
            "clearedQueues": cleared,
            "errors": errors,
        }
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=json.dumps(details))],
            structuredContent=details,
        )
    details = {
        "clearedQueues": cleared,
        "inputQueueCleared": False,
        "notice": "Existing output decisions were cleared. Queued or running requests were not cancelled and may produce new decisions.",
    }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(details))],
        structuredContent=details,
    )
