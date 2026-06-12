"""Backfill scanner: ingest recordings that already exist in S3 (Phase 2 capstone).

Run it:
    ./venv/bin/python -m src.connector.backfill

Why this exists
---------------
S3 event notifications are FORWARD-ONLY: they only fire for objects created after
the notification was wired up. Recordings that landed before that (or a brand-new
customer's whole history of recordings) never produce an event. The only way to
ingest them is to LIST what's already in the bucket and pull each one in.

This is the customer-onboarding motion: "event-driven for new conversations, a
one-time backfill for the history that predates us." Reconciliation (a periodic
re-scan to catch any rarely-missed event) is the same scan run on a schedule.

How it stays correct
--------------------
It calls the connector's SHARED ingest_object() — the exact same parse, HeadObject,
idempotency, and DB write the live path uses. No second copy of the logic to drift.
Because that core ends in an ON CONFLICT insert, the backfill is idempotent: objects
already ingested by the live path come back as 'duplicate' no-ops, so it is safe to
run repeatedly and safe to run while live events are flowing.

It runs as the least-privilege connector user, using the s3:ListBucket grant
(prefix-scoped) we provisioned for exactly this.
"""
from __future__ import annotations

from src.common.config import RECORDINGS_PREFIX, make_client, settings
from src.common.log import get_logger, kv
from src.connector import db
from src.connector.consumer import ingest_object

log = get_logger("backfill")


def run() -> None:
    s3 = make_client("s3", as_connector=True)
    conn = db.connect()
    bucket = settings.s3_bucket
    log.info(kv(action="backfill_start", bucket=bucket, prefix=RECORDINGS_PREFIX))

    tally = {"ingested": 0, "duplicate": 0, "skipped": 0}
    try:
        # Paginator transparently follows S3's continuation tokens, so this works
        # for 6 objects or 600,000 without us managing pages by hand.
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=RECORDINGS_PREFIX):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue  # "folder" placeholder object, not a recording
                # ListObjectsV2 keys are already plain (not URL-encoded).
                status = ingest_object(s3, conn, bucket, key, size_hint=obj["Size"])
                tally[status] += 1
    finally:
        conn.close()

    log.info(
        kv(
            action="backfill_done",
            ingested=tally["ingested"],
            duplicate=tally["duplicate"],
            skipped=tally["skipped"],
        )
    )


if __name__ == "__main__":
    run()
