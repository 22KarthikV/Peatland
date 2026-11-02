"""POST /sites, GET /sites, GET /sites/{site_id}."""

import hashlib
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from geoalchemy2.shape import to_shape
from shapely.geometry import MultiPolygon, mapping
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.db.models import Classification, Composite, Model, Run, Site
from api.db.session import get_session
from api.errors import ApiError
from api.schemas import (
    AvailableWindow,
    RunSummary,
    SiteCreate,
    SiteDetail,
    SitePage,
    SiteSummary,
)
from api.security import SettingsDep, enforce_rate_limit
from api.validation import (
    check_region_and_area,
    is_valid_slug,
    parse_boundary,
    slugify,
)

router = APIRouter(dependencies=[Depends(enforce_rate_limit)], tags=["sites"])

SessionDep = Annotated[Session, Depends(get_session)]


def _boundary_json(geom: MultiPolygon) -> dict[str, object]:
    return dict(mapping(geom))


def _run_summary(run: Run | None) -> RunSummary | None:
    if run is None:
        return None
    return RunSummary(
        id=run.id,
        kind=run.kind,  # type: ignore[arg-type]
        status=run.status,  # type: ignore[arg-type]
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def _latest_runs(session: Session, site_ids: list[uuid.UUID]) -> dict[uuid.UUID, Run]:
    if not site_ids:
        return {}
    stmt = (
        select(Run)
        .where(Run.site_id.in_(site_ids))
        .ext(distinct_on(Run.site_id))
        .order_by(Run.site_id, Run.created_at.desc(), Run.id.desc())
    )
    return {run.site_id: run for run in session.scalars(stmt)}


def _summary(site: Site, run: Run | None) -> SiteSummary:
    return SiteSummary(
        id=site.id,
        slug=site.slug,
        name=site.name,
        area_ha=float(site.area_ha),
        created_at=site.created_at,
        latest_run=_run_summary(run),
    )


def _windows(session: Session, site_id: uuid.UUID) -> list[AvailableWindow]:
    comps = session.scalars(
        select(Composite)
        .where(Composite.site_id == site_id)
        .order_by(Composite.date_start, Composite.date_end, Composite.season)
    ).all()
    models: dict[uuid.UUID, list[str]] = {}
    rows = session.execute(
        select(Classification.composite_id, Model.name, Model.version)
        .join(Model, Model.id == Classification.model_id)
        .where(Classification.composite_id.in_([c.id for c in comps]))
        .order_by(Model.name, Model.version)
    )
    for composite_id, name, version in rows:
        models.setdefault(composite_id, []).append(f"{name}-{version}")
    return [
        AvailableWindow(
            year=c.year,
            season=c.season,
            start=c.date_start,
            end=c.date_end,
            composite_id=c.id,
            models=models.get(c.id, []),
        )
        for c in comps
    ]


def _detail(session: Session, site: Site, run: Run | None) -> SiteDetail:
    geom = to_shape(site.boundary)
    return SiteDetail(
        **_summary(site, run).model_dump(),
        boundary=_boundary_json(geom),
        available_windows=_windows(session, site.id),
    )


@router.post(
    "/sites",
    response_model=SiteDetail,
    status_code=201,
    operation_id="createSite",
    summary="Create a site from a GeoJSON boundary",
)
def create_site(
    body: SiteCreate,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> SiteDetail:
    geom = parse_boundary(body.boundary)
    check_region_and_area(session, geom)

    slug = body.slug or slugify(body.name)
    if slug is None or not is_valid_slug(slug):
        raise ApiError(
            422,
            "validation_error",
            "A slug cannot be derived from the name; provide a `slug` (2-63 characters, "
            "lowercase letters, digits and hyphens).",
            [
                {
                    "loc": ["body", "slug"],
                    "msg": "slug is required for this name",
                    "type": "value_error",
                }
            ],
        )

    # SHA-256 of the GeoJSON bytes that belong at boundary_uri (cog-layout.md section 2).
    geojson = json.dumps(_boundary_json(geom), separators=(",", ":"), sort_keys=True)
    site = Site(
        slug=slug,
        name=body.name,
        boundary=func.ST_Multi(func.ST_GeomFromText(geom.wkt, 4326)),
        boundary_sha256=hashlib.sha256(geojson.encode()).hexdigest(),
        boundary_uri=f"s3://{settings.data_bucket}/sites/{slug}/boundary.geojson",
    )
    session.add(site)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if "uq_sites_slug" in str(exc.orig):
            raise ApiError(
                409,
                "conflict",
                f"A site with slug {slug} already exists. Choose a different slug.",
                {"slug": slug},
            ) from exc
        raise
    session.refresh(site)
    session.commit()
    response.headers["Location"] = str(request.url_for("get_site", site_id=site.id))
    return _detail(session, site, None)


@router.get("/sites", response_model=SitePage, operation_id="listSites", summary="List sites")
def list_sites(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> SitePage:
    stmt = select(Site)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            Site.name.ilike(pattern, escape="\\") | Site.slug.ilike(pattern, escape="\\")
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    sites = session.scalars(
        stmt.order_by(Site.created_at.desc(), Site.id.desc()).limit(limit).offset(offset)
    ).all()
    runs = _latest_runs(session, [s.id for s in sites])
    return SitePage(
        items=[_summary(s, runs.get(s.id)) for s in sites],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/sites/{site_id}",
    response_model=SiteDetail,
    operation_id="getSite",
    summary="Show one site with its available dates",
)
def get_site(site_id: uuid.UUID, session: SessionDep) -> SiteDetail:
    site = session.get(Site, site_id)
    if site is None:
        raise ApiError(404, "not_found", "Site not found.", {"site_id": str(site_id)})
    return _detail(session, site, _latest_runs(session, [site.id]).get(site.id))
