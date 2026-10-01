# Troubleshooting

## Output queues stay empty

Check the function logs / Application Insights for the skill run and its
`azurequeues_PutMessage_V2` span. Confirm the Function has non-empty `QUEUE_MCP_SERVER_URL` and
`QUEUE_MCP_CLIENT_ID` settings, the Function identity has a connection access policy, and the
Connector Namespace identity has **Storage Queue Data Message Sender** on each output queue. RBAC
can take a few minutes to propagate after deploy. Tail the live trace with `azd monitor --logs`.

## Application Insights **Agents** doesn't load or show the hosted skill correctly

The **Agents** blade is a preview portal experience and can fail to load even when telemetry
ingestion is healthy. Use **Search** or **Logs** to verify the data independently. A generated
identifier might also appear as `gen_ai.agent.name`, while the friendly hosted-skill name remains
available as `af.agent.name` on the parent `agent.run` span.

Open a recent operation in Application Insights **Search** to inspect its correlated model and tool
spans.

## `403` from the scripts against the account

Your identity is missing **Storage Queue Data Contributor** (send/read) or **Storage Blob Data
Contributor** (policies) on the account. `azd` grants both to the deployer, but role propagation can
take a few minutes. Wait and retry.

## Policy changes don't seem to take effect

Confirm what's in effect with
`uv run --project src --no-sync python scripts/set_policy.py --list --cloud`, then send a **new**
request. Policies are read per request, so messages already processed keep their original decision.
Remember the skill selects by category: swapping `travel-policy.md` only affects travel requests.

## Policy MCP tools are missing or return `401` / `403`

Confirm the Function App has non-empty `POLICY_MCP_SERVER_URL` and `POLICY_MCP_CLIENT_ID` settings.
The Function identity needs a connection access policy, and the Connector Namespace system-assigned
identity needs **Storage Blob Data Reader** on the `policies` container. RBAC can take several
minutes to propagate after the first provision.

The connector is intentionally read-only. Seeing only `azureblob_ListFolder_V4` and
`azureblob_GetFileContentByPath_V2` is expected. The folder-list operation is pinned to the
`policies` container so the connector identity does not need storage-account-wide access.

## Queue MCP tool is missing or returns `401` / `403`

Confirm the Function App has non-empty `QUEUE_MCP_SERVER_URL` and `QUEUE_MCP_CLIENT_ID` settings.
The Queue MCP server should expose exactly `azurequeues_PutMessage_V2`. Its connection must report
`Ready` with `managedIdentityAuth`, and the Connector Namespace identity needs **Storage Queue Data
Message Sender** separately on `expense-approved`, `expense-review`, and `expense-flagged`.

A request for `expense-requests` is expected to fail: the tool schema excludes it and the connector
identity has no sender role there. Do not broaden the role to the storage account to bypass that
boundary.

## AI Gateway returns `401`

The gateway requires a Microsoft Entra token for
`https://cognitiveservices.azure.com/.default` and accepts only the Function app's user-assigned
identity client ID. Confirm the Function has `AZURE_CLIENT_ID`, `AZURE_OPENAI_ENDPOINT`, and
`AZURE_OPENAI_DEPLOYMENT`, and that `AZURE_OPENAI_ENDPOINT` equals the `AI_GATEWAY_URL` azd output.
An unsigned request, an APIM subscription key, or a token issued to your developer identity is not
a substitute for the Function identity.

## AI Gateway or Foundry returns `403`

For a normal expense, first allow time for RBAC propagation. The API Management system-assigned
identity needs **Cognitive Services User** on the Foundry account. A `403` from
`llm-content-safety` means prompt shielding or one of the configured harmful-input categories
blocked the request; inspect the APIM request telemetry before changing a threshold.

## AI Gateway returns `429` or token-quota `403`

The Function app shares one counter: 100,000 tokens per minute and 1,000,000 tokens per UTC day.
Wait for the rate window or daily quota to reset. Responses include `Retry-After`,
`x-ratelimit-remaining-tokens`, `x-quota-remaining-tokens`, and `x-tokens-consumed` when available.
Raise the limits in [`infra/expense-processor/ai-gateway-policy.xml`](../infra/expense-processor/ai-gateway-policy.xml) only
after reviewing expected model cost.

## Gateway route returns `404`

The hosted skills runtime uses the OpenAI Responses API, not Chat Completions. Confirm APIM exposes
`POST /openai/v1/responses` and the Function's `AZURE_OPENAI_ENDPOINT` is the gateway root URL, not
the full Responses operation URL.

## Gateway token metrics are missing

Confirm the APIM API diagnostic references the Application Insights logger, the API Management
identity has **Monitoring Metrics Publisher**, and custom metrics are enabled on the Application
Insights resource. Token metrics require a successful supported model response and can take several
minutes to appear. Prompt and response bodies are intentionally not logged.

## `DeploymentNotFound` / model errors

The Foundry model deployment isn't ready, the gateway backend is wrong, or
`AZURE_OPENAI_DEPLOYMENT` does not match it. Check the `azd` outputs, APIM backend, and Function App
configuration.

## The manual demo send was skipped

`uv run --project src --no-sync python scripts/setup_demo.py send-samples` sends only when all output
queues are empty, so repeated runs do not duplicate the presentation data. Read and remove existing
decisions with `uv run --project src --no-sync python scripts/read_decision.py --queue all --cloud
--max 1000`, then run the sample command again. This clears only the three output queues. To submit
just one request, use
`uv run --project src --no-sync python scripts/send_expense.py --file samples/travel.txt --cloud`.

## `uv` can't download a package before the demo

Run `uv sync --project src` once while you have network access. All presentation commands use that
prepared environment with `--no-sync`, so they do not resolve packages or contact PyPI during the
demo.

## Local run: the skill can't reach a model

Local Azurite covers the input trigger, but Connector Namespace has no local emulator. Copy
[`src/local.settings.json.sample`](../src/local.settings.json.sample) to `src/local.settings.json`,
set `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_DEPLOYMENT`, and copy `POLICY_MCP_SERVER_URL` plus
`QUEUE_MCP_SERVER_URL` from `azd env get-values`. Leave both MCP client IDs empty to use your
signed-in developer credential. The deployment grants that developer connection access policies.
Run `az login` and `azd provision` before the first complete local test. Local decisions are written
to the deployed Azure output queues, so read them with `--cloud`.

## `uv run func start` fails to import the runtime

Make sure you're running from the `src/` directory (where `pyproject.toml` lives) so uv resolves the
function app's environment. `uv sync` in `src/` rebuilds `.venv` from `uv.lock`.

## Windows local dev

On Windows, `uv run func start` can fail with
`ModuleNotFoundError: No module named 'azure_functions_agents'` even after a clean `uv sync`. The
cause is the **Microsoft Store `python.exe` alias**: it sits ahead of the uv-managed venv on your
`PATH`, so the Functions host launches the wrong interpreter. Turn it off in **Settings → Apps →
Advanced app settings → App execution aliases** (disable the `python.exe` and `python3.exe` Microsoft
Store entries), open a new terminal, and run `uv run func start` from `src/` again. Installing the
interpreter with uv (`uv python install 3.13`) keeps the venv Python authoritative.

## MCP server - Entra provisioning fails with `ServiceTreeValueMissing`

```text
BadRequest: ServiceTreeValueMissing: ServiceManagementReference field is required for Update, but is missing in the request
```

Some tenants require a service management reference when creating or updating an Entra app
registration. This typically applies to Microsoft internal users; the reference is the
**Service Tree ID** for the service.

Set it in the active environment before running `azd up`:

```bash
azd env set SERVICE_MANAGEMENT_REFERENCE <service-management-reference>
azd up
```

Tenants without this requirement do not need to configure the value.
