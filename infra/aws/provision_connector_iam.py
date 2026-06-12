"""Create the least-privilege IAM user the connector runs as (Phase 2).

Run after provision_sqs.py, with ADMIN creds:

    ./venv/bin/python infra/aws/provision_connector_iam.py

Why a separate user at all
--------------------------
The connector should be able to do *exactly* its job and nothing else: pull
messages off the queue and read recording objects. If its key ever leaked, the
blast radius is "read some audio + drain one queue" — not "do anything in the
account." That's least privilege, and it's the difference between the contractor's
master keyring (admin) and the tenant's single door key (this user).

The policy below grants only:
  - SQS: receive/delete/peek/extend-visibility on the ONE main queue ARN.
         (No CreateQueue, no access to other queues, not even the DLQ.)
  - S3:  GetObject on the audio + CTR prefixes only (not the whole bucket),
         plus ListBucket limited by prefix (needed for the backfill scan).

Idempotent-ish: the user + policy are safe to re-apply. Access KEYS are not
re-shown by AWS after creation, so if a key already exists the script leaves it
alone and tells you to reuse it or rotate.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from botocore.exceptions import ClientError  # noqa: E402

from src.common.config import (  # noqa: E402
    CONNECTOR_IAM_USER,
    CTR_PREFIX,
    RECORDINGS_PREFIX,
    SQS_QUEUE_NAME,
    make_client,
    settings,
)

POLICY_NAME = "acx-connector-least-privilege"


def main() -> None:
    iam = make_client("iam")
    sqs = make_client("sqs")
    sts = make_client("sts")

    account_id = sts.get_caller_identity()["Account"]
    bucket = settings.s3_bucket
    bucket_arn = f"arn:aws:s3:::{bucket}"

    queue_url = sqs.get_queue_url(QueueName=SQS_QUEUE_NAME)["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]

    # 1) Create the user (tolerate "already exists").
    try:
        iam.create_user(UserName=CONNECTOR_IAM_USER)
        print(f"Created IAM user: {CONNECTOR_IAM_USER}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            print(f"IAM user already exists: {CONNECTOR_IAM_USER}")
        else:
            raise

    # 2) Attach the least-privilege inline policy (put_* overwrites cleanly).
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ConsumeFromMainQueue",
                "Effect": "Allow",
                "Action": [
                    "sqs:ReceiveMessage",
                    "sqs:DeleteMessage",
                    "sqs:GetQueueAttributes",
                    "sqs:ChangeMessageVisibility",
                ],
                "Resource": queue_arn,
            },
            {
                "Sid": "ReadRecordingAndCtrObjects",
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": [
                    f"{bucket_arn}/{RECORDINGS_PREFIX}*",
                    f"{bucket_arn}/{CTR_PREFIX}*",
                ],
            },
            {
                "Sid": "ListOnlyOurPrefixes",
                "Effect": "Allow",
                "Action": "s3:ListBucket",
                "Resource": bucket_arn,
                "Condition": {
                    "StringLike": {
                        "s3:prefix": [f"{RECORDINGS_PREFIX}*", f"{CTR_PREFIX}*"]
                    }
                },
            },
        ],
    }
    iam.put_user_policy(
        UserName=CONNECTOR_IAM_USER,
        PolicyName=POLICY_NAME,
        PolicyDocument=json.dumps(policy),
    )
    print(f"Attached inline policy: {POLICY_NAME}")

    # 3) Access key — only if the user has none (AWS won't re-show secrets).
    existing_keys = iam.list_access_keys(UserName=CONNECTOR_IAM_USER)[
        "AccessKeyMetadata"
    ]
    if existing_keys:
        print(
            f"\nUser already has {len(existing_keys)} access key(s); not creating a new one.\n"
            "If you don't have the secret saved, delete the old key in the IAM console\n"
            "and re-run this script to mint a fresh one."
        )
        return

    key = iam.create_access_key(UserName=CONNECTOR_IAM_USER)["AccessKey"]
    print("\n--- Add these lines to .env (connector's RUNTIME creds) ---")
    print(f"CONNECTOR_AWS_ACCESS_KEY_ID={key['AccessKeyId']}")
    print(f"CONNECTOR_AWS_SECRET_ACCESS_KEY={key['SecretAccessKey']}")
    print(
        "\nThis secret is shown ONCE. Paste it now; AWS will never display it again."
    )


if __name__ == "__main__":
    main()
