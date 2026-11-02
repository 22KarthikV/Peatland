"""Pydantic models for request and response bodies (mirror contracts/openapi.yaml)."""

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class DateRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: dt.date
    end: dt.date


class RunCreate(BaseModel):
    """Request body of POST /sites/{id}/runs."""

    model_config = ConfigDict(extra="forbid")

    windows: list[DateRange] = Field(min_length=1, max_length=8)
    model: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,31}-[0-9A-Za-z._-]{1,32}$"
    )
    max_cloud_cover_pct: float = Field(default=60, ge=0, le=100)

    @model_validator(mode="after")
    def _ranges_ordered(self) -> "RunCreate":
        for w in self.windows:
            if w.end < w.start:
                raise ValueError("window end must not be before start")
        return self


class ModelRef(BaseModel):
    id: uuid.UUID
    name: str
    version: str


class Run(RunSummary):
    site_id: uuid.UUID
    model: ModelRef
    params: dict[str, Any]
    progress: dict[str, Any]
    outputs: dict[str, Any]
    error: dict[str, Any] | None
    started_at: dt.datetime | None
