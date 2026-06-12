"""Wire S3 ObjectCreated -> SQS for the recordings prefix (Phase 2).

Run after provision_sqs.py, with ADMIN creds:

    ./venv/bin/python infra/aws/provision_s3_notification.py

Two steps, and the ORDER matters:

1) Queue access policy. By default nothing but the account owner may send to the
   queue. S3 is a *different* AWS service principal, so we attach a resource
   policy to the queue that says: "s3.amazonaws.com may sqs:SendMessage to me,
   but ONLY when the event comes from this specific bucket in this account."
   The aws:SourceArn / aws:SourceAccount conditions stop a stranger's bucket from
   being pointed at our queue (the "confused deputy" problem).

2) Bucket notification. We tell the bucket: on s3:ObjectCreated:* under the
   CallRecordings/ prefix, send an event to this queue. We deliberately filter to
   the audio prefix so CTR metadata objects (a different prefix) do NOT trigger
   the connector — audio drives the pipeline; metadata is matched on demand.

Gotcha handled here: PutBucketNotificationConfiguration *replaces* the whole
notification config. We read the existing config and merge our queue config in,
so we never clobber anything Connect may already rely on.

Idempotent: re-running re-asserts the same policy + notification.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src.common.config import (  # noqa: E402
    RECORDINGS_PREFIX,
    SQS_QUEUE_NAME,
    make_client,
    settings,
)

# A stable id so re-runs update OUR config entry instead of appending duplicates.
NOTIFICATION_ID = "acx-connector-recordings"


def main() -> None:
    sqs = make_client("sqs")
    s3 = make_client("s3")
    sts = make_client("sts")

    account_id = sts.get_caller_identity()["Account"]
    bucket = settings.s3_bucket
    bucket_arn = f"arn:aws:s3:::{bucket}"

    # Resolve the queue by name (robust to .env not being updated yet).
    queue_url = sqs.get_queue_url(QueueName=SQS_QUEUE_NAME)["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]

    # --- Step 1: queue resource policy allowing S3 (this bucket only) to send ---
    queue_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowS3ObjectCreatedToSend",
                "Effect": "Allow",
                "Principal": {"Service": "s3.amazonaws.com"},
                "Action": "sqs:SendMessage",
                "Resource": queue_arn,
                "Condition": {
                    "ArnLike": {"aws:SourceArn": bucket_arn},
                    "StringEquals": {"aws:SourceAccount": account_id},
                },
            }
        ],
    }
    sqs.set_queue_attributes(
        QueueUrl=queue_url, Attributes={"Policy": json.dumps(queue_policy)}
    )
    print(f"Queue policy set: S3 bucket {bucket} may SendMessage to {SQS_QUEUE_NAME}")

    # --- Step 2: bucket notification, merged into any existing config ---
    existing = s3.get_bucket_notification_configuration(Bucket=bucket)
    # Drop boto3 response metadata; keep real config keys only.
    existing.pop("ResponseMetadata", None)

    queue_configs = [
        qc
        for qc in existing.get("QueueConfigurations", [])
        if qc.get("Id") != NOTIFICATION_ID  # replace our own entry if present
    ]
    queue_configs.append(
        {
            "Id": NOTIFICATION_ID,
            "QueueArn": queue_arn,
            "Events": ["s3:ObjectCreated:*"],
            "Filter": {
                "Key": {
                    "FilterRules": [{"Name": "prefix", "Value": RECORDINGS_PREFIX}]
                }
            },
        }
    )
    existing["QueueConfigurations"] = queue_configs

    s3.put_bucket_notification_configuration(
        Bucket=bucket, NotificationConfiguration=existing
    )
    print(
        f"Bucket notification set: s3:ObjectCreated:* under "
        f"'{RECORDINGS_PREFIX}' -> {SQS_QUEUE_NAME}"
    )


if __name__ == "__main__":
    main()
