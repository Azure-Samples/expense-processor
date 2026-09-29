# Use cases

Three things prove the skill is genuinely reasoning rather than matching a fixed schema: equivalent
amounts normalized from different representations, the **same amount** decided differently by
category, and **swapping one policy document** rerouting only that category with no code change and
no redeploy.

## The same amount, three representations, three policies

Because every policy sets its **own** thresholds, the category the skill picks changes the outcome.
The bundled demo also varies how that amount arrives, requiring the model to normalize words,
currency codes, and symbols before applying the selected policy:

| Request as received | Normalized amount | Category → policy | Auto-approve ≤ | Decision |
|---|---:|---|---:|---|
| “four hundred and fifty dollars” flight | 450 USD | travel → `travel-policy.md` | $1,000 | **`approve`** |
| `USD 450.00` monitor | 450 USD | equipment → `equipment-software-policy.md` | $500 | **`approve`** |
| `$450` client dinner | 450 USD | meals → `meals-entertainment-policy.md` | $150 (client entertainment always reviews) | **`review`** |
| `450 dollars` uncategorized purchase | 450 USD | fallback → `general-expense-policy.md` | $100 | **`review`** |

Same normalized amount, different surface forms and outcomes. The model must first infer that each
representation means 450 USD; the decision is then driven by *which policy the skill selects*, not by
logic compiled into the prompt. After `azd up`, submit the first three together with the
[manual demo command](deploy.md#run-the-presentation-demo); locally, send them individually:

```bash
uv run --project src --no-sync python scripts/send_expense.py --file samples/travel.txt        # words
uv run --project src --no-sync python scripts/send_expense.py --file samples/equipment.txt     # ISO code
uv run --project src --no-sync python scripts/send_expense.py --file samples/client-dinner.txt # symbol
uv run --project src --no-sync python scripts/read_decision.py --queue all --peek --cloud
```

Those local submissions enter Azurite, while the Queue MCP server writes decisions to the deployed
Azure output queues, so the read command uses `--cloud`.

## A worked example

Send this raw text to the input queue:

> Booked a round-trip flight to Denver for the customer onsite next week. The fare came to four
> hundred and fifty dollars. (Priya)

The skill extracts the details, selects the travel policy, applies it, and produces:

```json
{ "expenseId": "EXP-3F8A1C", "vendor": "United Airlines", "category": "travel", "amount": 450.0, "currency": "USD", "policyApplied": "travel-policy.md", "decision": "approve", "routedTo": "expense-approved", "reason": "Travel expense of 450 USD is at or below the travel policy's 1,000 auto-approve threshold." }
```

…and puts it on the `expense-approved` queue. Each decision carries a `policyApplied` field naming
the document the skill selected.

## Any format in, the right decision out

The skill extracts the details whatever the shape of the message: free text, an email snippet,
`key: value` lines, or JSON. It then applies the matching policy's judgment rules on top of the amount:

```bash
uv run --project src --no-sync python scripts/send_expense.py "lunch ran about $45"  # -> approve
uv run --project src --no-sync python scripts/send_expense.py --file samples/cash-advance.txt
uv run --project src --no-sync python scripts/send_expense.py --file samples/foreign-currency.txt
uv run --project src --no-sync python scripts/send_expense.py --file samples/missing-amount.txt
uv run --project src --no-sync python scripts/send_expense.py --amount 45
```

An `USD 50` cash advance is **flagged**, `€480` is **routed** for FX review (the skill won't guess a
rate), “forty-five bucks” is normalized to 45 USD, and a message with no number is **flagged** for
clarification. All come from the same skill, driven by what it extracts, selects, and reads.

## Swap one policy, reroute one category

The most telling part of the demo: replace a **single** category's policy document and only that
category reroutes. The bundled [`samples/strict-travel-policy.md`](../samples/strict-travel-policy.md)
drops travel auto-approve from $1,000 to **$250**, so the **$450 flight flips from `approve` to
`review`**, while the $450 monitor and the $450 client dinner are **completely unaffected**. The
rules live in the documents, and the skill applies whichever one it selects.

→ Step-by-step commands for the swap are in [customize.md](customize.md#swap-a-single-policy).
