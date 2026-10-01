import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def module(template, name):
    return next(
        resource for resource in template["resources"]
        if resource["type"] == "Microsoft.Resources/deployments" and resource["name"] == name
    )


def parameters(deployment):
    return {name: value["value"] for name, value in deployment["properties"]["parameters"].items()}


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "main.json"
            build = subprocess.run(
                ["az", "bicep", "build", "--file", str(ROOT / "infra/main.bicep"), "--outfile", str(output)],
                capture_output=True, text=True,
            )
            if build.returncode:
                raise RuntimeError(build.stdout + build.stderr)
            cls.template = json.loads(output.read_text())
        cls.processor = module(cls.template, "expenseProcessor")
        cls.mcp = module(cls.template, "expenseMcp")
        cls.access = module(cls.template, "expenseMcpQueueAccess")
        cls.processor_template = cls.processor["properties"]["template"]
        cls.mcp_template = cls.mcp["properties"]["template"]

    def test_apps_have_different_resource_groups_and_environment_tags(self):
        groups = [resource for resource in self.template["resources"] if resource["type"] == "Microsoft.Resources/resourceGroups"]
        self.assertEqual(len(groups), 2)
        self.assertNotEqual(self.processor["resourceGroup"], self.mcp["resourceGroup"])
        self.assertEqual({group["name"] for group in groups}, {self.processor["resourceGroup"], self.mcp["resourceGroup"]})
        self.assertEqual(self.access["resourceGroup"], self.processor["resourceGroup"])
        self.assertEqual(self.template["variables"]["tags"], {"azd-env-name": "[parameters('environmentName')]"})
        for group in groups:
            self.assertEqual(group["tags"], "[variables('tags')]")

    def test_service_resource_groups_are_explicitly_configured(self):
        source = (ROOT / "azure.yaml").read_text()
        for service, output in (
            ("api", "EXPENSE_PROCESSOR_RESOURCE_GROUP"),
            ("mcp", "EXPENSE_MCP_RESOURCE_GROUP"),
        ):
            with self.subTest(service=service):
                self.assertRegex(source, rf"(?m)^  {service}:\n    resourceGroup: \$\{{{output}\}}$")
        outputs = self.template["outputs"]
        self.assertEqual(outputs["AZURE_RESOURCE_GROUP"]["value"], self.processor["resourceGroup"])
        self.assertEqual(outputs["EXPENSE_PROCESSOR_RESOURCE_GROUP"]["value"], self.processor["resourceGroup"])
        self.assertEqual(outputs["EXPENSE_MCP_RESOURCE_GROUP"]["value"], self.mcp["resourceGroup"])

    def test_mcp_accepts_only_queue_endpoint_as_cross_app_configuration(self):
        inputs = parameters(self.mcp)
        self.assertEqual(set(inputs), {"name", "resourceToken", "location", "tags", "expenseQueueServiceUri", "preAuthorizedClientIds", "serviceManagementReference"})
        self.assertIn("expenseProcessor", inputs["expenseQueueServiceUri"])
        self.assertIn("EXPENSE_QUEUE_SERVICE_URI", inputs["expenseQueueServiceUri"])
        source = (ROOT / "infra/expense-mcp/main.bicep").read_text()
        self.assertNotIn("expenseStorageAccountName", source)
        self.assertNotIn("Microsoft.Authorization/roleAssignments", source)

    def test_service_management_reference_reaches_the_graph_application(self):
        input_file = json.loads((ROOT / "infra/main.parameters.json").read_text())
        self.assertEqual(
            input_file["parameters"]["serviceManagementReference"]["value"],
            "${SERVICE_MANAGEMENT_REFERENCE}",
        )
        self.assertEqual(parameters(self.mcp)["serviceManagementReference"], "[parameters('serviceManagementReference')]")
        entra = module(self.mcp_template, "expenseMcpEntra")
        self.assertEqual(parameters(entra)["serviceManagementReference"], "[parameters('serviceManagementReference')]")
        application = entra["properties"]["template"]["resources"]["application"]["properties"]
        self.assertEqual(
            application["serviceManagementReference"],
            "[if(empty(parameters('serviceManagementReference')), null(), parameters('serviceManagementReference'))]",
        )

    def test_mcp_uses_platform_oauth_without_a_system_key(self):
        app = parameters(module(self.mcp_template, "expenseMcpApp"))
        auth = app["authSettings"]
        self.assertTrue(auth["platform"]["enabled"])
        self.assertTrue(auth["globalValidation"]["requireAuthentication"])
        self.assertEqual(auth["globalValidation"]["unauthenticatedClientAction"], "Return401")
        self.assertTrue(auth["httpSettings"]["requireHttps"])
        provider = auth["identityProviders"]["azureActiveDirectory"]
        self.assertTrue(provider["enabled"])
        self.assertIn("'expenseMcpEntra'", provider["registration"]["clientId"])
        self.assertEqual(provider["registration"]["clientSecretSettingName"], "OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID")
        self.assertIn("'expenseMcpEntra'", provider["validation"]["allowedAudiences"][0])
        self.assertIn("preAuthorizedClientIds", provider["validation"]["defaultAuthorizationPolicy"]["allowedApplications"])
        settings = app["appSettings"]
        host = json.loads((ROOT / "mcp-server/host.json").read_text())
        self.assertEqual(host["extensions"]["mcp"]["system"]["webhookAuthorizationLevel"], "Anonymous")
        self.assertNotIn("AzureFunctionsJobHost__extensions__mcp__system__webhookAuthorizationLevel", settings)
        self.assertIn("/user_impersonation", settings["WEBSITE_AUTH_PRM_DEFAULT_WITH_SCOPES"])
        self.assertIn("'expenseMcpIdentity'", settings["OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID"])
        self.assertEqual(settings["WEBSITE_AUTH_AAD_ALLOWED_TENANTS"], "[tenant().tenantId]")
        self.assertEqual(provider["login"]["loginParameters"], ["scope=openid profile email"])
        self.assertFalse(auth["login"]["tokenStore"]["enabled"])

    def test_registration_exposes_mcp_scope_and_preauthorizes_configured_clients_without_graph_permissions(self):
        client_parameter = self.template["parameters"]["preAuthorizedClientIds"]
        self.assertEqual(client_parameter["type"], "string")
        self.assertEqual(client_parameter["minLength"], 1)
        self.assertNotIn("defaultValue", client_parameter)
        input_file = json.loads((ROOT / "infra/main.parameters.json").read_text())
        self.assertEqual(input_file["parameters"]["preAuthorizedClientIds"]["value"], "${PRE_AUTHORIZED_CLIENT_IDS}")
        self.assertEqual(parameters(self.mcp)["preAuthorizedClientIds"], "[variables('preAuthorizedClientIdsArray')]")
        conversion = self.template["variables"]["preAuthorizedClientIdsArray"]
        self.assertIn("split(parameters('preAuthorizedClientIds'), ',')", conversion)
        self.assertIn("trim(", conversion)
        entra = module(self.mcp_template, "expenseMcpEntra")
        self.assertEqual(parameters(entra)["preAuthorizedClientIds"], "[parameters('preAuthorizedClientIds')]")
        self.assertIn("'expenseMcpIdentity'", parameters(entra)["managedIdentityPrincipalId"])
        template = entra["properties"]["template"]
        application = template["resources"]["application"]["properties"]
        self.assertEqual(application["signInAudience"], "AzureADMyOrg")
        self.assertEqual(application["api"]["requestedAccessTokenVersion"], 2)
        scope = application["api"]["oauth2PermissionScopes"][0]
        self.assertEqual(scope["value"], "user_impersonation")
        self.assertTrue(scope["isEnabled"])
        preauthorized = application["api"]["copy"][0]
        self.assertEqual(preauthorized["name"], "preAuthorizedApplications")
        self.assertEqual(preauthorized["input"]["delegatedPermissionIds"], [scope["id"]])
        self.assertNotIn("requiredResourceAccess", application)
        self.assertNotIn("passwordCredentials", application)
        federated = template["resources"]["federatedCredential"]["properties"]
        self.assertEqual(federated["subject"], "[parameters('managedIdentityPrincipalId')]")
        self.assertEqual(federated["audiences"], ["api://AzureADTokenExchange"])

    def test_oauth_is_not_enabled_for_the_queue_only_processor(self):
        app = parameters(module(self.processor_template, "api"))
        self.assertNotIn("authSettings", app)
        self.assertNotIn("WEBSITE_AUTH_PRM_DEFAULT_WITH_SCOPES", app["appSettings"])
        self.assertNotIn("AzureFunctionsJobHost__extensions__mcp__system__webhookAuthorizationLevel", app["appSettings"])

    def test_remote_clients_use_oauth_instead_of_an_inline_key(self):
        configurations = [
            (path, namespace)
            for path, namespace in (
                (ROOT / ".vscode/mcp.json", "servers"),
                (ROOT / ".mcp.json", "mcpServers"),
            )
            if path.is_file()
        ]
        self.assertTrue(configurations, "An MCP client configuration is required.")
        for path, namespace in configurations:
            config = json.loads(path.read_text())
            self.assertTrue(config[namespace])
            for name, server in config[namespace].items():
                with self.subTest(configuration=path, server=name):
                    self.assertNotIn("x-functions-key", server.get("headers", {}))
            self.assertFalse(any(item["id"] == "expense-mcp-key" for item in config.get("inputs", [])))

    def test_queue_endpoint_is_read_only_after_storage_creation(self):
        endpoint = self.processor_template["outputs"]["EXPENSE_QUEUE_SERVICE_URI"]["value"]
        self.assertIn("'storageQueues'", endpoint)
        queues = module(self.processor_template, "storageQueues")
        self.assertIn("'storage'", " ".join(queues["dependsOn"]))
        self.assertIn("'storage'", parameters(queues)["storageAccountName"])
        self.assertIn("primaryEndpoints.queue", queues["properties"]["template"]["outputs"]["queueServiceUri"]["value"])
        self.assertIn("'expenseProcessor'", " ".join(self.mcp["dependsOn"]))

    def test_mcp_dependencies_are_local_to_its_resource_group(self):
        app = parameters(module(self.mcp_template, "expenseMcpApp"))
        for field, name in (
            ("applicationInsightsName", "expenseMcpMonitoring"),
            ("storageAccountName", "expenseMcpHostStorage"),
            ("identityId", "expenseMcpIdentity"),
            ("appServicePlanId", "expenseMcpPlan"),
        ):
            with self.subTest(field=field):
                self.assertIn(name, app[field])
                self.assertNotIn("extensionResourceId", app[field])
        settings = app["appSettings"]
        self.assertEqual(settings["ExpenseStorage__queueServiceUri"], "[parameters('expenseQueueServiceUri')]")
        self.assertEqual(settings["ExpenseStorage__credential"], "managedidentity")
        self.assertIn("expenseMcpIdentity", settings["ExpenseStorage__clientId"])
        for suffix in ("queueServiceUri", "credential", "clientId"):
            self.assertEqual(settings[f"ExpenseInputStorage__{suffix}"], settings[f"ExpenseStorage__{suffix}"])
        self.assertNotIn("ExpenseInputStorage", settings)
        self.assertFalse(any(key.startswith(("POLICY_MCP", "QUEUE_MCP", "AZURE_OPENAI")) for key in settings))
        self.assertEqual(module(self.mcp_template, "expenseMcpHostStorage")["properties"]["parameters"]["allowSharedKeyAccess"]["value"], False)

    def test_only_mcp_has_an_always_ready_http_instance(self):
        mcp = module(self.mcp_template, "expenseMcpApp")
        self.assertEqual(parameters(mcp)["alwaysReady"], [{"name": "http", "instanceCount": 1}])
        processor = module(self.processor_template, "api")
        self.assertNotIn("alwaysReady", parameters(processor))
        for app in (mcp, processor):
            template = app["properties"]["template"]
            self.assertEqual(template["parameters"]["alwaysReady"]["defaultValue"], [])
            site = next(resource for resource in template["resources"] if resource["type"] == "Microsoft.Resources/deployments")
            scaling = site["properties"]["parameters"]["functionAppConfig"]["value"]["scaleAndConcurrency"]
            self.assertIn("if(empty(parameters('alwaysReady')), createObject()", scaling)
            self.assertIn("'alwaysReady', parameters('alwaysReady')", scaling)

    def test_apps_have_separate_insights_and_separate_workspaces(self):
        processor_monitor = module(self.processor_template, "processorMonitoring")
        mcp_monitor = module(self.mcp_template, "expenseMcpMonitoring")
        processor_names = parameters(processor_monitor)
        mcp_names = parameters(mcp_monitor)
        self.assertNotEqual(processor_names["applicationInsightsName"], mcp_names["applicationInsightsName"])
        self.assertNotEqual(processor_names["workspaceName"], mcp_names["workspaceName"])
        for deployment in (processor_monitor, mcp_monitor):
            with self.subTest(deployment=deployment["name"]):
                template = deployment["properties"]["template"]
                insights = parameters(module(template, "applicationInsights"))
                self.assertTrue(insights["disableLocalAuth"])
                self.assertIn("'logAnalytics'", insights["workspaceResourceId"])
                self.assertEqual(parameters(module(template, "logAnalytics"))["dataRetention"], 30)

    def test_mcp_telemetry_role_is_scoped_to_its_own_insights(self):
        access = parameters(module(self.mcp_template, "expenseMcpHostRbac"))
        self.assertIn("expenseMcpMonitoring", access["appInsightsName"])
        self.assertIn("expenseMcpIdentity", access["managedIdentityPrincipalId"])
        self.assertIn("expenseMcpHostStorage", access["storageAccountName"])

    def test_integration_waits_for_both_apps_and_only_grants_queue_roles(self):
        dependencies = " ".join(self.access["dependsOn"])
        self.assertIn("'expenseProcessor'", dependencies)
        self.assertIn("'expenseMcp'", dependencies)
        integration_inputs = parameters(self.access)
        self.assertIn("'expenseMcp'", integration_inputs["principalId"])
        self.assertIn("'expenseProcessor'", integration_inputs["expenseStorageAccountName"])
        template = self.access["properties"]["template"]
        self.assertEqual(template["variables"]["messageSenderRoleId"], "c6a89b2d-59bc-44d0-9896-0f6e12d7b80a")
        self.assertEqual(template["variables"]["queueContributorRoleId"], "974c5e8b-45b9-4653-ba55-5f855dd0fb88")
        self.assertEqual(len(template["resources"]), 2)
        for resource in template["resources"]:
            self.assertEqual(resource["type"], "Microsoft.Authorization/roleAssignments")
            self.assertIn("Microsoft.Storage/storageAccounts/queueServices/queues", resource["scope"])
            self.assertEqual(resource["properties"]["principalId"], "[parameters('principalId')]")

    def test_processor_queue_names_and_public_outputs_are_preserved(self):
        self.assertEqual(self.processor_template["variables"]["inputQueueName"], "expense-requests")
        self.assertEqual(self.processor_template["variables"]["outputQueueNames"], [
            "expense-approved", "expense-review", "expense-flagged",
        ])
        for name in (
            "AZURE_FUNCTION_NAME", "FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL",
            "AI_GATEWAY_NAME", "AI_GATEWAY_URL", "AI_GATEWAY_RESPONSES_ENDPOINT",
            "OUTPUT_STORAGE_ACCOUNT", "INPUT_QUEUE_NAME", "POLICY_MCP_SERVER_URL",
            "QUEUE_MCP_SERVER_URL", "POLICY_CONNECTOR_NAMESPACE_NAME",
            "POLICY_CONNECTOR_CONNECTION_NAME", "QUEUE_CONNECTOR_CONNECTION_NAME",
        ):
            with self.subTest(output=name):
                self.assertIn("'expenseProcessor'", self.template["outputs"][name]["value"])


if __name__ == "__main__":
    unittest.main()
