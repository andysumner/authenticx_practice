"""Response shapes for the ingestion API (Phase 3).

The *upload* itself is multipart form-data (so the audio file rides alongside its
metadata), so there's no request model here -- FastAPI's Form/File params handle
that. These Pydantic models define the JSON the API sends *back*, which also
becomes the documented contract rendered at /docs.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class IngestResponse(BaseModel):
    """Returned by POST /v1/recordings."""

    status: str  # "created" (HTTP 201) | "duplicate" (HTTP 200)
    media_record_id: int
    contact_id: str
    idempotency_key: str
    duration_seconds: int | None = None
    message: str


class MediaRecordView(BaseModel):
    """Returned by GET /v1/recordings/{id}."""

    id: int
    contact_id: str
    status: str
    content_type: str | None = None
    size_bytes: int | None = None
    duration_seconds: int | None = None
    received_at: datetime | None = None
