#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "azure-storage-blob>=12.19",
#     "azure-storage-queue>=12.9",
#     "azure-identity>=1.16",
# ]
# ///
"""Prepare the deployed policy library or submit the bundled demo requests."""

from __future__ import annotations

import argparse
from pathlib import Path

from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient
from azure.storage.queue import QueueClient

from _cloud import resolve_blob_account_url, resolve_queue_account_url

POLICY_CONTAINER = "policies"
INPUT_QUEUE = "expense-requests"
OUTPUT_QUEUES = ("expense-approved", "expense-review", "expense-flagged")
SAMPLES = ("travel.txt", "equipment.txt", "client-dinner.txt")
REPO_ROOT = Path(__file__).resolve().parent.parent


def seed_policies(credential: DefaultAzureCredential) -> None:
    account_url = resolve_blob_account_url()
    if not account_url:
        raise SystemExit("Could not resolve OUTPUT_STORAGE_ACCOUNT from the azd environment.")

    with ContainerClient(
        account_url=account_url,
        container_name=POLICY_CONTAINER,
        credential=credential,
    ) as container:
        try:
            container.create_container()
        except ResourceExistsError:
            pass

        existing = next(iter(container.list_blob_names()), None)
        if existing:
            print("Policies already exist; skipping seed.")
            return

        policies = sorted((REPO_ROOT / "src" / "policies").glob("*.md"))
        for policy in policies:
            container.upload_blob(policy.name, policy.read_bytes())
        print(f"Seeded {len(policies)} expense policies.")


def send_samples(credential: DefaultAzureCredential) -> None:
    account_url = resolve_queue_account_url()
    if not account_url:
        raise SystemExit("Could not resolve OUTPUT_STORAGE_ACCOUNT from the azd environment.")

    for queue_name in OUTPUT_QUEUES:
        with QueueClient(
            account_url=account_url,
            queue_name=queue_name,
            credential=credential,
        ) as queue:
            if next(iter(queue.peek_messages(max_messages=1)), None):
                print("Output queues already contain decisions; skipping the sample send.")
                return

    with QueueClient(
        account_url=account_url,
        queue_name=INPUT_QUEUE,
        credential=credential,
    ) as queue:
        for sample_name in SAMPLES:
            queue.send_message((REPO_ROOT / "samples" / sample_name).read_text(encoding="utf-8"))

    print(
        "Sent 3 sample requests. Read the decisions with: "
        "uv run --project src --no-sync python scripts/read_decision.py --queue all --peek --cloud"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed-policies", "send-samples"))
    args = parser.parse_args()

    credential = DefaultAzureCredential(exclude_azure_cli_credential=True)
    try:
        if args.action == "seed-policies":
            seed_policies(credential)
        else:
            send_samples(credential)
    finally:
        credential.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
