"""TEST UTILITY: upload a synthetic recording to fire a real S3 ObjectCreated event.

Phase 1's DID phone number was released, so no live calls are arriving. To exercise
the event-driven path end-to-end we drop a SYNTHETIC object under the recordings
prefix; S3 fires ObjectCreated -> SQS exactly as a real call would.

    ./venv/bin/python infra/aws/upload_test_recording.py

Synthetic-data rule: this is generated silence with a random fake contact_id.
Never upload real audio.
"""
from __future__ import annotations

import datetime as dt
import io
import pathlib
import sys
import uuid
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src.common.config import RECORDINGS_PREFIX, make_client, settings  # noqa: E402


def make_silent_wav(seconds: float = 0.5, rate: int = 8000) -> bytes:
    """Return bytes of a valid mono 16-bit WAV containing silence."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def main() -> None:
    contact_id = str(uuid.uuid4())
    ts = dt.datetime.utcnow().strftime("%Y%m%dT%H:%M")
    key = f"{RECORDINGS_PREFIX}{dt.datetime.utcnow():%Y/%m/%d}/{contact_id}_{ts}_UTC.wav"

    s3 = make_client("s3")  # admin creds (the connector deliberately can't PutObject)
    s3.put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=make_silent_wav(),
        ContentType="audio/wav",
    )
    print(f"Uploaded synthetic recording:\n  contact_id={contact_id}\n  key={key}")


if __name__ == "__main__":
    main()
