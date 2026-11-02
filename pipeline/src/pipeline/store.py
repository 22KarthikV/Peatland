"""S3 (LocalStack) upload with idempotency skip (contracts/cog-layout.md section 6)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pipeline.output import sidecar_key

DEFAULT_ENDPOINT = "http://localhost:4566"

Outcome = Literal["uploaded", "skipped"]


def s3_client(endpoint_url: str | None = None) -> Any:
    """boto3 S3 client; endpoint from AWS_ENDPOINT_URL (default LocalStack), creds test/test."""
    import boto3  # type: ignore[import-not-found,unused-ignore]  # lazy: only S3 code needs it

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or os.environ.get("AWS_ENDPOINT_URL", DEFAULT_ENDPOINT),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def object_exists(client: Any, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
    except client.exceptions.ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    return True


def existing_idempotency_key(client: Any, bucket: str, tif_key: str) -> str | None:
    """Idempotency key recorded in the stored sidecar, or None if absent/unreadable."""
    skey = sidecar_key(tif_key)
    if not object_exists(client, bucket, skey):
        return None
    body = client.get_object(Bucket=bucket, Key=skey)["Body"].read()
    try:
        value = json.loads(body).get("idempotency_key")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def is_up_to_date(client: Any, bucket: str, tif_key: str, idem_key: str) -> bool:
    return existing_idempotency_key(client, bucket, tif_key) == idem_key


def upload_product(
    tif_path: Path,
    sidecar_path: Path,
    bucket: str = "peatwatch-data",
    *,
    client: Any | None = None,
) -> Outcome:
    """Upload the .tif then the .json (sidecar last), unless the stored key matches.

    Returns "skipped" if a sidecar with the same idempotency key already exists.
    """
    sidecar = json.loads(Path(sidecar_path).read_text(encoding="utf-8"))
    tif_key: str = sidecar["key"]
    idem: str = sidecar["idempotency_key"]
    s3 = client or s3_client()
    if is_up_to_date(s3, bucket, tif_key, idem):
        return "skipped"
    s3.upload_file(
        str(tif_path),
        bucket,
        tif_key,
        ExtraArgs={"ContentType": "image/tiff; application=geotiff; profile=cloud-optimized"},
    )
    s3.upload_file(
        str(sidecar_path),
        bucket,
        sidecar_key(tif_key),
        ExtraArgs={"ContentType": "application/json"},
    )
    return "uploaded"
