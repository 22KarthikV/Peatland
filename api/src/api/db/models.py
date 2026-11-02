"""SQLAlchemy 2.0 models mirroring contracts/db-schema.md (C3).

The Alembic migration 0001 is the executable DDL (including the run-state trigger); these
models must match it. Check constraints get the `ck_<table>_` prefix from the naming convention.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base

RUN_KINDS = ("pipeline", "change")
RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")

run_kind = ENUM(*RUN_KINDS, name="run_kind", create_type=False)
run_status = ENUM(*RUN_STATUSES, name="run_status", create_type=False)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created_at() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _hash_check(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} ~ '^[0-9a-f]{{64}}$'", name=f"{column}_hex")


def _fk(target: str) -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete="RESTRICT"))


def _fk_null(target: str) -> Mapped[uuid.UUID | None]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete="RESTRICT"))


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    boundary: Mapped[Any] = mapped_column(
        Geometry("MULTIPOLYGON", srid=4326, spatial_index=False), nullable=False
    )
    area_ha: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        Computed("(ST_Area(ST_Transform(boundary, 32630)) / 10000)::numeric(12,2)", persisted=True),
    )
    boundary_sha256: Mapped[str] = mapped_column(CHAR(64))
    boundary_uri: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'", name="slug_format"),
        CheckConstraint("length(name) BETWEEN 1 AND 200", name="name_length"),
        CheckConstraint("ST_IsValid(boundary)", name="boundary_valid"),
        _hash_check("boundary_sha256"),
        UniqueConstraint("slug", name="uq_sites_slug"),
        Index("ix_sites_boundary", "boundary", postgresql_using="gist"),
        Index("ix_sites_created_at", text("created_at DESC")),
    )


class Model(Base):
    __tablename__ = "models"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    artifact_uri: Mapped[str] = mapped_column(Text)
    model_card_uri: Mapped[str | None] = mapped_column(Text)
    git_commit: Mapped[str] = mapped_column(Text)
    classes_contract: Mapped[int] = mapped_column(SmallInteger)
    is_default: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("name ~ '^[a-z0-9][a-z0-9_-]{0,31}$'", name="name_format"),
        CheckConstraint("version ~ '^[0-9A-Za-z._-]{1,32}$'", name="version_format"),
        CheckConstraint("git_commit ~ '^[0-9a-f]{7,40}$'", name="git_commit_format"),
        UniqueConstraint("name", "version", name="uq_models_name_version"),
        Index(
            "uq_models_is_default", "is_default", unique=True, postgresql_where=text("is_default")
        ),
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    site_id: Mapped[uuid.UUID] = _fk("sites.id")
    kind: Mapped[str] = mapped_column(run_kind)
    status: Mapped[str] = mapped_column(run_status, nullable=False, server_default="queued")
    model_id: Mapped[uuid.UUID] = _fk("models.id")
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(CHAR(64))
    progress: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    outputs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    log_uri: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created_at()
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _hash_check("idempotency_key"),
        CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name="error_iff_failed"),
        CheckConstraint(
            "status NOT IN ('running', 'succeeded') OR started_at IS NOT NULL",
            name="started_when_running",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)",
            name="finished_iff_terminal",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="finished_after_started",
        ),
        Index("ix_runs_site_id_created_at", "site_id", text("created_at DESC")),
        Index("ix_runs_status_created_at", "status", "created_at"),
        Index("ix_runs_model_id", "model_id"),
        Index(
            "uq_runs_active_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )


class Composite(Base):
    __tablename__ = "composites"

    id: Mapped[uuid.UUID] = _uuid_pk()
    site_id: Mapped[uuid.UUID] = _fk("sites.id")
    year: Mapped[int] = mapped_column(SmallInteger)
    season: Mapped[str] = mapped_column(Text)
    date_start: Mapped[dt.date] = mapped_column(Date)
    date_end: Mapped[dt.date] = mapped_column(Date)
    cog_uri: Mapped[str] = mapped_column(Text)
    pipeline_version: Mapped[str] = mapped_column(Text)
    scene_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))
    cloud_stats: Mapped[dict[str, Any]] = mapped_column(JSONB)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(CHAR(64))
    run_id: Mapped[uuid.UUID | None] = _fk_null("runs.id")
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("year BETWEEN 2015 AND 2100", name="year_range"),
        CheckConstraint(
            "season ~ '^(spring|summer|autumn|winter|annual|w[0-9]{8}-[0-9]{8})$'",
            name="season_format",
        ),
        CheckConstraint("date_end >= date_start", name="dates_ordered"),
        _hash_check("idempotency_key"),
        UniqueConstraint("idempotency_key", name="uq_composites_idempotency_key"),
        UniqueConstraint("cog_uri", name="uq_composites_cog_uri"),
        UniqueConstraint("site_id", "year", "season", name="uq_composites_site_id_year_season"),
        Index("ix_composites_site_id_date_start", "site_id", "date_start"),
        Index("ix_composites_run_id", "run_id"),
    )


class Classification(Base):
    __tablename__ = "classifications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    composite_id: Mapped[uuid.UUID] = _fk("composites.id")
    model_id: Mapped[uuid.UUID] = _fk("models.id")
    cog_uri: Mapped[str] = mapped_column(Text)
    class_areas_ha: Mapped[dict[str, Any]] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(CHAR(64))
    run_id: Mapped[uuid.UUID | None] = _fk_null("runs.id")
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _hash_check("idempotency_key"),
        UniqueConstraint("idempotency_key", name="uq_classifications_idempotency_key"),
        UniqueConstraint("cog_uri", name="uq_classifications_cog_uri"),
        UniqueConstraint(
            "composite_id", "model_id", name="uq_classifications_composite_id_model_id"
        ),
        Index("ix_classifications_model_id", "model_id"),
        Index("ix_classifications_run_id", "run_id"),
    )


class Change(Base):
    __tablename__ = "changes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    site_id: Mapped[uuid.UUID] = _fk("sites.id")
    from_composite_id: Mapped[uuid.UUID] = _fk("composites.id")
    to_composite_id: Mapped[uuid.UUID] = _fk("composites.id")
    from_classification_id: Mapped[uuid.UUID] = _fk("classifications.id")
    to_classification_id: Mapped[uuid.UUID] = _fk("classifications.id")
    model_id: Mapped[uuid.UUID] = _fk("models.id")
    cog_uri: Mapped[str] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB)
    stats_csv_uri: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(CHAR(64))
    run_id: Mapped[uuid.UUID | None] = _fk_null("runs.id")
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("from_composite_id <> to_composite_id", name="distinct_composites"),
        _hash_check("idempotency_key"),
        UniqueConstraint("idempotency_key", name="uq_changes_idempotency_key"),
        UniqueConstraint("cog_uri", name="uq_changes_cog_uri"),
        UniqueConstraint(
            "from_composite_id",
            "to_composite_id",
            "model_id",
            name="uq_changes_from_to_composite_model",
        ),
        Index("ix_changes_site_id_created_at", "site_id", text("created_at DESC")),
        Index("ix_changes_to_composite_id", "to_composite_id"),
        Index("ix_changes_from_classification_id", "from_classification_id"),
        Index("ix_changes_to_classification_id", "to_classification_id"),
        Index("ix_changes_model_id", "model_id"),
        Index("ix_changes_run_id", "run_id"),
    )
