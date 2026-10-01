# Deploy to Azure

`azd up` provisions everything in [`infra/`](../infra/), deploys both apps, and seeds the policy
documents. It intentionally does not submit expenses, so a fresh deployment starts with empty
queues and you can demonstrate the requests arriving and the hosted skill routing each decision.

## Prerequisites

- An **Azure subscription** with permission to create Functions, Storage, Microsoft Foundry, API
  Management, Connector Namespace preview resources, and role assignments.
- Permission to create Microsoft Entra app registrations and service principals in the deployment
  tenant. The Microsoft Graph Bicep extension creates the MCP authentication registration;
  Azure subscription Contributor/Owner permissions do not provide this directory permission.
- [Azure Developer CLI (`azd`)](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd).
- [uv](https://docs.astral.sh/uv/): the `prepackage` hook runs `uv export` to generate
  `requirements.txt`, and the deployment hooks and helper scripts run with `uv run`.

## Provision and deploy

```bash
uv sync --project src
uv sync --project mcp-server
azd auth login
azd env set PRE_AUTHORIZED_CLIENT_IDS aebc6443-996d-45c2-90f0-388ff96faa56
```

`PRE_AUTHORIZED_CLIENT_IDS` is required:
the ID above authorizes VS Code. Other MCP clients require their own application IDs in this
comma-separated list.

The explicit sync commands prepare both app environments once. The presentation commands below use
`--no-sync`, so they never contact PyPI during the demo.

Provision and deploy:

```bash
azd up
```

`azd up` prompts for an environment name, subscription, and region on first run, then:

1. **Provisions** both resource groups and their independent resources, then configures the
   MCP app's queue endpoint and queue-scoped access grants.
2. **Seeds** the bundled policy documents into the `policies` blob container (post-provision hook).
3. **Packages** each app: its `prepackage` hook regenerates `requirements.txt` from the app's
   `pyproject.toml` + `uv.lock` via `uv export`, so the Functions remote build has one.
4. **Deploys** the hosted-skill and MCP Function Apps.

### Resource groups and deployment ordering

| Resource group | Owned resources |
|---|---|
| `rg-<environment>` | Expense Processor app, plan, identity, expense storage and policies, Connector Namespace, API Management AI Gateway, Foundry, Application Insights, and Log Analytics workspace |
| `rg-<environment>-mcp` | MCP app, plan, identity, host/deployment storage, Application Insights, and Log Analytics workspace |

The apps share no host or monitoring dependencies. Their integration consists of the expense
queue endpoint and the MCP identity's queue-scoped role assignments. `azure.yaml` sets
`resourceGroup` separately for the `api` and `mcp` services so `azd` deploys each app to the
correct group.

[`infra/main.bicep`](../infra/main.bicep) coordinates the app modules and the
[`queue-access.bicep`](../infra/integration/queue-access.bicep) integration module. Bicep
dependencies ensure the queues and MCP identity exist before the grants are created. Provisioning
finishes before application code deployment, so you do not need to deploy the hosted-skill code
first or run `azd up` twice.

The API Management Developer instance can take 30 minutes or longer to provision. It is the lowest
APIM tier that supports both `llm-token-limit` and `llm-content-safety`; unlike Flex Consumption, it
does not scale to zero and has a recurring cost. The tier has no production SLA and is intended for
this sample, not a production financial workload.

## Run the presentation demo

For natural-language submission, listing and reset through an assistant, follow the
[Functions MCP walkthrough](mcp.md). The script-based flow below remains supported.

First, show that all three output queues are empty:

```bash
uv run --project src --no-sync python scripts/read_decision.py --queue all --peek --cloud
```

The command reports zero messages for `expense-approved`, `expense-review`, and `expense-flagged`.
Then submit the three bundled expenses:

```bash
uv run --project src --no-sync python scripts/setup_demo.py send-samples
```

The script sends semantically equivalent 450 USD requests written as words, an ISO currency prefix,
and a currency symbol. Allow up to two minutes for processing, then show the populated output
queues:

```bash
uv run --project src --no-sync python scripts/read_decision.py --queue all --peek --cloud
```

The default view groups decisions by outcome and shows the normalized amount, selected policy, and
reason. Add `--raw` to print the original queue JSON.

To reset the three output queues before another run, omit `--peek`:

```bash
uv run --project src --no-sync python scripts/read_decision.py --queue all --cloud --max 1000
```

This receives and deletes output decisions only; it does not clear the input or poison queue.

## Show the telemetry

Each app has its own workspace-based Application Insights resource and Log Analytics workspace
in its resource group. The processor's Functions host and hosted skills runtime export
OpenTelemetry; the AI Gateway writes to that same processor Application Insights. MCP tool
invocations are logged to the MCP app's Application Insights. Telemetry ingestion authenticates
with each app's own identity; no instrumentation key or API key is used.

After the sample requests finish, open Application Insights:

```bash
azd monitor --overview
```

Select the processor's monitoring resource for the walkthrough below. With two apps, `azd monitor`
may open multiple monitoring views. For an unambiguous target, use the resource IDs in
`EXPENSE_PROCESSOR_APPLICATIONINSIGHTS_RESOURCE_ID` or `EXPENSE_MCP_APPLICATIONINSIGHTS_RESOURCE_ID`
from `azd env get-values` to locate the respective resource in the portal.

Use **Search** (called **Transaction search** in some portal versions) to open a recent operation.
The hosted skills runtime is span-first, so the useful detail appears in the end-to-end transaction
view rather than only in the trace-log list. Look for:

- `agent.run Expense Processor`: the complete queue-triggered skill run.
- `invoke_agent` / `chat`: model activity and token usage emitted by the model framework.
- `execute_tool azureblob_ListFolder_V4`, `execute_tool azureblob_GetFileContentByPath_V2`, and
  `execute_tool azurequeues_PutMessage_V2`: the policy-discovery, policy-read, and routing tool calls.
- `af.agent.*` properties on the run span, including outcome, model, payload sizes, and tool counts.
- API Management requests for `POST /openai/v1/responses`.
- Custom token metrics in the `expense-processor-ai-gateway` namespace.

The preview **Agents** blade may display a generated `gen_ai.agent.name` instead of the hosted
skill's friendly name. Use **Search** or
**Logs** to verify and inspect the complete correlated spans. See
[Troubleshooting](troubleshooting.md#application-insights-agents-doesnt-load-or-show-the-hosted-skill-correctly).

Telemetry can take a few minutes to become queryable. Input and response content are not captured in
the cloud by default; the sample exports metadata, model/tool spans, outcomes, durations, and token
usage without recording the expense text. The gateway diagnostic also omits request and response
bodies.

## What gets deployed

- **Function App:** Flex Consumption, Python 3.13, running the hosted skill.
- **MCP Function App:** independent Flex Consumption app, Python 3.13, with the Functions MCP
  extension and create/list/reset tools. Its resource group contains its own host storage, plan,
  managed identity, Application Insights and Log Analytics workspace. It accesses the processor's
  expense queues through `ExpenseInputStorage` for submission and `ExpenseStorage` for listing
  and reset. Both connections use the same Azure queue endpoint and identity. The endpoint requires
  Microsoft Entra OAuth sign-in through App Service Authentication; see
  [client setup and scoped roles](mcp.md). No access key is required.
- **MCP authentication registration:** a single-tenant Entra application and service principal,
  an exposed `user_impersonation` scope, preauthorization for the configured clients, and a managed-identity federated
  credential for platform sign-in without a client secret.
  are requested.
- **Microsoft Foundry** account + project + a `gpt-5.4` model deployment.
- **API Management AI Gateway:** Developer tier, exposing only `POST /openai/v1/responses` without
  a subscription key. It validates the Function identity, limits the app to 100,000 tokens/minute
  and 1,000,000 tokens/day, applies prompt shielding and medium/high harmful-input filtering, and
  emits token metrics. Semantic caching and prompt/response body logging are disabled.
- **Application Insights + Log Analytics:** one independent pair per app, with 30-day workspace
  retention. Processor telemetry includes the host, hosted skill, model, policy tools and AI Gateway;
  MCP telemetry covers its host and tool invocations.
- **Storage account:** the `expense-requests` input queue, the `expense-approved` /
  `expense-review` / `expense-flagged` output queues, and a `policies` blob container that holds the
  approval policy documents. Shared-key access is **disabled**.
- **Connector Namespace:** managed-identity Azure Blob and Azure Queues connections with separate
  configurable MCP servers. The Blob server exposes only policy listing/content reads; the Queue
  server exposes only `PutMessage_V2`, pins the storage endpoint, and accepts only the three output
  queue names.
- **User-assigned managed identity** + RBAC:

  | Identity | Role | Why |
  |---|---|---|
  | Function app MI | Storage Queue Data Contributor | Functions host storage and input queue trigger |
  | Function app MI | Storage Blob Data Owner | Functions host/deployment storage; not used by the policy MCP connector |
  | Function app MI | AI Gateway caller validated by client ID | the skill calls only the governed Responses API |
  | Function app MI | Monitoring Metrics Publisher | the host and runtime send telemetry to Application Insights |
  | API Management MI | Cognitive Services User | the gateway calls the Foundry model and Content Safety |
  | API Management MI | Monitoring Metrics Publisher | the gateway sends diagnostics and token metrics to Application Insights |
  | Connector Namespace MI | Storage Blob Data Reader on `policies` | the Blob MCP tools list and read policy documents |
  | Connector Namespace MI | Storage Queue Data Message Sender on each output queue | the Queue MCP tool sends decisions, with no role on the input queue |
  | Deploying user | Storage Queue Data Contributor + Storage Blob Data Contributor | so the demo scripts and hooks can send requests, read decisions, and change policies out of the box |

Key values are printed as `azd` outputs and saved to `.azure/<env>/.env` (for example
`OUTPUT_STORAGE_ACCOUNT`, `AZURE_FUNCTION_NAME`, `INPUT_QUEUE_NAME`, and
`POLICY_MCP_SERVER_URL`, and `QUEUE_MCP_SERVER_URL`). Gateway outputs include `AI_GATEWAY_NAME`,
`AI_GATEWAY_URL`, and `AI_GATEWAY_RESPONSES_ENDPOINT`.
Resource-group outputs are `EXPENSE_PROCESSOR_RESOURCE_GROUP` and `EXPENSE_MCP_RESOURCE_GROUP`;
`AZURE_RESOURCE_GROUP` remains the processor group for compatibility. Monitoring resource IDs are
`EXPENSE_PROCESSOR_APPLICATIONINSIGHTS_RESOURCE_ID` and `EXPENSE_MCP_APPLICATIONINSIGHTS_RESOURCE_ID`.
MCP outputs are `EXPENSE_MCP_FUNCTION_NAME` and `EXPENSE_MCP_SERVER_URL`.

The Function uses `AZURE_FUNCTIONS_AGENTS_PROVIDER=azure_openai` in Azure and points
`AZURE_OPENAI_ENDPOINT` at the gateway. The runtime appends `/openai/v1/` and calls the Responses
API. Local settings continue to point directly at an Azure OpenAI endpoint unless you deliberately
replace that endpoint with `AI_GATEWAY_URL`.

## Policy seeding and manual sample submission

The `postprovision` hook in [`azure.yaml`](../azure.yaml) calls
[`scripts/setup_demo.py`](../scripts/setup_demo.py) to upload [`src/policies/*.md`](../src/policies/)
into the `policies` container. It seeds **only when the container is empty**, so re-provisioning never
overwrites a policy you've edited. The read-only connector does not seed or modify policies.

Sample submission is deliberately **not** an `azd` hook. Run
`uv run --project src --no-sync python scripts/setup_demo.py send-samples` when you're ready to
demonstrate the queue activity. The command sends only when all output queues are empty, preventing
accidental duplicate demonstrations.

## Send and read against the cloud yourself

The helper scripts talk to the deployed account over **Entra ID** (no keys). `azd` already granted
your identity the roles above, so you can send, read, and change policies right away. `--cloud`
auto-resolves the account from your `azd` env:

```bash
uv run --project src --no-sync python scripts/setup_demo.py send-samples
uv run --project src --no-sync python scripts/send_expense.py --file samples/travel.txt --cloud
uv run --project src --no-sync python scripts/read_decision.py --queue all --peek --cloud
```

## Clean up

```bash
azd down --purge
```

The top-level subscription deployment tracks both resource groups, so this removes the sample's
resources in both groups. It is not an app-specific cleanup command.

## Redeploy individual apps

After provisioning, deploy application code independently:

```bash
azd deploy api
azd deploy mcp
```

Infrastructure changes require `azd provision` (or `azd up`) before deploying the affected app.

## Infrastructure layout

```text
infra/
  main.bicep                 # subscription deployment; both groups and integration
  main.parameters.json
  expense-processor/         # processor resources and its own monitoring
    main.bicep
    foundry.bicep
    ai-gateway.bicep
    ai-gateway-policy.xml
    blob-policy-connector.bicep
    storage-queues.bicep
  expense-mcp/
    main.bicep               # MCP app resources and its own monitoring
    entra.bicep              # Entra registration and federated platform sign-in credential
  bicepconfig.json           # Microsoft Graph Bicep extension
  integration/
    queue-access.bicep       # role assignments on processor queues for the MCP identity
  common/                    # reusable definitions, not shared deployed resources
    function-app.bicep
    host-rbac.bicep
    monitoring.bicep
  tests/
    test_deployment.py       # compiled-template checks; no cloud provisioning
```

With Azure CLI and its Bicep component installed, validate the deployment layout locally:

```bash
python3 -m unittest discover -s infra/tests -v
```

---

Change a policy without redeploying: [customize.md](customize.md). Hitting an error:
[troubleshooting.md](troubleshooting.md).
