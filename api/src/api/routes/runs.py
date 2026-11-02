"""POST /sites/{site_id}/runs and GET /runs/{run_id}."""

import calendar
import datetime as dt
import hashlib
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.auth import SettingsDep
from api.db.models import Model, Run, Site
from api.db.session import get_session
from api.errors import ApiError
from api.queue import QueueDep
from api.ratelimit import enforce_rate_limit
from api.schemas import DateRange, ModelRef, RunCreate
from api.schemas import Run as RunOut

router = APIRouter(dependencies=[Depends(enforce_rate_limit)], tags=["runs"])

SessionDep = Annotated[Session, Depends(get_session)]

CONTRACT_VERSION = 1
ACTIVE = ("queued", "running")


def canonical(obj: Any) -> str:
    """Canonical JSON (cog-layout.md section 6)."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def season_for(start: dt.date, end: dt.date) -> tuple[int, str]:
    """(year, C2 season token) for a date range."""
    y = start.year
    if (start, end) == (dt.date(y, 1, 1), dt.date(y, 12, 31)):
        return y, "annual"
    for name, (m1, m2) in {"spring": (3, 5), "summer": (6, 8), "autumn": (9, 11)}.items():
        if (start, end) == (dt.date(y, m1, 1), dt.date(y, m2, calendar.monthrange(y, m2)[1])):
            return y, name
    ny = y + 1
    if (start, end) == (dt.date(y, 12, 1), dt.date(ny, 2, calendar.monthrange(ny, 2)[1])):
        return y, "winter"
    return y, f"w{start:%Y%m%d}-{end:%Y%m%d}"


def _invalid(loc: list[str | int], msg: str) -> ApiError:
    return ApiError(
        422, "validation_error", msg, [{"loc": ["body", *loc], "msg": msg, "type": "value_error"}]
    )


def _resolve_model(session: Session, ref: str | None) -> Model:
    if ref is None:
        model = session.scalar(select(Model).where(Model.is_default))
        if model is None:
            raise _invalid(["model"], "No default model is registered; specify `model`.")
        return model
    for model in session.scalars(select(Model)):
        if f"{model.name}-{model.version}" == ref:
            return model
    raise _invalid(["model"], f"Unknown model {ref}.")


def _clean_ranges(windows: list[DateRange]) -> list[DateRange]:
    """Sorted, de-duplicated ranges, so the same request in any order gets the same key."""
    return [DateRange(start=s, end=e) for s, e in sorted({(w.start, w.end) for w in windows})]


def _to_out(session: Session, run: Run) -> RunOut:
    model = session.get(Model, run.model_id)
    assert model is not None
    return RunOut(
        id=run.id,
        kind=run.kind,  # type: ignore[arg-type]
        status=run.status,  # type: ignore[arg-type]
        created_at=run.created_at,
        finished_at=run.finished_at,
        site_id=run.site_id,
        model=ModelRef(id=model.id, name=model.name, version=model.version),
        params=run.params,
        progress=run.progress,
        outputs=run.outputs,
        error=run.error,
        started_at=run.started_at,
    )


def _active(session: Session, key: str) -> Run | None:
    return session.scalar(select(Run).where(Run.idempotency_key == key, Run.status.in_(ACTIVE)))


@router.post(
    "/sites/{site_id}/runs",
    response_model=RunOut,
    status_code=202,
    operation_id="createRun",
    summary="Start a pipeline and inference run for a date range",
    responses={200: {"model": RunOut, "description": "An identical run is already active."}},
)
def create_run(
    site_id: uuid.UUID,
    body: RunCreate,
    request: Request,
    response: Response,
    session: SessionDep,
    queue: QueueDep,
    settings: SettingsDep,
) -> RunOut:
    site = session.get(Site, site_id)
    if site is None:
        raise ApiError(404, "not_found", "Site not found.", {"site_id": str(site_id)})
    model = _resolve_model(session, body.model)

    ranges = _clean_ranges(body.windows)
    windows: list[dict[str, Any]] = []
    for r in ranges:
        year, season = season_for(r.start, r.end)
        if not 2015 <= year <= 2100:
            raise _invalid(["windows"], "Window years must be between 2015 and 2100.")
        windows.append(
            {"year": year, "season": season, "start": r.start.isoformat(), "end": r.end.isoformat()}
        )
    cloud = body.max_cloud_cover_pct
    params: dict[str, Any] = {
        "windows": [{"start": r.start.isoformat(), "end": r.end.isoformat()} for r in ranges],
        "model": f"{model.name}-{model.version}",
        "max_cloud_cover_pct": int(cloud) if float(cloud).is_integer() else cloud,
    }
    key = hashlib.sha256(
        canonical(
            {
                "kind": "pipeline",
                "site": site.slug,
                "boundary_sha256": site.boundary_sha256,
                "params": params,
                "model": params["model"],
            }
        ).encode()
    ).hexdigest()

    existing = _active(session, key)
    if existing is not None:
        response.status_code = 200
        return _to_out(session, existing)

    run = Run(
        id=uuid.uuid4(),
        site_id=site.id,
        kind="pipeline",
        model_id=model.id,
        params=params,
        idempotency_key=key,
    )
    run.log_uri = f"s3://{settings.artifacts_bucket}/runs/{run.id}/log.jsonl"
    session.add(run)
    try:
        session.flush()
    except IntegrityError:
        # Lost a race with an identical request: the partial unique index fired.
        session.rollback()
        existing = _active(session, key)
        if existing is None:
            raise
        response.status_code = 200
        return _to_out(session, existing)
    session.refresh(run)
    session.commit()  # the worker must be able to see the row before it gets the message

    message = {
        "contract_version": CONTRACT_VERSION,
        "kind": "pipeline",
        "run_id": str(run.id),
        "site": {"id": str(site.id), "slug": site.slug},
        "windows": windows,
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

    response.headers["Location"] = str(request.url_for("get_run", run_id=run.id))
    return _to_out(session, run)


@router.get(
    "/runs/{run_id}", response_model=RunOut, operation_id="getRun", summary="Poll run status"
)
def get_run(run_id: uuid.UUID, session: SessionDep) -> RunOut:
    run = session.get(Run, run_id)
    if run is None:
        raise ApiError(404, "not_found", "Run not found.", {"run_id": str(run_id)})
    return _to_out(session, run)
