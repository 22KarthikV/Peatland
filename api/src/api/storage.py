"""Object store access behind a small interface (like queue.py).

`S3ObjectStore` is the real boto3 implementation; `InMemoryObjectStore` is the test fake.
Routes get the store through the `get_storage` dependency, which tests override.
"""

from functools import cached_property
from typing import Annotated, Any, Protocol

from fastapi import Depends, Request

from api.settings import Settings


class ObjectStore(Protocol):
    def put(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        """Write one object; raise on failure."""
        ...

    def presign_get(self, bucket: str, key: str, expires_seconds: int = 900) -> str:
        """Short-lived GET URL for one object; raise on failure."""
        ...


class InMemoryObjectStore:
    """Test double: keeps objects by (bucket, key); set `fail` to simulate an upload error."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.fail = False

    def put(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        if self.fail:
            raise RuntimeError("simulated S3 failure")
        self.objects[(bucket, key)] = data

    def presign_get(self, bucket: str, key: str, expires_seconds: int = 900) -> str:
        if self.fail:
            raise RuntimeError("simulated S3 failure")
        return (
            f"http://localhost:4566/{bucket}/{key}?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            f"&X-Amz-Expires={expires_seconds}&X-Amz-Signature={'0' * 64}"
        )


class S3ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @cached_property
    def _client(self) -> Any:
        import boto3

        return boto3.client(
            "s3",
            region_name=self._settings.aws_region,
            endpoint_url=self._settings.aws_endpoint_url or None,
        )

    def put(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)

    def presign_get(self, bucket: str, key: str, expires_seconds: int = 900) -> str:
        return str(
            self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires_seconds,
            )
        )


def get_storage(request: Request) -> ObjectStore:
    store: ObjectStore | None = getattr(request.app.state, "object_store", None)
    if store is None:
        store = S3ObjectStore(request.app.state.settings)
        request.app.state.object_store = store
    return store


StorageDep = Annotated[ObjectStore, Depends(get_storage)]
