#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "azure-storage-blob>=12.19",
#     "azure-identity>=1.16",
# ]
# ///
"""Show, list, seed, or replace the expense-approval policy documents the hosted skill reads.

The skill picks a policy per request from a set of documents in the `policies` blob
container (one general policy plus category-specific ones — travel, meals &
entertainment, equipment & software). Because the skill fetches the chosen policy on
every run, replacing a document changes how the matching requests are routed with **no
code change and no redeploy** — and swapping a single category's policy reroutes only
that category.

By default this targets local Azurite (AzureWebJobsStorage=UseDevelopmentStorage=true).
Use ``--cloud`` to target the deployed storage account over Entra ID (needs the "Storage
Blob Data Contributor" role, which azd grants the deployer).

Examples
--------
    # List the policies currently in effect (name + what each covers)
    uv run --project src --no-sync python scripts/set_policy.py --list --cloud

    # Show one policy
    uv run --project src --no-sync python scripts/set_policy.py --show travel-policy.md --cloud

    # Swap in a stricter TRAVEL policy, then re-send a $450 flight and watch it flip
    # approve -> review — while meals/equipment are unaffected
    uv run --project src --no-sync python scripts/set_policy.py --file samples/strict-travel-policy.md --name travel-policy.md --cloud
    uv run --project src --no-sync python scripts/send_expense.py --file samples/travel.txt --cloud
    uv run --project src --no-sync python scripts/read_decision.py --queue all --peek --cloud

    # Restore the shipped travel policy (or re-seed everything)
    uv run --project src --no-sync python scripts/set_policy.py --file src/policies/travel-policy.md --cloud
    uv run --project src --no-sync python scripts/set_policy.py --seed --cloud
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from azure.storage.blob import ContainerClient

from _cloud import resolve_blob_account_url

DEV_CONNECTION_STRING = "UseDevelopmentStorage=true"
DEFAULT_POLICY = os.environ.get("POLICY_BLOB", "general-expense-policy.md")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLED_DIR = os.path.join(REPO_ROOT, "src", "policies")

_APPLIES_TO_RE = re.compile(r"^\*\*Applies to:\*\*\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def _scope_of(text: str) -> str:
    match = _APPLIES_TO_RE.search(text)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def build_container_client(args: argparse.Namespace) -> ContainerClient:
    """Create a ContainerClient from an account URL (Entra ID) or a connection string."""
    if args.account_url:
        from azure.identity import DefaultAzureCredential

        return ContainerClient(
            account_url=args.account_url,
            container_name=args.container,
            credential=DefaultAzureCredential(),
        )
    return ContainerClient.from_connection_string(args.connection_string, args.container)


def _ensure_container(container: ContainerClient) -> None:
    try:
        container.create_container()
    except Exception:
        pass  # already exists


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="List the policies in the container and exit.")
    parser.add_argument(
        "--show",
        nargs="?",
        const=DEFAULT_POLICY,
        metavar="NAME",
        help=f"Print a policy document (default: {DEFAULT_POLICY}) and exit.",
    )
    parser.add_argument("--file", "-f", help="Path to a policy document (.md) to upload.")
    parser.add_argument("--name", help="Blob name to upload --file as (default: the file's basename).")
    parser.add_argument("--seed", action="store_true", help="Upload every bundled src/policies/*.md document.")
    parser.add_argument("--container", default=os.environ.get("POLICY_CONTAINER", "policies"), help="Blob container.")
    parser.add_argument(
        "--connection-string",
        default=os.environ.get("AzureWebJobsStorage", DEV_CONNECTION_STRING),
        help="Storage connection string (defaults to Azurite dev storage).",
    )
    parser.add_argument(
        "--account-url",
        default=os.environ.get("OUTPUT_STORAGE_ACCOUNT_BLOB_URL"),
        help="Blob endpoint, e.g. https://<acct>.blob.core.windows.net. When set, authenticates "
        "with Entra ID (DefaultAzureCredential) instead of a connection string.",
    )
    parser.add_argument(
        "--cloud",
        action="store_true",
        help="Target the DEPLOYED storage account (Entra ID). Auto-resolves the blob endpoint "
        "from OUTPUT_STORAGE_ACCOUNT_BLOB_URL / OUTPUT_STORAGE_ACCOUNT or `azd env get-values`.",
    )
    args = parser.parse_args()

    if args.cloud and not args.account_url:
        args.account_url = resolve_blob_account_url()
        if not args.account_url:
            raise SystemExit(
                "--cloud could not resolve the deployed storage account. Run this from the "
                "project root after `azd up` (needs OUTPUT_STORAGE_ACCOUNT in the azd env), "
                "or pass --account-url https://<acct>.blob.core.windows.net explicitly."
            )

    if not (args.list or args.show is not None or args.file or args.seed):
        raise SystemExit(
            "Nothing to do. Use --list, --show [NAME], --file PATH [--name NAME], or --seed."
        )

    container = build_container_client(args)
    target = args.account_url or "Azurite"

    if args.seed:
        _ensure_container(container)
        names = sorted(f for f in os.listdir(BUNDLED_DIR) if f.endswith(".md"))
        for name in names:
            with open(os.path.join(BUNDLED_DIR, name), "r", encoding="utf-8") as handle:
                container.upload_blob(name=name, data=handle.read().encode("utf-8"), overwrite=True)
        print(f"Seeded {len(names)} bundled polic{'y' if len(names) == 1 else 'ies'} -> {args.container} ({target})")

    if args.file:
        blob_name = args.name or os.path.basename(args.file)
        with open(args.file, "r", encoding="utf-8") as handle:
            text = handle.read()
        _ensure_container(container)
        container.upload_blob(name=blob_name, data=text.encode("utf-8"), overwrite=True)
        print(f"Uploaded '{args.file}' -> {args.container}/{blob_name} ({target}, {len(text)} bytes)")

    if args.list:
        print(f"--- policies in {args.container} ({target}) ---")
        rows = []
        for blob in container.list_blobs():
            text = container.download_blob(blob.name).readall().decode("utf-8")
            rows.append((blob.name, _scope_of(text)))
        width = max((len(name) for name, _ in rows), default=0)
        for name, scope in sorted(rows):
            print(f"  {name:<{width}}  {scope}")

    if args.show is not None:
        text = container.download_blob(args.show).readall().decode("utf-8")
        print(f"--- {args.container}/{args.show} ({target}) ---")
        print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
