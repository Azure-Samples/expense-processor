# Troubleshooting

## Output queues stay empty

Check the function logs / Application Insights for the skill run and any `route_expense_decision`
error. Confirm the app deployed and that the managed identity has **Storage Queue Data Contributor**
on the storage account (RBAC can take a few minutes to propagate after deploy). Tail the live trace
with `azd monitor --logs`.

## Application Insights **Agents** doesn't load or show the hosted skill correctly

The **Agents** blade is a preview portal experience and can fail to load even when telemetry
ingestion is healthy. Use **Search** or **Logs** to verify the data independently. A generated
identifier might also appear as `gen_ai.agent.name`, while the friendly hosted-skill name remains
available as `af.agent.name` on the parent `agent.run` span.

Open the correlated telemetry with `azd monitor --logs` and query:

```kusto
AppDependencies
| where TimeGenerated > ago(30m)
| where Name startswith "agent.run"
    or tostring(Properties["gen_ai.operation.name"]) in ("invoke_agent", "chat", "execute_tool")
| project TimeGenerated, OperationId, Name, Success, DurationMs, Properties
| order by TimeGenerated desc
```

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

## `DeploymentNotFound` / model errors

The Foundry model deployment isn't ready or the app settings don't point at it. Check the `azd`
outputs and the Function App configuration.

## The manual demo send was skipped

`uv run --project src --no-sync python scripts/setup_demo.py send-samples` sends only when all output
queues are empty, so repeated runs do not duplicate the presentation data. Read and remove existing
decisions by omitting `--peek`, then run the sample command again. To submit just one request, use
`uv run --project src --no-sync python scripts/send_expense.py --file samples/travel.txt --cloud`.

## `uv` can't download a package before the demo

Run `uv sync --project src` once while you have network access. All presentation commands use that
prepared environment with `--no-sync`, so they do not resolve packages or contact PyPI during the
demo.

## Local run: the skill can't reach a model

Local Azurite covers the queues, but Connector Namespace has no local emulator. Copy
[`src/local.settings.json.sample`](../src/local.settings.json.sample) to `src/local.settings.json`,
set `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_DEPLOYMENT`, and copy `POLICY_MCP_SERVER_URL` from
`azd env get-values`. Leave `POLICY_MCP_CLIENT_ID` empty to use your signed-in developer credential.
The deployment grants that developer a connection access policy. Run `az login` and `azd provision`
before the first complete local test.

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
