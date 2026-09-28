#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "azure-storage-queue>=12.9",
#     "azure-identity>=1.16",
# ]
# ///
"""Read and print messages from a decision output queue.

Useful for verifying routing after deployment (use --cloud, or point --account-url at
the deployed storage account) or for local experiments against Azurite.

Examples
--------
    uv run scripts/read_decision.py --queue expense-approved
    uv run scripts/read_decision.py --queue expense-review
    uv run scripts/read_decision.py --queue expense-flagged

    # Peek ALL three decision queues at once (handy right after sending a batch):
    uv run scripts/read_decision.py --queue all --peek

    # Against the DEPLOYED account (Entra ID), auto-resolves the queue endpoint from your
    # azd env (OUTPUT_STORAGE_ACCOUNT). The identity used by azd needs the "Storage Queue
    # Data Reader" (peek) or "Message Processor" (receive+delete) role.
    uv run scripts/read_decision.py --queue all --peek --cloud

    # Print the original queue payloads instead of the readable summary:
    uv run scripts/read_decision.py --queue all --peek --cloud --raw

    # Or point at a specific account explicitly:
    uv run scripts/read_decision.py --queue expense-approved \
        --account-url https://<storageaccount>.queue.core.windows.net
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from azure.storage.queue import QueueClient

from _cloud import resolve_queue_account_url

DEV_CONNECTION_STRING = "UseDevelopmentStorage=true"

# The three decision queues the skill routes to, in amount order.
DECISION_QUEUES = ["expense-approved", "expense-review", "expense-flagged"]

QUEUE_LABELS = {
    "expense-approved": "APPROVED",
    "expense-review": "NEEDS REVIEW",
    "expense-flagged": "FLAGGED",
}


def build_queue_client(args: argparse.Namespace, queue_name: str) -> QueueClient:
    """Create a QueueClient from either an account URL (Entra ID) or a connection string."""
    if args.account_url:
        from azure.identity import DefaultAzureCredential

        return QueueClient(
            account_url=args.account_url,
            queue_name=queue_name,
            credential=DefaultAzureCredential(),
        )
    return QueueClient.from_connection_string(args.connection_string, queue_name)


def decode(text: str) -> str:
    """Best-effort: return decoded JSON whether the message is base64 or raw text."""
    try:
        candidate = base64.b64decode(text).decode("utf-8")
        json.loads(candidate)
        return candidate
    except Exception:
        return text


def parse_decision(content: str) -> dict[str, object] | None:
    """Parse a queue message when it contains a decision object."""
    try:
        value = json.loads(decode(content))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def format_amount(decision: dict[str, object]) -> str:
    """Format an amount and currency for terminal output."""
    amount = decision.get("amount")
    currency = str(decision.get("currency") or "").upper()
    if isinstance(amount, (int, float)) and not isinstance(amount, bool):
        prefix = "$" if currency == "USD" else ""
        suffix = f" {currency}" if currency else ""
        return f"{prefix}{amount:,.2f}{suffix}"
    return str(amount or "Amount unknown")


def print_field(label: str, value: object, width: int = 100) -> None:
    """Print a labeled field with wrapped continuation lines."""
    prefix = f"     {label}: "
    print(
        textwrap.fill(
            str(value),
            width=width,
            initial_indent=prefix,
            subsequent_indent=" " * len(prefix),
        )
    )


def print_summary(messages: dict[str, list[str]], queue_names: list[str], peek: bool) -> None:
    """Print decisions grouped by outcome."""
    action = "peeked; messages remain on the queues" if peek else "received and removed from the queues"
    print(f"Expense decisions ({action})")

    total = 0
    for queue_name in queue_names:
        queue_messages = messages[queue_name]
        total += len(queue_messages)
        label = QUEUE_LABELS.get(queue_name, queue_name.upper())
        print(f"\n{label} ({len(queue_messages)}) [{queue_name}]")

        if not queue_messages:
            print("  No messages.")
            continue

        for index, content in enumerate(queue_messages, start=1):
            decision = parse_decision(content)
            if decision is None:
                print(f"  {index}. Unrecognized message")
                print_field("Raw", decode(content))
                continue

            category = decision.get("category") or "category unknown"
            vendor = decision.get("vendor") or "vendor unknown"
            print(f"  {index}. {format_amount(decision)} | {category} | {vendor}")
            print_field("ID", decision.get("expenseId") or "not provided")
            print_field("Policy", decision.get("policyApplied") or "not provided")
            print_field("Reason", decision.get("reason") or "not provided")

    noun = "message" if total == 1 else "messages"
    print(f"\nTotal: {total} {noun}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", "-q", required=True, help="Queue name to read (e.g. expense-approved).")
    parser.add_argument("--max", "-n", type=int, default=32, help="Max messages to read.")
    parser.add_argument("--peek", action="store_true", help="Peek without dequeuing.")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print the original queue name and JSON payload instead of the formatted summary.",
    )
    parser.add_argument(
        "--connection-string",
        default=os.environ.get("AzureWebJobsStorage", DEV_CONNECTION_STRING),
        help="Storage connection string (defaults to Azurite dev storage).",
    )
    parser.add_argument(
        "--account-url",
        default=os.environ.get("OUTPUT_STORAGE_ACCOUNT_URL"),
        help="Queue endpoint, e.g. https://<acct>.queue.core.windows.net. When set, "
        "authenticates with Entra ID (DefaultAzureCredential) instead of a connection string.",
    )
    parser.add_argument(
        "--cloud",
        action="store_true",
        help="Read from the DEPLOYED Azure storage account (Entra ID). Auto-resolves the queue "
        "endpoint from OUTPUT_STORAGE_ACCOUNT_URL / OUTPUT_STORAGE_ACCOUNT or `azd env get-values`.",
    )
    args = parser.parse_args()

    if args.cloud and not args.account_url:
        args.account_url = resolve_queue_account_url()
        if not args.account_url:
            raise SystemExit(
                "--cloud could not resolve the deployed storage account. Run this from the "
                "project root after `azd up` (needs OUTPUT_STORAGE_ACCOUNT in the azd env), "
                "or pass --account-url https://<acct>.queue.core.windows.net explicitly."
            )

    queues = DECISION_QUEUES if args.queue == "all" else [args.queue]
    messages: dict[str, list[str]] = {}
    for queue_name in queues:
        client = build_queue_client(args, queue_name)
        queue_messages: list[str] = []
        if args.peek:
            for msg in client.peek_messages(max_messages=min(args.max, 32)):
                queue_messages.append(msg.content)
        else:
            for msg in client.receive_messages(max_messages=args.max):
                queue_messages.append(msg.content)
                client.delete_message(msg)
        messages[queue_name] = queue_messages

    if args.raw:
        total = 0
        for queue_name in queues:
            for content in messages[queue_name]:
                print(f"[{queue_name}] {decode(content)}")
                total += 1
        print(f"\n({total} message(s) total)")
    else:
        print_summary(messages, queues, args.peek)
    return 0


if __name__ == "__main__":
    sys.exit(main())
