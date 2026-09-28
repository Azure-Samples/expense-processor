#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "azure-storage-queue>=12.9",
#     "azure-identity>=1.16",
# ]
# ///
"""Drop an expense/order request (free text or JSON) on the expense-requests queue.

By default it targets local Azurite (AzureWebJobsStorage=UseDevelopmentStorage=true),
so enqueuing needs no cloud resources. (The hosted skill that then processes the message from
the queue still calls your configured Azure OpenAI / Foundry model to reason over it.)

Examples
--------
    # Send one of the bundled samples (words, key-value text, EUR, cash advance, ...)
    uv run scripts/send_expense.py --file samples/travel.txt
    uv run scripts/send_expense.py --file samples/equipment.txt
    uv run scripts/send_expense.py --file samples/cash-advance.txt

    # Send free text or inline JSON directly — the skill extracts the details either way
    uv run scripts/send_expense.py "lunch with the team ran about $45"
    uv run scripts/send_expense.py '{"description":"team lunch","amount":45,"currency":"USD"}'

    # Generate a message from just an amount (quick way to prove the amount drives the decision)
    uv run scripts/send_expense.py --amount 45
    uv run scripts/send_expense.py --amount 5000

    # Against the DEPLOYED account (Entra ID), auto-resolves the queue endpoint from your
    # azd env (OUTPUT_STORAGE_ACCOUNT). The identity used by azd needs the "Storage Queue
    # Data Message Sender" (or Contributor) role on the account.
    uv run scripts/send_expense.py --amount 250 --cloud

    # Or point at a specific account explicitly:
    uv run scripts/send_expense.py --file samples/travel.txt \
        --account-url https://<storageaccount>.queue.core.windows.net
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from azure.storage.queue import QueueClient, TextBase64EncodePolicy

from _cloud import resolve_queue_account_url

DEV_CONNECTION_STRING = "UseDevelopmentStorage=true"


def build_queue_client(args: argparse.Namespace, encode_policy) -> QueueClient:
    """Create a QueueClient from either an account URL (Entra ID) or a connection string.

    ``--account-url`` (or the OUTPUT_STORAGE_ACCOUNT_URL env var) selects Entra ID auth via
    DefaultAzureCredential, which is the right choice for a deployed account that has local
    (shared-key) auth disabled. Otherwise we fall back to a connection string, which defaults
    to local Azurite dev storage.
    """
    if args.account_url:
        from azure.identity import DefaultAzureCredential

        return QueueClient(
            account_url=args.account_url,
            queue_name=args.queue,
            credential=DefaultAzureCredential(),
            message_encode_policy=encode_policy,
        )
    return QueueClient.from_connection_string(
        args.connection_string, args.queue, message_encode_policy=encode_policy
    )


def build_message(args: argparse.Namespace) -> str:
    if args.file:
        return open(args.file, "r", encoding="utf-8").read().strip()
    if args.message:
        return args.message.strip()
    if args.amount is not None:
        return json.dumps(
            {
                "expenseId": args.expense_id,
                "description": "Ad-hoc expense",
                "amount": args.amount,
                "currency": "USD",
                "submittedBy": "cli",
            }
        )
    raise SystemExit("Provide a message: --file PATH, --amount N, or an inline JSON string.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("message", nargs="?", help="Inline JSON message to enqueue.")
    parser.add_argument("--file", "-f", help="Path to a JSON file to enqueue.")
    parser.add_argument("--amount", "-a", type=float, help="Generate a message with this dollar amount.")
    parser.add_argument("--expense-id", default="EXP-CLI", help="expenseId to use with --amount.")
    parser.add_argument("--queue", "-q", default="expense-requests", help="Target queue name.")
    parser.add_argument(
        "--connection-string",
        default=os.environ.get("AzureWebJobsStorage", DEV_CONNECTION_STRING),
        help="Storage connection string (defaults to Azurite dev storage).",
    )
    parser.add_argument(
        "--account-url",
        default=os.environ.get("OUTPUT_STORAGE_ACCOUNT_URL"),
        help="Queue endpoint, e.g. https://<acct>.queue.core.windows.net. When set, "
        "authenticates with Entra ID (DefaultAzureCredential) instead of a connection string. "
        "Use this for a deployed account with shared-key auth disabled.",
    )
    parser.add_argument(
        "--cloud",
        action="store_true",
        help="Target the DEPLOYED Azure storage account (Entra ID). Auto-resolves the queue "
        "endpoint from OUTPUT_STORAGE_ACCOUNT_URL / OUTPUT_STORAGE_ACCOUNT or `azd env get-values`.",
    )
    parser.add_argument(
        "--base64",
        action="store_true",
        help="Base64-encode the message. Default is raw text, which the Python queue "
        "trigger (extension bundle 4.x) delivers to the skill unchanged.",
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

    message = build_message(args)
    # Validate/pretty the JSON for the console, but send the compact original text.
    try:
        parsed = json.loads(message)
        preview = json.dumps(parsed)
    except json.JSONDecodeError:
        preview = message  # allow deliberately malformed input to exercise the flag path

    encode_policy = TextBase64EncodePolicy() if args.base64 else None
    client = build_queue_client(args, encode_policy)
    # Locally the queue may not exist yet; create it. In the cloud it is provisioned by azd
    # and a Message-Sender-only identity can't create queues, so don't try.
    if not args.account_url:
        try:
            client.create_queue()
        except Exception:
            pass  # already exists

    client.send_message(message)
    target = args.account_url or "Azurite"
    print(f"Sent to '{args.queue}' ({target}): {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
