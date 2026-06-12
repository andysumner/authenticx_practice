"""Central configuration for authenticx_practice.

One place that reads .env so neither the provisioning scripts nor the connector
hardcode secrets or resource names. Import `settings` and `make_client` from here.

No secrets live in this file — only the *names* of environment variables and the
*names* of AWS resources. Values come from .env (git-ignored).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env from the project root into os.environ (does not override real env vars).
load_dotenv()


# --- AWS resource names (the same strings provisioning creates and the connector reads) ---
SQS_QUEUE_NAME = "acx-recordings"
SQS_DLQ_NAME = "acx-recordings-dlq"
CONNECTOR_IAM_USER = "acx-connector"

# Connect writes audio under CallRecordings/ and CTR metadata under CTR/ in the SAME
# bucket. We only want the connector to react to *audio* objects, so the S3 event
# notification is filtered to this prefix. CTR is read on-demand when matching, not events.
RECORDINGS_PREFIX = "connect/acx-practice/CallRecordings/"
CTR_PREFIX = "connect/acx-practice/CTR/"


def _require(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        raise RuntimeError(
            f"Missing required env var {name!r}. Set it in .env "
            f"(see .env.example for the list)."
        )
    return val


@dataclass(frozen=True)
class Settings:
    aws_region: str
    s3_bucket: str
    sqs_queue_url: str          # may be "" before provisioning runs
    sqs_dlq_url: str            # may be "" before provisioning runs
    database_url: str

    # Admin creds used ONLY by infra/ provisioning scripts.
    aws_access_key_id: str
    aws_secret_access_key: str

    # Least-privilege creds the *connector* uses at runtime (created by the IAM script).
    connector_access_key_id: str
    connector_secret_access_key: str


def load_settings() -> Settings:
    return Settings(
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        s3_bucket=os.environ.get("S3_RECORDINGS_BUCKET", ""),
        sqs_queue_url=os.environ.get("SQS_QUEUE_URL", ""),
        sqs_dlq_url=os.environ.get("SQS_DLQ_URL", ""),
        database_url=os.environ.get("DATABASE_URL", ""),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        connector_access_key_id=os.environ.get("CONNECTOR_AWS_ACCESS_KEY_ID", ""),
        connector_secret_access_key=os.environ.get("CONNECTOR_AWS_SECRET_ACCESS_KEY", ""),
    )


settings = load_settings()


def make_client(service: str, *, as_connector: bool = False):
    """Build a boto3 client.

    as_connector=False (default) → use the ADMIN credentials (for provisioning).
    as_connector=True             → use the least-privilege CONNECTOR credentials.

    Keeping these separate is the whole point of the two-tier credential model:
    powerful keys build the infrastructure; a scoped key runs against it.
    """
    import boto3

    if as_connector:
        return boto3.client(
            service,
            region_name=settings.aws_region,
            aws_access_key_id=_require("CONNECTOR_AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=_require("CONNECTOR_AWS_SECRET_ACCESS_KEY"),
        )
    return boto3.client(
        service,
        region_name=settings.aws_region,
        aws_access_key_id=_require("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_require("AWS_SECRET_ACCESS_KEY"),
    )
