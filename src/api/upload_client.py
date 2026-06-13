"""Tiny client that uploads a recording to the ingestion API (Phase 3).

This is the *pushing* side of the contract, exercised by hand. It mirrors what a
productionized connector would do: read audio, attach CTR-style metadata, POST it
with the API token over HTTP, and act on the status code. (Wiring the real S3
connector to call this endpoint instead of writing Postgres directly is the
natural Phase 3 follow-up.)

Usage:
    # upload a real WAV file with some metadata
    ./venv/bin/python -m src.api.upload_client path/to/audio.wav \\
        --contact-id <uuid> --queue-name Support --channel VOICE

    # generate a throwaway SILENT synthetic WAV to smoke-test the endpoint
    ./venv/bin/python -m src.api.upload_client --synthetic
"""
from __future__ import annotations

import argparse
import io
import sys
import uuid
import wave

import httpx

from src.common.config import settings
from src.common.log import get_logger, kv

log = get_logger("api.client")

DEFAULT_BASE_URL = "http://localhost:8000"


def make_synthetic_wav(seconds: int = 1, sample_rate: int = 8000) -> bytes:
    """A short SILENT PCM WAV. Synthetic data only -- never a real recording."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * sample_rate * seconds)
    return buf.getvalue()


def upload(
    base_url: str, token: str, *, audio: bytes, contact_id: str, **meta
) -> httpx.Response:
    """POST one recording. Metadata kwargs with None values are dropped."""
    files = {"file": ("recording.wav", audio, "audio/wav")}
    data = {"contact_id": contact_id}
    for key, value in meta.items():
        if value is not None:
            data[key] = str(value)
    return httpx.post(
        f"{base_url}/v1/recordings",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
        data=data,
        timeout=30.0,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Upload a recording to the ingestion API")
    p.add_argument("path", nargs="?", help="path to a .wav file")
    p.add_argument("--synthetic", action="store_true", help="generate a silent test WAV")
    p.add_argument("--contact-id", default=str(uuid.uuid4()))
    p.add_argument("--channel")
    p.add_argument("--initiation-method")
    p.add_argument("--queue-name")
    p.add_argument("--agent-username")
    p.add_argument("--duration-seconds", type=int)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = p.parse_args(argv)

    if args.synthetic:
        audio = make_synthetic_wav()
    elif args.path:
        with open(args.path, "rb") as f:
            audio = f.read()
    else:
        p.error("provide a wav PATH or --synthetic")
        return 2  # unreachable; argparse exits

    if not settings.api_token:
        log.error(kv(action="no_token", hint="set ACX_API_TOKEN in .env"))
        return 2

    resp = upload(
        args.base_url,
        settings.api_token,
        audio=audio,
        contact_id=args.contact_id,
        channel=args.channel,
        initiation_method=args.initiation_method,
        queue_name=args.queue_name,
        agent_username=args.agent_username,
        duration_seconds=args.duration_seconds,
    )
    log.info(kv(action="upload_response", http_status=resp.status_code))
    print(resp.status_code, resp.text)
    return 0 if resp.status_code in (200, 201) else 1


if __name__ == "__main__":
    sys.exit(main())
