"""GET /sites/{site_id}/classifications, plus helpers shared with changes.py."""

import os
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.db.models import Classification, Composite, Model, Site
from api.db.session import get_session
from api.errors import ApiError
from api.ratelimit import enforce_rate_limit
from api.schemas import (
    ClassArea,
    ClassificationPage,
    ClassRef,
    ModelRef,
    Window,
)
from api.schemas import Classification as ClassificationOut
from api.storage import ObjectStore, StorageDep

router = APIRouter(dependencies=[Depends(enforce_rate_limit)], tags=["classifications"])

SessionDep = Annotated[Session, Depends(get_session)]

PRESIGN_SECONDS = 900
# Mirrors contracts/classes.yaml (a test asserts they match).
CLASSES: dict[int, ClassRef] = {
    c.id: c
    for c in (
        ClassRef(id=0, name="other", label="Other", hex="#999999"),
        ClassRef(id=1, name="bog", label="Bog (peatland)", hex="#0072B2"),
        ClassRef(id=2, name="woodland", label="Woodland", hex="#009E73"),
        ClassRef(id=3, name="felled_or_burnt_bare", label="Felled or burnt-bare", hex="#D55E00"),
    )
}


def class_ref(class_id: int) -> ClassRef:
    return CLASSES.get(
        class_id,
        ClassRef(id=class_id, name=f"class_{class_id}", label=f"Class {class_id}", hex="#999999"),
    )


def class_areas(areas_ha: dict[str, float], total_ha: float | None = None) -> list[ClassArea]:
    """Per-class areas ordered by class id; percent of `total_ha` (default: their sum)."""
    total = sum(areas_ha.values()) if total_ha is None else total_ha
    return [
        ClassArea(  # type: ignore[call-arg]
            class_=class_ref(int(k)),
            area_ha=round(float(v), 2),
            percent=round(float(v) / total * 100, 2) if total > 0 else 0.0,
        )
        for k, v in sorted(areas_ha.items(), key=lambda kv: int(kv[0]))
    ]


def titiler_base() -> str:
    return os.environ.get("TITILER_BASE_URL", "http://localhost:8001").rstrip("/")


def tile_url(cog_uri: str) -> str:
    return (
        f"{titiler_base()}/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png"
        f"?url={quote(cog_uri, safe='')}"
    )


def download_url(storage: ObjectStore, cog_uri: str) -> str:
    """Presigned GET for an `s3://bucket/key` URI; 503 when the store cannot sign."""
    bucket, _, key = cog_uri.removeprefix("s3://").partition("/")
    try:
        return storage.presign_get(bucket, key, PRESIGN_SECONDS)
    except Exception as exc:
        raise ApiError(
            503, "storage_unavailable", "A download link could not be created. Try again."
        ) from exc


def model_ref(model: Model) -> ModelRef:
    return ModelRef(id=model.id, name=model.name, version=model.version)


def window_of(comp: Composite) -> Window:
    return Window(year=comp.year, season=comp.season, start=comp.date_start, end=comp.date_end)


@router.get(
    "/sites/{site_id}/classifications",
    response_model=ClassificationPage,
    operation_id="listClassifications",
    summary="List classification maps by date",
)
def list_classifications(
    site_id: uuid.UUID,
    session: SessionDep,
    storage: StorageDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    model: Annotated[
        str | None, Query(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}(-[0-9A-Za-z._-]{1,32})?$")
    ] = None,
    year: Annotated[int | None, Query(ge=2015, le=2100)] = None,
) -> ClassificationPage:
    if session.get(Site, site_id) is None:
        raise ApiError(404, "not_found", "Site not found.", {"site_id": str(site_id)})
    stmt = (
        select(Classification, Composite, Model)
        .join(Composite, Composite.id == Classification.composite_id)
        .join(Model, Model.id == Classification.model_id)
        .where(Composite.site_id == site_id)
    )
    if model is not None:
        stmt = stmt.where(
            (Model.name == model) | (func.concat(Model.name, "-", Model.version) == model)
        )
    if year is not None:
        stmt = stmt.where(Composite.year == year)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(
        stmt.order_by(
            Composite.date_start, Composite.date_end, Classification.created_at, Classification.id
        )
        .limit(limit)
        .offset(offset)
    ).all()
    items = [
        ClassificationOut(
            id=cls.id,
            cog_uri=cls.cog_uri,
            tile_url=tile_url(cls.cog_uri),
            download_url=download_url(storage, cls.cog_uri),
            model=model_ref(m),
            created_at=cls.created_at,
            composite_id=comp.id,
            window=window_of(comp),
            class_areas=class_areas(cls.class_areas_ha),
        )
        for cls, comp, m in rows
    ]
    return ClassificationPage(items=items, total=total, limit=limit, offset=offset)
