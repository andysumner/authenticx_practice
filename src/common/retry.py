"""Exponential-backoff retry for transient failures.

Two layers of retry exist in this system, and they're different on purpose:

  - THIS helper retries a single in-process call (e.g. one S3 HeadObject) a few
    times with growing delays, to ride out a brief network blip or throttle.
  - SQS itself retries the whole MESSAGE: if the connector fails to process a
    message, we don't delete it, so after the visibility timeout SQS redelivers
    it — and after maxReceiveCount failures it goes to the DLQ.

So this helper handles the small, fast, transient stuff; SQS handles durable,
cross-restart retry and the dead-letter safety net.
"""
from __future__ import annotations

import time
from typing import Callable, Tuple, Type, TypeVar

T = TypeVar("T")


def with_backoff(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 0.5,
    retry_on: Tuple[Type[BaseException], ...] = (Exception,),
) -> T:
    """Call fn(); on a retryable exception, wait base_delay * 2**n and try again.

    Raises the last exception if all attempts fail. Delays: 0.5s, 1s, 2s, ...
    Only exceptions in `retry_on` are retried; anything else propagates at once
    (e.g. a 404 is permanent — retrying it just wastes time).
    """
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except retry_on as exc:  # noqa: PERF203
            last_exc = exc
            if attempt == attempts - 1:
                break
            time.sleep(base_delay * (2 ** attempt))
    assert last_exc is not None
    raise last_exc
