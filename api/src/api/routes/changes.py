"""POST /sites/{site_id}/changes and GET /changes/{change_id}/stats (JSON or CSV)."""

import csv
import datetime as dt
import hashlib
import io
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.auth import SettingsDep
from api.db.models import Change, Classification, Composite, Model, Site
from api.db.models import Run as RunRow
from api.db.session import get_session
from api.errors import ApiError
from api.queue import QueueDep
from api.ratelimit import enforce_rate_limit
from api.routes.classifications import (
    class_areas,
    class_ref,
    download_url,
    model_ref,
    tile_url,
    window_of,
)
from api.routes.runs import ACTIVE, CONTRACT_VERSION, _invalid, _resolve_model, _to_out, canonical
from api.schemas import Change as ChangeOut
from api.schemas import ChangeCreate, ChangeStats, Transition
from api.schemas import Run as RunOut
from api.storage import ObjectStore, StorageDep

router = APIRouter(dependencies=[Depends(enforce_rate_limit)], tags=["changes"])

SessionDep = Annotated[Session, Depends(get_session)]

CSV_HEADER = ["from_class_id", "from_class", "to_class_id", "to_class", "area_ha", "percent"]


def _change_out(session: Session, storage: ObjectStore, change: Change) -> ChangeOut:
    model = session.get(Model, change.model_id)
    frm = session.get(Composite, change.from_composite_id)
    to = session.get(Composite, change.to_composite_id)
    assert model is not None and frm is not None and to is not None
    return ChangeOut(  # type: ignore[call-arg]
        id=change.id,
        cog_uri=change.cog_uri,
        tile_url=tile_url(change.cog_uri),
        download_url=download_url(storage, change.cog_uri),
        model=model_ref(model),
        created_at=change.created_at,
        site_id=change.site_id,
        from_=window_of(frm),
        to=window_of(to),
        from_classification_id=change.from_classification_id,
        to_classification_id=change.to_classification_id,
    )


def _classification_for(
    session: Session, site: Site, ref_year: int, ref_season: str, model: Model, label: str
) -> tuple[Composite, Classification]:
    comp = session.scalar(
        select(Composite).where(
            Composite.site_id == site.id,
            Composite.year == ref_year,
            Composite.season == ref_season,
        )
    )
    cls = (
        session.scalar(
            select(Classification).where(
                Classification.composite_id == comp.id, Classification.model_id == model.id
            )
        )
        if comp is not None
        else None
    )
    if comp is None or cls is None:
        name = f"{model.name}-{model.version}"
        raise ApiError(
            422,
            "classification_missing",
            f"No {name} classification exists for {ref_year}-{ref_season}. "
            "Run a classification for that date first.",
            {"year": ref_year, "season": ref_season, "model": name, "window": label},
        )
    return comp, cls


@router.post(
    "/sites/{site_id}/changes",
    response_model=None,
    status_code=202,
    operation_id="createChange",
    summary="Compute change between two dates",
    responses={
        200: {"model": ChangeOut, "description": "The change already exists."},
        202: {"model": RunOut, "description": "Change run queued (or identical active run)."},
    },
)
def create_change(
    site_id: uuid.UUID,
    body: ChangeCreate,
    request: Request,
    response: Response,
    session: SessionDep,
    queue: QueueDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> ChangeOut | RunOut:
    site = session.get(Site, site_id)
    if site is None:
        raise ApiError(404, "not_found", "Site not found.", {"site_id": str(site_id)})
    model = _resolve_model(session, body.model)

    frm, frm_cls = _classification_for(
        session, site, body.from_.year, body.from_.season, model, "from"
    )
    to, to_cls = _classification_for(session, site, body.to.year, body.to.season, model, "to")
    if (frm.date_start, frm.date_end) >= (to.date_start, to.date_end) or frm.id == to.id:
        raise _invalid(["from"], "`from` must start before `to`.")

    existing_change = session.scalar(
        select(Change).where(
            Change.from_composite_id == frm.id,
            Change.to_composite_id == to.id,
            Change.model_id == model.id,
        )
    )
    if existing_change is not None:
        response.status_code = 200
        return _change_out(session, storage, existing_change)

    model_name = f"{model.name}-{model.version}"
    params: dict[str, Any] = {
        "from": {"year": frm.year, "season": frm.season},
        "to": {"year": to.year, "season": to.season},
        "model": model_name,
    }
    key = hashlib.sha256(
        canonical(
            {
                "kind": "change",
                "site": site.slug,
                "boundary_sha256": site.boundary_sha256,
                "params": params,
                "model": model_name,
            }
        ).encode()
    ).hexdigest()

    def active() -> RunRow | None:
        return session.scalar(
            select(RunRow).where(RunRow.idempotency_key == key, RunRow.status.in_(ACTIVE))
        )

    def accepted(run: RunRow) -> RunOut:
        response.headers["Location"] = str(request.url_for("get_run", run_id=run.id))
        return _to_out(session, run)

    found = active()
    if found is not None:
        return accepted(found)

    run = RunRow(
        id=uuid.uuid4(),
        site_id=site.id,
        kind="change",
        model_id=model.id,
        params=params,
        idempotency_key=key,
        outputs={"composite_ids": [], "classification_ids": [], "change_id": None},
    )
    run.log_uri = f"s3://{settings.artifacts_bucket}/runs/{run.id}/log.jsonl"
    session.add(run)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        found = active()
        if found is None:
            raise
        return accepted(found)
    session.refresh(run)
    session.commit()  # the worker must see the row before it gets the message

    message = {
        "contract_version": CONTRACT_VERSION,
        "kind": "change",
        "run_id": str(run.id),
        "site": {"id": str(site.id), "slug": site.slug},
        "windows": [
            {
                "year": c.year,
                "season": c.season,
                "start": c.date_start.isoformat(),
                "end": c.date_end.isoformat(),
            }
            for c in (frm, to)
        ],
        "model": {"name": model.name, "version": model.version},
        "params": params,
        "idempotency_key": key,
        "enqueued_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        queue.send(message)
    except Exception as exc:
        run.status = "failed"
        run.finished_at = dt.datetime.now(dt.UTC)
        run.error = {
            "code": "enqueue_failed",
            "message": "The job could not be queued. Try again shortly.",
            "detail": {"reason": type(exc).__name__},
        }
        session.commit()
        raise ApiError(
            503,
            "enqueue_failed",
            "The job could not be queued. Try again shortly.",
            {"run_id": str(run.id)},
        ) from exc
    return accepted(run)


def _parse_accept(header: str | None) -> str | None:
    """Pick `application/json` or `text/csv` from an Accept header; None means 406."""
    if header is None or not header.strip():
        return "application/json"
    best: tuple[float, int, str] | None = None
    for part in header.split(","):
        bits = [b.strip() for b in part.split(";")]
        media = bits[0].lower()
        q = 1.0
        for p in bits[1:]:
            if p.lower().startswith("q="):
                try:
                    q = float(p[2:])
                except ValueError:
                    q = 0.0
        if q <= 0:
            continue
        # (rank for ties: json wins, then csv); exact types beat wildcards via specificity
        for rank, candidate in ((1, "application/json"), (0, "text/csv")):
            if media in (candidate, "*/*", candidate.split("/")[0] + "/*"):
                specificity = 2 if media == candidate else 1 if media != "*/*" else 0
                cand = (q + specificity * 1e-3, rank, candidate)
                if best is None or cand[:2] > best[:2]:
                    best = cand
    return best[2] if best else None


def _csv(stats: dict[str, Any]) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(CSV_HEADER)
    rows = sorted(stats["transitions"], key=lambda t: (t["from_class_id"], t["to_class_id"]))
    for t in rows:
        f, to = class_ref(t["from_class_id"]), class_ref(t["to_class_id"])
        w.writerow([f.id, f.name, to.id, to.name, f"{t['area_ha']:.2f}", f"{t['percent']:.2f}"])
    return out.getvalue()


@router.get(
    "/changes/{change_id}/stats",
    response_model=ChangeStats,
    operation_id="getChangeStats",
    summary="Area per transition in hectares (JSON or CSV)",
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def get_change_stats(
    change_id: uuid.UUID,
    session: SessionDep,
    storage: StorageDep,
    accept: Annotated[str | None, Header()] = None,
) -> Any:
    media = _parse_accept(accept)
    if media is None:
        raise ApiError(
            406,
            "not_acceptable",
            "Use Accept: application/json or text/csv.",
            {"accept": accept},
        )
    change = session.get(Change, change_id)
    if change is None:
        raise ApiError(404, "not_found", "Change not found.", {"change_id": str(change_id)})
    stats = change.stats
    if media == "text/csv":
        site = session.get(Site, change.site_id)
        model = session.get(Model, change.model_id)
        frm = session.get(Composite, change.from_composite_id)
        to = session.get(Composite, change.to_composite_id)
        assert site and model and frm and to
        name = (
            f"{site.slug}-{frm.year}-{frm.season}__{to.year}-{to.season}"
            f"-{model.name}-{model.version}.csv"
        )
        return Response(
            _csv(stats),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    total = float(stats["total_ha"])
    return ChangeStats(
        change=_change_out(session, storage, change),
        pixel_area_ha=float(stats["pixel_area_ha"]),
        total_ha=total,
        transitions=[
            Transition(
                from_class=class_ref(t["from_class_id"]),
                to_class=class_ref(t["to_class_id"]),
                area_ha=float(t["area_ha"]),
                percent=float(t["percent"]),
            )
            for t in sorted(
                stats["transitions"], key=lambda t: (t["from_class_id"], t["to_class_id"])
            )
        ],
        from_class_areas=class_areas(stats["class_areas_ha"]["from"], total),
        to_class_areas=class_areas(stats["class_areas_ha"]["to"], total),
    )
