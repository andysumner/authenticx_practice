"""The connector: consume S3 ObjectCreated events from SQS, record media (Phase 2).

Run it:
    ./venv/bin/python -m src.connector.consumer          # poll forever
    ./venv/bin/python -m src.connector.consumer --drain  # process what's there, then exit

Flow per message
----------------
1. Receive (long-poll) up to 10 messages from the main queue.
2. For each message:
     - Skip s3:TestEvent (S3's wiring-check ping).
     - For each S3 record: URL-decode the key, keep only audio under the
       recordings prefix, parse the contact_id out of the filename.
     - HeadObject (as the least-privilege connector user) to confirm the object
       and read its content-type; retried with backoff for transient blips.
     - DB: upsert the interaction anchor, then insert the media_record with an
       idempotency key. ON CONFLICT means a duplicate event is a harmless no-op.
     - Commit the DB write, THEN delete the SQS message (the ack).
3. If processing raised, we do NOT delete the message. SQS makes it visible again
   after the visibility timeout and retries it; after maxReceiveCount it lands in
   the DLQ. That's "never silently drop data" enforced by infrastructure.

We log IDs and status only — never audio bytes or transcript text.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.parse
import uuid

from botocore.exceptions import ClientError, EndpointConnectionError

from src.common.config import RECORDINGS_PREFIX, make_client, settings
from src.common.log import get_logger, kv
from src.common.retry import with_backoff
from src.connector import db

log = get_logger("connector")

# S3/network errors worth a quick local retry (vs. a permanent 404, which is not).
TRANSIENT_S3_CODES = {
    "RequestTimeout",
    "SlowDown",
    "InternalError",
    "ServiceUnavailable",
    "Throttling",
    "ThrottlingException",
}


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, EndpointConnectionError):
        return True
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        return code in TRANSIENT_S3_CODES
    return False


def parse_contact_id(key: str) -> str | None:
    """Connect names recordings <contactId>_<timestamp>_UTC.wav.

    The contact_id is the UUID before the first underscore in the filename.
    Returns None if the leading token isn't a UUID (defensive).
    """
    filename = key.rsplit("/", 1)[-1]
    candidate = filename.split("_", 1)[0]
    try:
        uuid.UUID(candidate)
        return candidate
    except ValueError:
        return None


def idempotency_key(bucket: str, key: str) -> str:
    """Deterministic per-object key: sha256(bucket/key).

    The same S3 object always hashes to the same value, so a duplicated SQS
    delivery collides on media_records.idempotency_key and is skipped. (The
    object's ETag would also work; bucket/key is simplest and needs no S3 call.)
    """
    return hashlib.sha256(f"{bucket}/{key}".encode()).hexdigest()


def extract_s3_records(body: dict) -> list[dict]:
    """Pull the S3 records out of a message body, skipping the test ping."""
    if body.get("Event") == "s3:TestEvent":
        log.info(kv(event="s3_test_event", action="skip"))
        return []
    return body.get("Records", [])


def ingest_object(s3, conn, bucket: str, key: str, *, size_hint: int | None = None) -> str:
    """Record one S3 audio object as a media_record. The SHARED core.

    Used by BOTH paths:
      - the event-driven connector (process_record decodes the event key first), and
      - the backfill scanner (which lists S3 and already has plain keys).
    Expects `key` to be the real, already-decoded S3 key.

    Returns a status string for callers that want to tally: 'ingested',
    'duplicate', or 'skipped'. Raises on transient failure so the event path can
    retry via SQS.
    """
    if not key.startswith(RECORDINGS_PREFIX):
        log.info(kv(action="skip_non_recording", key=key))
        return "skipped"

    contact_id = parse_contact_id(key)
    if contact_id is None:
        log.warning(kv(action="skip_unparseable_key", key=key))
        return "skipped"

    # HeadObject as the connector (exercises its s3:GetObject grant). Retry transient
    # errors; a 404 is permanent and propagates immediately to the DLQ path.
    head = with_backoff(
        lambda: s3.head_object(Bucket=bucket, Key=key),
        retry_on=(ClientError, EndpointConnectionError),
    )
    content_type = head.get("ContentType")
    size_bytes = head.get("ContentLength", size_hint)

    idem = idempotency_key(bucket, key)

    db.upsert_interaction_anchor(conn, contact_id)
    media_id = db.insert_media_record(
        conn,
        contact_id=contact_id,
        s3_bucket=bucket,
        s3_key=key,
        idempotency_key=idem,
        content_type=content_type,
        size_bytes=size_bytes,
    )
    conn.commit()

    if media_id is None:
        log.info(kv(action="duplicate_skip", contact_id=contact_id, key=key))
        return "duplicate"

    log.info(
        kv(
            action="media_recorded",
            media_id=media_id,
            contact_id=contact_id,
            size_bytes=size_bytes,
            status="queued",
        )
    )
    return "ingested"


def process_record(s3, conn, record: dict) -> None:
    """Unwrap one S3 ObjectCreated event record and hand it to ingest_object."""
    s3_info = record["s3"]
    bucket = s3_info["bucket"]["name"]
    # Keys in S3 EVENT JSON are URL-encoded (':' -> '%3A', ' ' -> '+'). Decode here,
    # in the event wrapper only — ListObjectsV2 (backfill) returns plain keys.
    key = urllib.parse.unquote_plus(s3_info["object"]["key"])
    size = s3_info["object"].get("size")
    ingest_object(s3, conn, bucket, key, size_hint=size)


def process_message(s3, conn, message: dict) -> bool:
    """Return True if the message is fully handled and safe to delete."""
    try:
        body = json.loads(message["Body"])
        for record in extract_s3_records(body):
            process_record(s3, conn, record)
        return True
    except Exception as exc:  # noqa: BLE001 - we decide retry vs. surface here
        conn.rollback()
        log.error(
            kv(
                action="message_failed",
                transient=is_transient(exc),
                error=type(exc).__name__,
            )
        )
        # Returning False leaves the message on the queue -> SQS retries -> DLQ.
        return False


def run(drain: bool = False) -> None:
    sqs = make_client("sqs", as_connector=True)
    s3 = make_client("s3", as_connector=True)
    conn = db.connect()
    log.info(kv(action="connector_start", queue=settings.sqs_queue_url, drain=drain))

    try:
        while True:
            resp = sqs.receive_message(
                QueueUrl=settings.sqs_queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,  # long poll
            )
            messages = resp.get("Messages", [])
            if not messages:
                if drain:
                    log.info(kv(action="queue_empty", drain="exit"))
                    return
                continue

            for message in messages:
                if process_message(s3, conn, message):
                    sqs.delete_message(
                        QueueUrl=settings.sqs_queue_url,
                        ReceiptHandle=message["ReceiptHandle"],
                    )
    finally:
        conn.close()


if __name__ == "__main__":
    run(drain="--drain" in sys.argv)
