"""FastAPI application factory."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from api.errors import register_error_handlers
from api.routes import sites
from api.security import RateLimiter
from api.settings import Settings, get_settings

VERSION = "0.1.0"


class Health(BaseModel):
    status: Literal["ok"]
    version: str


def create_app(settings: Settings | None = None, limiter: RateLimiter | None = None) -> FastAPI:
    app = FastAPI(title="PeatWatch API", version=VERSION)
    app.state.settings = settings or get_settings()
    app.state.rate_limiter = limiter or RateLimiter()
    register_error_handlers(app)

    @app.get("/health", response_model=Health, tags=["health"], operation_id="getHealth")
    async def get_health() -> Health:
        """Liveness check for the load balancer (no auth)."""
        return Health(status="ok", version=VERSION)

    app.include_router(sites.router)

    return app


app = create_app()
