"""Pydantic models for request and response bodies (mirror contracts/openapi.yaml)."""

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SiteCreate(BaseModel):
    """Request body of POST /sites. The boundary is validated in api.validation."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    boundary: dict[str, Any]


class RunSummary(BaseModel):
    id: uuid.UUID
    kind: Literal["pipeline", "change"]
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    created_at: dt.datetime
    finished_at: dt.datetime | None


class SiteSummary(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    area_ha: float
    created_at: dt.datetime
    latest_run: RunSummary | None


class AvailableWindow(BaseModel):
    year: int
    season: str
    start: dt.date
    end: dt.date
    composite_id: uuid.UUID
    models: list[str]


class SiteDetail(SiteSummary):
    boundary: dict[str, Any]
    available_windows: list[AvailableWindow]


class SitePage(BaseModel):
    items: list[SiteSummary]
    total: int
    limit: int
    offset: int
