"""Run configuration: a Pydantic model loaded from YAML."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml  # types-PyYAML not in lock; see handoff
from pydantic import BaseModel, ConfigDict, Field, model_validator

SITE_PATTERN = r"^[a-z0-9][a-z0-9-]{1,62}$"


class DateWindow(BaseModel):
    """Inclusive UTC date window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> DateWindow:
        if self.end < self.start:
            raise ValueError(f"date window end {self.end} is before start {self.start}")
        return self


class Buckets(BaseModel):
    """Bucket names (contracts/cog-layout.md section 1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data: str = "peatwatch-data"
    artifacts: str = "peatwatch-artifacts"


class RunConfig(BaseModel):
    """Everything a pipeline run needs; the resolved form is logged and hashed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    site: str = Field(pattern=SITE_PATTERN)
    boundary_path: Path
    window: DateWindow
    cloud_threshold: float = Field(default=40.0, ge=0, le=100, description="Max scene cloud %.")
    output_dir: Path = Path("out")
    buckets: Buckets = Field(default_factory=Buckets)
    parameters: dict[str, Any] = Field(default_factory=dict)


def load_config(path: Path) -> RunConfig:
    """Load and validate a YAML config file; raise with a clear message on bad input."""
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"config {path} must be a YAML mapping")
    cfg: RunConfig = RunConfig.model_validate(raw)
    if not cfg.boundary_path.is_absolute():
        # Resolve relative to the config file so runs do not depend on the cwd.
        cfg = cfg.model_copy(update={"boundary_path": path.parent / cfg.boundary_path})
    return cfg
