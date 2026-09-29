# Deploy to Azure

`azd up` provisions everything in [`infra/`](../infra/), deploys the app, and seeds the policy
documents. It intentionally does not submit expenses, so a fresh deployment starts with empty
queues and you can demonstrate the requests arriving and the hosted skill routing each decision.

## Prerequisites

- An **Azure subscription** with permission to create Functions, Storage, Microsoft Foundry, API
  Management, Connector Namespace preview resources, and role assignments.
- [Azure Developer CLI (`azd`)](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd).
- [uv](https://docs.astral.sh/uv/): the `prepackage` hook runs `uv export` to generate
  `requirements.txt`, and the deployment hooks and helper scripts run with `uv run`.

## Provision and deploy

```bash
uv sync --project src
azd up
```

The explicit sync prepares the project environment once. The presentation commands below use
`--no-sync`, so they never contact PyPI during the demo.

`azd up` prompts for an environment name, subscription, and region on first run, then:

1. **Provisions** the resources below.
2. **Seeds** the bundled policy documents into the `policies` blob container (post-provision hook).
3. **Packages** the app: the `prepackage` hook regenerates `src/requirements.txt` from
   `src/pyproject.toml` + `src/uv.lock` via `uv export`, so the Functions remote build has one.
4. **Deploys** the function app.

The API Management Developer instance can take 30 minutes or longer to provision. It is the lowest
APIM tier that supports both `llm-token-limit` and `llm-content-safety`; unlike Flex Consumption, it
does not scale to zero and has a recurring cost. The tier has no production SLA and is intended for
this sample, not a production financial workload.

## Run the presentation demo

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

The deployment creates a workspace-based Application Insights resource and configures both the
Functions host and hosted skills runtime to export OpenTelemetry. Telemetry ingestion authenticates
with the Function App's user-assigned managed identity; no instrumentation key or API key is used.

After the sample requests finish, open Application Insights:

```bash
azd monitor --overview
```

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

The preview **Agents** blade can fail to load even when ingestion is healthy, and it may display a
generated `gen_ai.agent.name` instead of the hosted skill's friendly name. Use **Search** or
**Logs** to verify and inspect the complete correlated spans. See
[Troubleshooting](troubleshooting.md#application-insights-agents-doesnt-load-or-show-the-hosted-skill-correctly).

To query the runs directly, open the Logs view:

```bash
azd monitor --logs
```

Then run:

```kusto
AppDependencies
| where TimeGenerated > ago(30m)
| where Name startswith "agent.run"
| project
    TimeGenerated,
    OperationId,
    Name,
    Success,
    DurationMs,
    Model = tostring(Properties["af.agent.model"]),
    Outcome = tostring(Properties["af.agent.outcome"]),
    ToolCalls = toint(Properties["af.agent.tool_call_count"])
| order by TimeGenerated desc
```

Select an `OperationId`, then use this query to show the full correlated transaction:

```kusto
let operationId = "<operation-id>";
union AppRequests, AppDependencies, AppTraces, AppExceptions
| where OperationId == operationId
| project TimeGenerated, itemType, Name, Message, Success, DurationMs
| order by TimeGenerated asc
```

Telemetry can take a few minutes to become queryable. Input and response content are not captured in
the cloud by default; the sample exports metadata, model/tool spans, outcomes, durations, and token
usage without recording the expense text. The gateway diagnostic also omits request and response
bodies.

Query gateway token metrics with:

```kusto
AppMetrics
| where TimeGenerated > ago(30m)
| where Name == "azure.ai_gateway.client.token.usage"
| where tostring(Properties["Service Name"]) startswith "apim-expense-skill-"
| project
    TimeGenerated,
    TokenType = tostring(Properties["gen_ai.token.type"]),
    Model = tostring(Properties["ModelDeployment"]),
    Sum,
    Count
| order by TimeGenerated desc
```

Depending on the Application Insights table experience, the same custom metrics can appear in
`customMetrics` with lowercase column names.

## What gets deployed

- **Function App:** Flex Consumption, Python 3.13, running the hosted skill.
- **Microsoft Foundry** account + project + a `gpt-5.4` model deployment.
- **API Management AI Gateway:** Developer tier, exposing only `POST /openai/v1/responses` without
  a subscription key. It validates the Function identity, limits the app to 100,000 tokens/minute
  and 1,000,000 tokens/day, applies prompt shielding and medium/high harmful-input filtering, and
  emits token metrics. Semantic caching and prompt/response body logging are disabled.
- **Application Insights + Log Analytics:** correlated Function host, hosted skill, model, and tool
  telemetry with 30-day workspace retention.
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

---

Change a policy without redeploying: [customize.md](customize.md). Hitting an error:
[troubleshooting.md](troubleshooting.md).
