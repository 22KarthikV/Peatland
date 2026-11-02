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


class ClassRef(BaseModel):
    id: int
    name: str
    label: str
    hex: str


class ClassArea(BaseModel):
    # `class` is a keyword, so the field is aliased; responses are dumped by alias.
    model_config = ConfigDict(populate_by_name=True)

    class_: ClassRef = Field(alias="class")
    area_ha: float
    percent: float


class Window(BaseModel):
    year: int
    season: str
    start: dt.date
    end: dt.date


class Product(BaseModel):
    id: uuid.UUID
    cog_uri: str
    tile_url: str
    download_url: str
    model: ModelRef
    created_at: dt.datetime


class Classification(Product):
    composite_id: uuid.UUID
    window: Window
    class_areas: list[ClassArea]


class ClassificationPage(BaseModel):
    items: list[Classification]
    total: int
    limit: int
    offset: int


class WindowRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: int = Field(ge=2015, le=2100)
    season: str = Field(pattern=r"^(spring|summer|autumn|winter|annual|w[0-9]{8}-[0-9]{8})$")


class ChangeCreate(BaseModel):
    """Request body of POST /sites/{id}/changes."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: WindowRef = Field(alias="from")
    to: WindowRef
    model: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,31}-[0-9A-Za-z._-]{1,32}$"
    )


class Change(Product):
    model_config = ConfigDict(populate_by_name=True)

    site_id: uuid.UUID
    from_: Window = Field(alias="from")
    to: Window
    from_classification_id: uuid.UUID
    to_classification_id: uuid.UUID


class Transition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_class: ClassRef
    to_class: ClassRef
    area_ha: float
    percent: float


class ChangeStats(BaseModel):
    change: Change
    units: Literal["hectares"] = "hectares"
    pixel_area_ha: float
    total_ha: float
    transitions: list[Transition]
    from_class_areas: list[ClassArea]
    to_class_areas: list[ClassArea]
