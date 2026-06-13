"""Tests for the ingestion API (Phase 3).

These run against the local Postgres (``docker compose up``). Each test uses a
random contact_id and cleans up after itself, so the suite is repeatable and
leaves no residue. If the DB or the API token isn't available, the suite SKIPS
rather than fails -- so it's safe to run anywhere.

What we prove:
  - health endpoint round-trips to the DB
  - auth: missing / wrong token -> 401
  - happy path -> 201, and the SAME bytes again -> 200 with the SAME id (idempotent)
  - metadata is matched onto the interaction row
  - validation: bad contact_id -> 422, wrong Content-Type -> 415, non-WAV -> 400
  - GET returns the recorded media record
"""
from __future__ import annotations

import io
import uuid
import wave

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from src.api.main import app  # noqa: E402
from src.common.config import settings  # noqa: E402

TOKEN = settings.api_token


def _db_ok() -> bool:
    if not settings.database_url:
        return False
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_ok() or not TOKEN,
    reason="needs local Postgres (docker compose up) and ACX_API_TOKEN set",
)


def make_wav(seconds: int = 1, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * rate * seconds)
    return buf.getvalue()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def contact_id():
    cid = str(uuid.uuid4())
    yield cid
    # Tear down every row this contact could have created (children first).
    with psycopg.connect(settings.database_url) as c:
        c.execute("DELETE FROM signals WHERE contact_id = %s", (cid,))
        c.execute("DELETE FROM transcripts WHERE contact_id = %s", (cid,))
        c.execute("DELETE FROM media_records WHERE contact_id = %s", (cid,))
        c.execute("DELETE FROM interactions WHERE contact_id = %s", (cid,))
        c.commit()


def _auth(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def post(client, contact_id, *, audio=None, token=TOKEN, content_type="audio/wav", **meta):
    audio = make_wav() if audio is None else audio
    data = {"contact_id": contact_id, **{k: str(v) for k, v in meta.items()}}
    return client.post(
        "/v1/recordings",
        headers=_auth(token),
        files={"file": ("rec.wav", audio, content_type)},
        data=data,
    )


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_missing_token_401(client, contact_id):
    r = client.post(
        "/v1/recordings",
        files={"file": ("rec.wav", make_wav(), "audio/wav")},
        data={"contact_id": contact_id},
    )
    assert r.status_code == 401


def test_wrong_token_401(client, contact_id):
    r = post(client, contact_id, token="not-the-token")
    assert r.status_code == 401


def test_ingest_then_duplicate_is_idempotent(client, contact_id):
    audio = make_wav()
    r1 = post(client, contact_id, audio=audio, queue_name="Support", channel="VOICE")
    assert r1.status_code == 201, r1.text
    body = r1.json()
    assert body["status"] == "created"
    media_id = body["media_record_id"]

    # Same bytes again -> idempotent 200, pointing at the SAME row.
    r2 = post(client, contact_id, audio=audio)
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"
    assert r2.json()["media_record_id"] == media_id

    # Exactly one media_record exists, and the metadata landed on the interaction.
    with psycopg.connect(settings.database_url) as c:
        count = c.execute(
            "SELECT count(*) FROM media_records WHERE contact_id = %s", (contact_id,)
        ).fetchone()[0]
        meta = c.execute(
            "SELECT queue_name, channel FROM interactions WHERE contact_id = %s",
            (contact_id,),
        ).fetchone()
    assert count == 1
    assert meta == ("Support", "VOICE")


def test_metadata_enrichment_does_not_clobber(client, contact_id):
    # First upload sets the queue; second upload (different bytes) omits it.
    a1 = make_wav(seconds=1)
    a2 = make_wav(seconds=2)
    assert post(client, contact_id, audio=a1, queue_name="Billing").status_code == 201
    assert post(client, contact_id, audio=a2, channel="VOICE").status_code == 201

    with psycopg.connect(settings.database_url) as c:
        row = c.execute(
            "SELECT queue_name, channel FROM interactions WHERE contact_id = %s",
            (contact_id,),
        ).fetchone()
    # queue_name survived (not erased by the second upload's NULL); channel filled in.
    assert row == ("Billing", "VOICE")


def test_bad_contact_id_422(client):
    r = post(client, "not-a-uuid")
    assert r.status_code == 422


def test_unsupported_content_type_415(client, contact_id):
    r = post(client, contact_id, content_type="application/pdf")
    assert r.status_code == 415


def test_not_a_wav_400(client, contact_id):
    r = post(client, contact_id, audio=b"this is plainly not audio", content_type="audio/wav")
    assert r.status_code == 400


def test_get_recording(client, contact_id):
    media_id = post(client, contact_id).json()["media_record_id"]
    g = client.get(f"/v1/recordings/{media_id}", headers=_auth())
    assert g.status_code == 200
    assert g.json()["status"] == "ingested"
    assert g.json()["contact_id"] == contact_id


def test_get_recording_404(client):
    g = client.get("/v1/recordings/999999999", headers=_auth())
    assert g.status_code == 404
