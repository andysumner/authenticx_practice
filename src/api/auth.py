"""API-token authentication for the ingestion API (Phase 3).

Any caller (the connector, a backfill job, curl) must present a static Bearer
token matching ACX_API_TOKEN. This is the simplest defensible auth for a
service-to-service ingestion contract: one shared secret, sent over TLS in
production, compared in **constant time** so we never leak it through timing.

Production Authenticx-style APIs would issue per-tenant keys or OAuth; we note
that and keep a single token for the learning project. We **fail closed**: if no
token is configured, every request is rejected rather than accidentally open.

This mirrors the auth on my resume's Ignition webhook ingest -- a shared secret
the caller presents on every request, validated before any work is done.
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from src.common.config import settings
from src.common.log import get_logger, kv

log = get_logger("api.auth")


def require_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: enforce ``Authorization: Bearer <token>``.

    Raises 401 on a missing/malformed header or a token mismatch. We log the
    *outcome* only (auth_failed) -- never the presented or configured token.
    """
    configured = settings.api_token
    if not configured:
        # Misconfiguration -> fail closed, and make the reason greppable in logs.
        log.error(kv(action="auth_no_token_configured"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API token not configured on the server",
            headers={"WWW-Authenticate": "Bearer"},
        )

    presented = ""
    if authorization and authorization.startswith("Bearer "):
        presented = authorization[len("Bearer ") :].strip()

    # compare_digest avoids leaking how many leading chars matched via timing.
    if not presented or not hmac.compare_digest(presented, configured):
        log.warning(kv(action="auth_failed"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
