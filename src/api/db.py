"""Database operations for the ingestion API (Phase 3).

The API is the **front door**. The Phase 2 connector wrote to Postgres directly
as a stand-in; here the API owns the two writes that record an ingested recording
and -- crucially -- it ENRICHES the interaction with the CTR-style metadata that
rode in on the upload. That enrichment is the cross-system mapping step: Amazon
Connect's contact-trace fields land in our internal schema.

Two writes per upload, in this order:

1. upsert_interaction(...)
     The interactions row is the "anchor" keyed by contact_id. Audio and CTR
     metadata arrive separately and out of order, so we upsert: insert if new,
     otherwise fill in columns we now know WITHOUT clobbering values already
     present (COALESCE(EXCLUDED.x, interactions.x)).

2. insert_media_record(...)
     The idempotency point. INSERT ... ON CONFLICT (idempotency_key) DO NOTHING
     RETURNING id. A returned id means a NEW recording (-> HTTP 201). No row means
     we've ingested this exact audio before (-> HTTP 200), so we look up and return
     the existing id. The database, not the app, guarantees no double-ingest.
"""
from __future__ import annotations

from datetime import datetime

import psycopg

from src.common.config import settings


def connect() -> psycopg.Connection:
    """Open a connection. The request dependency owns commit/rollback/close."""
    return psycopg.connect(settings.database_url)


def upsert_interaction(
    conn: psycopg.Connection,
    *,
    contact_id: str,
    channel: str | None,
    initiation_method: str | None,
    queue_name: str | None,
    agent_username: str | None,
    started_at: datetime | None,
    ended_at: datetime | None,
    duration_seconds: int | None,
    disconnect_reason: str | None,
) -> None:
    """Insert or enrich the interactions anchor for this contact.

    COALESCE(EXCLUDED.col, interactions.col) means: prefer the freshly supplied
    value, but never overwrite an existing value with NULL. Metadata can arrive
    in pieces (audio first, CTR later, or a partial upload) -- we fill blanks, we
    don't erase. metadata_received_at is stamped every time, as an audit marker
    of when the API last learned something about this contact.
    """
    conn.execute(
        """
        INSERT INTO interactions
            (contact_id, channel, initiation_method, queue_name, agent_username,
             started_at, ended_at, duration_seconds, disconnect_reason,
             metadata_received_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (contact_id) DO UPDATE SET
            channel              = COALESCE(EXCLUDED.channel, interactions.channel),
            initiation_method    = COALESCE(EXCLUDED.initiation_method, interactions.initiation_method),
            queue_name           = COALESCE(EXCLUDED.queue_name, interactions.queue_name),
            agent_username       = COALESCE(EXCLUDED.agent_username, interactions.agent_username),
            started_at           = COALESCE(EXCLUDED.started_at, interactions.started_at),
            ended_at             = COALESCE(EXCLUDED.ended_at, interactions.ended_at),
            duration_seconds     = COALESCE(EXCLUDED.duration_seconds, interactions.duration_seconds),
            disconnect_reason    = COALESCE(EXCLUDED.disconnect_reason, interactions.disconnect_reason),
            metadata_received_at = now()
        """,
        (
            contact_id, channel, initiation_method, queue_name, agent_username,
            started_at, ended_at, duration_seconds, disconnect_reason,
        ),
    )


def insert_media_record(
    conn: psycopg.Connection,
    *,
    contact_id: str,
    s3_bucket: str,
    s3_key: str,
    idempotency_key: str,
    content_type: str | None,
    size_bytes: int | None,
    duration_seconds: int | None,
) -> tuple[int, bool]:
    """Insert a media_record at status 'ingested'.

    Returns (media_record_id, created):
      created=True  -> a brand-new row (caller returns HTTP 201).
      created=False -> idempotency_key already existed; we return the existing
                       row's id (caller returns HTTP 200). No second row is ever
                       written -- the UNIQUE constraint makes that impossible.
    """
    row = conn.execute(
        """
        INSERT INTO media_records
            (contact_id, s3_bucket, s3_key, idempotency_key,
             content_type, size_bytes, duration_seconds, status, received_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'ingested', now())
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
        """,
        (
            contact_id, s3_bucket, s3_key, idempotency_key,
            content_type, size_bytes, duration_seconds,
        ),
    ).fetchone()
    if row is not None:
        return row[0], True

    existing = conn.execute(
        "SELECT id FROM media_records WHERE idempotency_key = %s",
        (idempotency_key,),
    ).fetchone()
    return existing[0], False


def get_media_record(conn: psycopg.Connection, media_id: int) -> dict | None:
    """Fetch one media_record by id, or None. Used by GET /v1/recordings/{id}."""
    row = conn.execute(
        """
        SELECT id, contact_id, status, content_type, size_bytes,
               duration_seconds, received_at
        FROM media_records
        WHERE id = %s
        """,
        (media_id,),
    ).fetchone()
    if row is None:
        return None
    keys = (
        "id", "contact_id", "status", "content_type",
        "size_bytes", "duration_seconds", "received_at",
    )
    return dict(zip(keys, row))
