"""Database operations for the connector.

Two writes, in this order, per recording:

1. upsert_interaction_anchor(contact_id)
     media_records.contact_id is a NOT NULL foreign key to interactions(contact_id).
     Audio and CTR metadata arrive separately and out of order, so when AUDIO
     arrives first we create a bare "anchor" interaction (just the contact_id).
     Whoever provides the real metadata later (Phase 3) enriches the same row.
     ON CONFLICT DO NOTHING makes this safe to call every time.

2. insert_media_record(...)
     The idempotency point. INSERT ... ON CONFLICT (idempotency_key) DO NOTHING
     RETURNING id. If a row comes back, this was a NEW recording. If nothing comes
     back, we've seen this object before (SQS at-least-once duplicate, or a re-run)
     and we simply skip — the database, not the app, guarantees no double-ingest.
"""
from __future__ import annotations

import psycopg

from src.common.config import settings


def connect() -> psycopg.Connection:
    """Open a connection. Caller manages the transaction (commit/rollback)."""
    return psycopg.connect(settings.database_url)


def upsert_interaction_anchor(conn: psycopg.Connection, contact_id: str) -> None:
    """Ensure an interactions row exists for this contact (anchor only)."""
    conn.execute(
        """
        INSERT INTO interactions (contact_id)
        VALUES (%s)
        ON CONFLICT (contact_id) DO NOTHING
        """,
        (contact_id,),
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
) -> int | None:
    """Insert a media_record at status 'queued'.

    Returns the new row id if inserted, or None if it already existed
    (idempotent skip). 'queued' means: the connector has accepted this object
    and it is queued for the ingestion API (Phase 3).
    """
    row = conn.execute(
        """
        INSERT INTO media_records
            (contact_id, s3_bucket, s3_key, idempotency_key,
             content_type, size_bytes, status, received_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'queued', now())
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
        """,
        (contact_id, s3_bucket, s3_key, idempotency_key, content_type, size_bytes),
    ).fetchone()
    return row[0] if row else None
