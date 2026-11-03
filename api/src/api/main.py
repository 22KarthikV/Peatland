"""FastAPI application factory."""

from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.auth import load_key_hashes
from api.errors import register_error_handlers
from api.ratelimit import RateLimiter
from api.routes import changes, classifications, runs, sites
from api.settings import Settings, get_settings

VERSION = "0.1.0"


class Health(BaseModel):
    status: Literal["ok"]
    version: str


def create_app(settings: Settings | None = None, limiter: RateLimiter | None = None) -> FastAPI:
    app = FastAPI(title="PeatWatch API", version=VERSION)
    cfg = settings or get_settings()
    origins = [o.strip() for o in cfg.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["X-API-Key", "Content-Type", "Accept"],
            expose_headers=["Location", "Retry-After", "Content-Disposition"],
        )
    app.state.settings = settings or get_settings()
    app.state.api_key_hashes = load_key_hashes(app.state.settings)
    app.state.rate_limiter = limiter or RateLimiter()
    register_error_handlers(app)

    @app.get("/health", response_model=Health, tags=["health"], operation_id="getHealth")
    async def get_health() -> Health:
        """Liveness check for the load balancer (no auth)."""
        return Health(status="ok", version=VERSION)

    app.include_router(sites.router)
    app.include_router(runs.router)
    app.include_router(classifications.router)
    app.include_router(changes.router)

    return app


app = create_app()
