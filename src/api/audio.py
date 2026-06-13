"""Audio format/codec validation for the ingestion API (Phase 3).

Why validate at the door? An ingestion API should reject garbage *early* with a
clear error, rather than let a bad file rot in the pipeline and blow up later at
transcription. Amazon Connect records calls as uncompressed PCM WAV, so that's
what we accept.

We do TWO checks on purpose, each mapping to a different HTTP response upstream:
  - the multipart Content-Type header  (cheap, client-*declared*)  -> 415 if wrong
  - the actual bytes (RIFF/WAVE magic + a parseable PCM header)      -> 400 if bad
A header can lie or be omitted, which is exactly why we don't trust it and also
parse the bytes. (Analogy: the Content-Type is the label on the envelope; the
byte check is opening it to confirm what's actually inside.)
"""
from __future__ import annotations

import io
import wave

# Content-Type values a well-behaved client might send for a WAV upload.
ALLOWED_CONTENT_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/vnd.wave",
}


class InvalidAudio(Exception):
    """Raised when the bytes are not a readable PCM WAV (-> HTTP 400)."""


def validate_wav(data: bytes) -> dict:
    """Confirm `data` is a PCM WAV and return basic facts about it.

    Returns {channels, sample_rate, frames, sample_width, duration_seconds}.
    Raises InvalidAudio if the bytes aren't a parseable PCM WAV. The stdlib
    ``wave`` module only understands uncompressed PCM, so a compressed or foreign
    codec naturally raises here -- that IS our codec check, no extra library.
    """
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise InvalidAudio("missing RIFF/WAVE header")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels = w.getnchannels()
            sample_rate = w.getframerate()
            frames = w.getnframes()
            sample_width = w.getsampwidth()
    except (wave.Error, EOFError) as exc:
        raise InvalidAudio(f"unreadable WAV: {exc}") from exc
    if sample_rate <= 0 or channels <= 0:
        raise InvalidAudio("invalid WAV parameters")
    duration_seconds = round(frames / sample_rate) if sample_rate else None
    return {
        "channels": channels,
        "sample_rate": sample_rate,
        "frames": frames,
        "sample_width": sample_width,
        "duration_seconds": duration_seconds,
    }
