"""X-API-Key check and a simple in-memory per-key rate limiter (SPEC D7)."""

import hashlib
import hmac
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request, Response

from api.errors import ApiError
from api.settings import Settings

WINDOW_SECONDS = 60.0

# Routes that start jobs get the tighter limit (openapi.yaml description).
JOB_ROUTES = frozenset(
    {
        ("POST", "/sites"),
        ("POST", "/sites/{site_id}/runs"),
        ("POST", "/sites/{site_id}/changes"),
    }
)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def is_valid_key(key: str | None, allowed_hashes: frozenset[str]) -> bool:
    if not key:
        return False
    digest = hash_key(key)
    return any(hmac.compare_digest(digest, h) for h in allowed_hashes)


@dataclass
class RateLimiter:
    """Fixed-window counter per (key hash, group). Single process only; fine for the MVP."""

    clock: Callable[[], float] = time.monotonic
    _windows: dict[tuple[str, str], tuple[float, int]] = field(default_factory=dict)

    def hit(self, subject: str, group: str, limit: int) -> tuple[bool, int, int]:
        """Return (allowed, remaining, retry_after_seconds)."""
        now = self.clock()
        start, count = self._windows.get((subject, group), (now, 0))
        if now - start >= WINDOW_SECONDS:
            start, count = now, 0
        if count >= limit:
            return False, 0, max(1, math.ceil(start + WINDOW_SECONDS - now))
        self._windows[(subject, group)] = (start, count + 1)
        return True, limit - count - 1, 0


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


SettingsDep = Annotated[Settings, Depends(_settings)]
LimiterDep = Annotated[RateLimiter, Depends(_limiter)]


async def require_api_key(request: Request, settings: SettingsDep) -> str:
    """Return the hash of the presented key, or raise 401."""
    key = request.headers.get("X-API-Key")
    if key is None or not is_valid_key(key, settings.api_key_hashes):
        raise ApiError(401, "unauthorized", "A valid X-API-Key header is required.")
    return hash_key(key)


async def enforce_rate_limit(
    request: Request,
    response: Response,
    subject: Annotated[str, Depends(require_api_key)],
    settings: SettingsDep,
    limiter: LimiterDep,
) -> str:
    """Auth plus rate limit; use as the dependency on every route except /health."""
    route = request.scope.get("route")
    template = getattr(route, "path", request.url.path)
    is_job = (request.method, template) in JOB_ROUTES
    limit = settings.rate_limit_job_per_minute if is_job else settings.rate_limit_default_per_minute
    allowed, remaining, retry_after = limiter.hit(subject, "job" if is_job else "default", limit)
    if not allowed:
        raise ApiError(
            429,
            "rate_limited",
            f"Too many requests; retry after {retry_after} seconds.",
            {"retry_after": retry_after},
            {
                "Retry-After": str(retry_after),
                "RateLimit-Limit": str(limit),
                "RateLimit-Remaining": "0",
            },
        )
    response.headers["RateLimit-Limit"] = str(limit)
    response.headers["RateLimit-Remaining"] = str(remaining)
    return subject
