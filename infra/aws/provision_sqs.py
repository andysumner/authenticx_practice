"""Provision the SQS main queue + dead-letter queue (Phase 2).

Run with the project venv and ADMIN credentials in .env:

    ./venv/bin/python infra/aws/provision_sqs.py

What it creates
---------------
1. A dead-letter queue (DLQ): `acx-recordings-dlq`.
2. The main queue: `acx-recordings`, wired to the DLQ via a *redrive policy*.

Key SQS concepts (interview material)
-------------------------------------
- Standard queue, NOT FIFO. Standard queues are *at-least-once* delivery: a
  message can occasionally be delivered more than once. That is exactly why we
  enforce idempotency downstream (the media_records.idempotency_key UNIQUE
  constraint). FIFO would add cost/complexity and S3→SQS is simplest on standard.
- Visibility timeout: when the connector receives a message, SQS *hides* it for
  N seconds instead of deleting it. If the connector finishes and deletes it,
  it's gone; if the connector crashes, the message reappears after N seconds and
  is retried. Set N comfortably above worst-case processing time.
- Redrive policy + maxReceiveCount: after a message has been received (and NOT
  deleted) this many times, SQS moves it to the DLQ instead of looping forever.
  That's how poison messages stop blocking the queue without being silently lost.
- Long polling (ReceiveMessageWaitTimeSeconds=20): the connector's receive call
  waits up to 20s for a message instead of returning instantly empty. Fewer empty
  receives = lower cost and less busy-looping.

The script is idempotent: re-running it returns the existing queues unchanged.
"""
from __future__ import annotations

import json
import pathlib
import sys

# Make `from src.common...` work regardless of where this is run from.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src.common.config import SQS_DLQ_NAME, SQS_QUEUE_NAME, make_client  # noqa: E402

# How many delivery attempts before a message is parked in the DLQ.
MAX_RECEIVE_COUNT = 5
# Hide a received message this long while the connector processes it (seconds).
VISIBILITY_TIMEOUT = 60
# Long-poll wait so receive() isn't a busy spin (seconds, max 20).
RECEIVE_WAIT = 20
# Keep messages up to this long if nothing consumes them (seconds; 14 days max).
RETENTION = 14 * 24 * 3600


def _queue_arn(sqs, queue_url: str) -> str:
    resp = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])
    return resp["Attributes"]["QueueArn"]


def main() -> None:
    sqs = make_client("sqs")  # admin creds

    # 1) DLQ first — we need its ARN before the main queue can point at it.
    dlq = sqs.create_queue(
        QueueName=SQS_DLQ_NAME,
        Attributes={"MessageRetentionPeriod": str(RETENTION)},
    )
    dlq_url = dlq["QueueUrl"]
    dlq_arn = _queue_arn(sqs, dlq_url)
    print(f"DLQ ready: {dlq_url}")

    # 2) Main queue, wired to the DLQ via the redrive policy.
    redrive_policy = json.dumps(
        {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": MAX_RECEIVE_COUNT}
    )
    main_q = sqs.create_queue(
        QueueName=SQS_QUEUE_NAME,
        Attributes={
            "VisibilityTimeout": str(VISIBILITY_TIMEOUT),
            "ReceiveMessageWaitTimeSeconds": str(RECEIVE_WAIT),
            "MessageRetentionPeriod": str(RETENTION),
            "RedrivePolicy": redrive_policy,
        },
    )
    main_url = main_q["QueueUrl"]
    main_arn = _queue_arn(sqs, main_url)
    print(f"Main queue ready: {main_url}")

    print("\n--- Add/update these lines in .env ---")
    print(f"SQS_QUEUE_URL={main_url}")
    print(f"SQS_DLQ_URL={dlq_url}")
    print("\n(For reference — used by later scripts, no need to store:)")
    print(f"  main queue ARN: {main_arn}")
    print(f"  DLQ ARN:        {dlq_arn}")


if __name__ == "__main__":
    main()
