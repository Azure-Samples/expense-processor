# Customize

The rules live in the policy documents in [`src/policies/`](../src/policies/), not in code. Because
the skill fetches the chosen policy on **every** run, replacing a document changes how the matching
requests are routed with **no code change and no redeploy**. Swapping a single category's policy
reroutes only that category.

The [`scripts/set_policy.py`](../scripts/set_policy.py) helper lists, shows, seeds, and replaces the
documents. It targets local Azurite by default; add `--cloud` to target the deployed storage account
over Entra ID (your identity needs **Storage Blob Data Contributor**, which `azd` grants the deployer).

## See what's in effect

```bash
uv run --project src --no-sync python scripts/set_policy.py --list --cloud
uv run --project src --no-sync python scripts/set_policy.py --show travel-policy.md --cloud
```

## Edit a policy in place

Edit any file under `src/policies/`, then push just that document:

```bash
uv run --project src --no-sync python scripts/set_policy.py --file src/policies/travel-policy.md --cloud
```

Send a **new** request afterward. Policies are read per request, so messages already processed keep
their original decision.

## Swap a single policy

Replace one category's document with a different one and watch only that category reroute. The bundled
`samples/strict-travel-policy.md` drops travel auto-approve to $250:

```bash
# Baseline: the $450 flight auto-approves under the shipped travel policy
uv run --project src --no-sync python scripts/send_expense.py --file samples/travel.txt --cloud
uv run --project src --no-sync python scripts/read_decision.py --queue expense-approved --peek --cloud

# Tighten ONLY travel: swap the travel document, leave meals/equipment/general alone
uv run --project src --no-sync python scripts/set_policy.py --file samples/strict-travel-policy.md --name travel-policy.md --cloud

# Same $450 flight is now routed for review; the $450 monitor still auto-approves
uv run --project src --no-sync python scripts/send_expense.py --file samples/travel.txt --cloud
uv run --project src --no-sync python scripts/send_expense.py --file samples/equipment.txt --cloud
uv run --project src --no-sync python scripts/read_decision.py --queue all --peek --cloud

# Restore the shipped travel policy (or re-seed the whole library) when you're done
uv run --project src --no-sync python scripts/set_policy.py --file src/policies/travel-policy.md --cloud
uv run --project src --no-sync python scripts/set_policy.py --seed --cloud
```

## Add a new policy category

1. Create `src/policies/<your-category>-policy.md`. Use a descriptive category filename and start it
   with an `**Applies to:**` line. The Blob MCP tools expose the filename and full document, which
   the skill uses to select and validate the policy. Use the bundled documents as a template for the
   threshold table.
2. Upload it with `uv run --project src --no-sync python scripts/set_policy.py --file
   src/policies/<your-category>-policy.md --cloud`.
3. Send a request in that category. The skill lists the policies, sees the new scope, selects it, and
   applies it. No prompt edit, no redeploy.

The skill picks by filename and confirms the scope from the fetched document, so make both explicit
for a brand-new category.

## Tune the hosted skill

The skill's instructions are the markdown body of
[`src/expense_processor.agent.md`](../src/expense_processor.agent.md). The local model
connection is set in [`src/local.settings.json.sample`](../src/local.settings.json.sample); cloud
model and reasoning settings are configured by the infrastructure deployment. Redeploy with
`azd deploy` after changing the skill definition.
