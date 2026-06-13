"""Mock ingestion API -- the "AcxAPI" clone (Phase 3).

This is the RECEIVING side of the ingestion contract. A client (the connector, a
backfill job, or curl) uploads a call recording; the API authenticates the
caller, validates the audio, records it idempotently, and matches its CTR-style
metadata onto the interaction anchor.

Endpoints
---------
GET  /healthz               liveness + DB reachability (no auth -- monitors hit it)
POST /v1/recordings         the upload: multipart audio + metadata (auth required)
GET  /v1/recordings/{id}    look up a recorded media record (auth required)

Run it:
    ./venv/bin/uvicorn src.api.main:app --reload --port 8000
    # interactive docs at http://localhost:8000/docs

Status codes are deliberate (the role screens for "clear response/error codes"):
    201 created       a new recording was ingested
    200 ok            we'd seen this exact audio before (idempotent no-op)
    401 unauthorized  bad/missing API token
    415 unsupported   declared Content-Type isn't a WAV type
    400 bad request   bytes aren't a readable PCM WAV
    422 unprocessable required field missing / bad UUID / bad timestamp
    404 not found     GET for an id that doesn't exist

We log IDs and status only -- never audio bytes.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

import psycopg
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)

from src.api import db
from src.api.audio import ALLOWED_CONTENT_TYPES, InvalidAudio, validate_wav
from src.api.auth import require_token
from src.api.schemas import IngestResponse, MediaRecordView
from src.common.log import get_logger, kv

log = get_logger("api")

app = FastAPI(title="AcxAPI (mock ingestion API)", version="0.3.0")


def get_conn():
    """Per-request DB connection (FastAPI dependency).

    A connection *pool* would be the production choice; one connection per request
    is plenty for this learning project and easy to reason about. We roll back on
    any error so a failed request can't leave a half-written transaction, and we
    always close. The handler commits explicitly on the happy path.
    """
    conn = db.connect()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _parse_dt(value: str | None, field: str) -> datetime | None:
    """Parse an optional ISO-8601 form field, or raise 422 with a clear message."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} must be ISO-8601 (e.g. 2026-06-12T15:04:05+00:00)",
        )


@app.get("/healthz")
def healthz(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """Liveness + DB round-trip. No auth: load balancers and monitors call this."""
    conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post(
    "/v1/recordings",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_token)],
)
async def ingest_recording(
    response: Response,
    conn: psycopg.Connection = Depends(get_conn),
    file: UploadFile = File(..., description="PCM WAV audio"),
    contact_id: str = Form(..., description="Amazon Connect ContactId (UUID)"),
    channel: str | None = Form(None, description="VOICE | CHAT | TASK"),
    initiation_method: str | None = Form(None),
    queue_name: str | None = Form(None),
    agent_username: str | None = Form(None),
    started_at: str | None = Form(None, description="ISO-8601"),
    ended_at: str | None = Form(None, description="ISO-8601"),
    duration_seconds: int | None = Form(None),
    disconnect_reason: str | None = Form(None),
    s3_bucket: str | None = Form(None, description="provenance, if from S3"),
    s3_key: str | None = Form(None, description="provenance, if from S3"),
) -> IngestResponse:
    # 1) contact_id must look like a Connect ContactId (a UUID) -> 422.
    try:
        uuid.UUID(contact_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="contact_id must be a UUID",
        )

    # 2) The *declared* Content-Type must be a WAV type -> 415.
    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"unsupported Content-Type {file.content_type!r}; expected WAV",
        )

    # 3) The actual BYTES must be a readable PCM WAV -> 400.
    data = await file.read()
    try:
        info = validate_wav(data)
    except InvalidAudio as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    started = _parse_dt(started_at, "started_at")
    ended = _parse_dt(ended_at, "ended_at")

    # Content-addressable idempotency: the same audio bytes ARE the same recording,
    # no matter who uploads them or how many times. (The Phase 2 connector keys on
    # sha256(bucket/key); both schemes feed the same UNIQUE idempotency_key column.)
    idem = hashlib.sha256(data).hexdigest()
    size_bytes = len(data)

    # The schema requires s3_bucket/s3_key (provenance). The connector supplies the
    # real Connect location; a direct uploader may not, so we derive a stable
    # placeholder from the content hash. We do NOT persist the audio bytes here:
    # they already live in S3 from Connect, and in production this API would stream
    # them to encrypted-at-rest object storage rather than duplicate them.
    bucket = s3_bucket or "uploads"
    key = s3_key or f"uploads/{idem}.wav"

    db.upsert_interaction(
        conn,
        contact_id=contact_id,
        channel=channel,
        initiation_method=initiation_method,
        queue_name=queue_name,
        agent_username=agent_username,
        started_at=started,
        ended_at=ended,
        duration_seconds=duration_seconds,
        disconnect_reason=disconnect_reason,
    )
    media_id, created = db.insert_media_record(
        conn,
        contact_id=contact_id,
        s3_bucket=bucket,
        s3_key=key,
        idempotency_key=idem,
        content_type=file.content_type or "audio/wav",
        size_bytes=size_bytes,
        duration_seconds=info["duration_seconds"],
    )
    conn.commit()

    if created:
        log.info(
            kv(
                action="ingested",
                media_id=media_id,
                contact_id=contact_id,
                size_bytes=size_bytes,
                status="ingested",
            )
        )
        response.status_code = status.HTTP_201_CREATED
        return IngestResponse(
            status="created",
            media_record_id=media_id,
            contact_id=contact_id,
            idempotency_key=idem,
            duration_seconds=info["duration_seconds"],
            message="recording ingested",
        )

    log.info(kv(action="duplicate", media_id=media_id, contact_id=contact_id))
    response.status_code = status.HTTP_200_OK
    return IngestResponse(
        status="duplicate",
        media_record_id=media_id,
        contact_id=contact_id,
        idempotency_key=idem,
        duration_seconds=info["duration_seconds"],
        message="recording already ingested",
    )


@app.get(
    "/v1/recordings/{media_id}",
    response_model=MediaRecordView,
    dependencies=[Depends(require_token)],
)
def get_recording(
    media_id: int, conn: psycopg.Connection = Depends(get_conn)
) -> MediaRecordView:
    record = db.get_media_record(conn, media_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="media record not found"
        )
    return MediaRecordView(**record)
