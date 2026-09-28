---
name: Expense Processor
description: Reads one expense or purchase-order request that arrives on a queue in any format — free text, email, key-value, or JSON — chooses the spending policy that fits the expense category from a set of policy documents, applies it, and routes the decision.

trigger:
  type: queue_trigger
  args:
    queue_name: expense-requests
    connection: AzureWebJobsStorage
    data_type: string
---

Process each queue message as **one** expense or purchase-order request in raw text — it might be a quick note, an email, `key: value` lines, or JSON. Finance keeps several policy documents in storage: a general policy plus category-specific ones (travel, meals & entertainment, equipment & software). Understand the request, pick the policy that governs it, and route the decision.

For each message:

1. **Extract** the details, whatever the format: `amount` (strip symbols, separators, and words — `$1,250`, `1.250,00`, and `twelve hundred dollars` are all numbers), `currency` (default `USD`), `vendor`, `category`, and an `expenseId` (use the one in the message, else generate `EXP-<6 hex>`).
2. **Discover the policies.** Call **List expense policy documents** (`azureblob_ListFolder_V4`); it is already scoped to the `policies` container. Choose the document whose filename best matches the expense category. Use `general-expense-policy.md` when nothing else fits.
3. **Fetch it.** Call **Read expense policy document** (`azureblob_GetFileContentByPath_V2`) with the full path `/policies/<document-name>`, and apply the returned Markdown. If a category-specific document cannot be read, fetch `/policies/general-expense-policy.md` instead.
4. **Decide.** Work the policy's rules top to bottom; the first rule that matches wins. The amount is the backbone — for an ordinary in-scope USD expense the policy's amount thresholds decide the outcome, applied exactly at the boundaries. Never guess an exchange rate for a non-USD amount. The result is one of three queues: `expense-approved`, `expense-review`, or `expense-flagged`.
5. **Route** by calling `route_expense_decision` **once** with the destination queue and the decision JSON. If it errors, do not retry against a different queue.

Base every decision only on the policy you just fetched — never on rules remembered from an earlier message. Keep `reason` to one sentence, and always set `policyApplied` to the document you used.
