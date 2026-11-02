"""X-API-Key check against stored SHA-256 hashes; keys from env or Secrets Manager."""

import hashlib
import hmac
import logging
import re
from typing import Annotated

from fastapi import Depends, Request

from api.errors import ApiError
from api.settings import Settings

logger = logging.getLogger(__name__)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def is_valid_key(key: str | None, allowed_hashes: frozenset[str]) -> bool:
    """Constant-time compare of the key's digest against every allowed hash (no early exit)."""
    if not key:
        return False
    digest = hash_key(key)
    matched = False
    for h in allowed_hashes:
        matched |= hmac.compare_digest(digest, h)
    return matched


def load_secret_hashes(settings: Settings) -> frozenset[str]:
    """Hashes of the keys held in the Secrets Manager secret (comma-separated plaintext keys).

    Plaintext is hashed immediately and discarded. Returns an empty set when no secret is
    configured or it cannot be read, so the env-var hashes remain the fallback.
    """
    if not settings.api_key_secret_id:
        return frozenset()
    try:
        import boto3

        client = boto3.client(
            "secretsmanager",
            region_name=settings.aws_region,
            endpoint_url=settings.aws_endpoint_url,
        )
        value = client.get_secret_value(SecretId=settings.api_key_secret_id)["SecretString"]
    except Exception:
        logger.warning("could not read API key secret; using API_KEYS only", exc_info=True)
        return frozenset()
    hashes: set[str] = set()
    for item in value.split(","):
        item = item.strip()
        if item:
            low = item.lower()
            hashes.add(low if _HEX64.match(low) else hash_key(item))
    return frozenset(hashes)


def load_key_hashes(settings: Settings) -> frozenset[str]:
    return settings.api_key_hashes | load_secret_hashes(settings)


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(_settings)]


async def require_api_key(request: Request, settings: SettingsDep) -> str:
    """Return the hash of the presented key, or raise 401."""
    allowed: frozenset[str] = getattr(request.app.state, "api_key_hashes", settings.api_key_hashes)
    key = request.headers.get("X-API-Key")
    if key is None or not is_valid_key(key, allowed):
        raise ApiError(401, "unauthorized", "A valid X-API-Key header is required.")
    return hash_key(key)
