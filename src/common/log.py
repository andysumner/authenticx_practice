"""Structured logging helper.

Non-negotiable rule: log IDs and status, never sensitive payloads (no call text,
no PII). These helpers emit compact key=value lines so logs are greppable and
machine-parseable, and they make it natural to log *about* a recording without
logging its *contents*.
"""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        _CONFIGURED = True
    return logging.getLogger(name)


def kv(**fields) -> str:
    """Render fields as `key=value key=value` for structured log lines.

    Pass only IDs/status/counts here — never transcript text or PII.
    """
    return " ".join(f"{k}={v}" for k, v in fields.items())
