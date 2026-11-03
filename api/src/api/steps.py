"""Real worker steps (A9a): pipeline composite -> NDVI baseline classification -> change.

The worker runs two steps per message (`pipeline`, `inference`). `WorkerSteps` dispatches on
the message kind:

- `kind=pipeline`: `pipeline_step` builds one composite per window with the pipeline runner
  (idempotent: it skips when the stored sidecar key matches), upserts `composites` rows;
  `inference_step` classifies each composite with the NDVI baseline, uploads the COG and its
  sidecar to `peatwatch-data`, and upserts `classifications` rows.
- `kind=change`: `pipeline_step` is a no-op; `inference_step` runs change detection on the two
  classifications, uploads the change COG, sidecar and stats CSV, and upserts the `changes` row.

Products are downloaded to a temporary directory and read locally (simple and reliable with
LocalStack); keys follow contracts/cog-layout.md section 3, rows follow db-schema.md section 6.
Each step appends its structured log to `peatwatch-artifacts/runs/<run_id>/log.jsonl`.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import rasterio
from botocore.exceptions import ClientError
from ml.baseline_ndvi import NdviRules
from ml.change import detect_change
from ml.infer import ndvi_baseline_model, predict_cog
from ml.stats import PIXEL_AREA_HA, build_stats, load_classes, stats_csv
from pipeline.config import Buckets, DateWindow, RunConfig
from pipeline.log import RunLogger
from pipeline.output import (
    SceneNotBoaCorrectedError,
    git_sha,
    idempotency_key,
    sidecar_key,
    validate_sidecar,
)
from pipeline.runner import RunResult, run_pipeline
from pipeline.stac import NoUsableScenesError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from api.db.models import Change, Classification, Composite
from api.settings import Settings
from api.worker import StepContext, StepError

logger = logging.getLogger(__name__)

BASELINE_NAME = "ndvi-baseline"
BASELINE_VERSION = "1.0.0"
CLASSES_CONTRACT = 1
BANDS_CONTRACT = 1
NODATA = 255
DEFAULT_CLOUD_PCT = 40.0
INFER_WINDOW = 512
INFER_OVERLAP = 128
CHANGE_WINDOW = 1024
COG_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"
NOT_FOUND = {"404", "NoSuchKey", "NotFound"}
CODE_VERSION_ENV = "PEATWATCH_CODE_VERSION"
_HEX = re.compile(r"^[0-9a-f]{7,40}$")

SessionFactory = Callable[[], AbstractContextManager[Session]]
Runner = Callable[..., RunResult]


# ---- small helpers ------------------------------------------------------------------------------


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """`s3://bucket/key` -> (bucket, key)."""
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3:// URI: {uri}")
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    if not bucket or not key:
        raise ValueError(f"not an s3://bucket/key URI: {uri}")
    return bucket, key


def model_tag(name: str, version: str) -> str:
    return f"{name}-{version}"


def code_version() -> str:
    """Commit sha for sidecars: env `PEATWATCH_CODE_VERSION`, else git, else 0000000."""
    value = (os.environ.get(CODE_VERSION_ENV) or "").strip().lower() or git_sha()
    return value if _HEX.match(value) else "0000000"


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "0.0.0"


def baseline_parameters() -> dict[str, Any]:
    """Resolved parameters of the NDVI baseline inference (hashed into the product key)."""
    return {
        "model": model_tag(BASELINE_NAME, BASELINE_VERSION),
        "rules": asdict(NdviRules()),
        "window_size": INFER_WINDOW,
        "overlap": INFER_OVERLAP,
    }


def change_parameters() -> dict[str, Any]:
    return {"encoding": "from*16+to", "window_size": CHANGE_WINDOW}


def value_counts(path: Path) -> npt.NDArray[np.int64]:
    """Pixel count per uint8 value (256 bins) of band 1, read block by block."""
    counts = np.zeros(256, dtype=np.int64)
    with rasterio.open(path) as src:
        for _, win in src.block_windows(1):
            counts += np.bincount(src.read(1, window=win).ravel(), minlength=256)[:256]
    return counts


def class_areas_ha(counts: npt.NDArray[np.int64]) -> dict[str, float]:
    """`{"<class_id>": hectares}` for every class in classes.yaml (nodata excluded)."""
    return {str(c["id"]): round(int(counts[c["id"]]) * PIXEL_AREA_HA, 2) for c in load_classes()}


def valid_fraction(counts: npt.NDArray[np.int64]) -> float:
    total = int(counts.sum())
    return float((total - int(counts[NODATA])) / total) if total else 0.0


def change_matrix(counts: npt.NDArray[np.int64]) -> npt.NDArray[np.int64]:
    """[from, to] pixel counts from change-COG value counts (v = from * 16 + to)."""
    matrix = counts.reshape(16, 16).copy()
    matrix[NODATA // 16, NODATA % 16] = 0
    return matrix


def db_change_stats(stats: Mapping[str, Any]) -> dict[str, Any]:
    """ml.stats ChangeStats dict -> the `changes.stats` jsonb shape (db-schema.md)."""
    return {
        "pixel_area_ha": stats["pixel_area_ha"],
        "total_ha": stats["total_ha"],
        "transitions": [
            {
                "from_class_id": t["from_class"]["id"],
                "to_class_id": t["to_class"]["id"],
                "area_ha": t["area_ha"],
                "percent": t["percent"],
            }
            for t in stats["transitions"]
        ],
        "class_areas_ha": {
            side: {str(a["class"]["id"]): a["area_ha"] for a in stats[f"{side}_class_areas"]}
            for side in ("from", "to")
        },
    }


def _created(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def s3_client_from_settings(settings: Settings) -> Any:
    """boto3 S3 client for the configured endpoint (path-style for LocalStack)."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url or None,
        config=Config(s3={"addressing_style": "path"}) if settings.aws_endpoint_url else None,
    )


# ---- the steps ----------------------------------------------------------------------------------


class WorkerSteps:
    """Step callables for `api.worker.Worker`; `s3` is a boto3 S3 client (or a fake)."""

    def __init__(
        self,
        session_factory: SessionFactory,
        s3: Any,
        *,
        data_bucket: str = "peatwatch-data",
        artifacts_bucket: str = "peatwatch-artifacts",
        runner: Runner = run_pipeline,
        workdir: Path | None = None,
        code: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.s3 = s3
        self.data_bucket = data_bucket
        self.artifacts_bucket = artifacts_bucket
        self.runner = runner
        self.workdir = workdir
        self.code = code or code_version()

    # ---- dispatch ------------------------------------------------------------------------------

    def pipeline_step(self, ctx: StepContext) -> Mapping[str, Any]:
        if ctx.message["kind"] == "change":
            return {}
        return self.composites(ctx)

    def inference_step(self, ctx: StepContext) -> Mapping[str, Any]:
        if ctx.message["kind"] == "change":
            return self.change(ctx)
        return self.classify(ctx)

    # ---- S3 ------------------------------------------------------------------------------------

    def _get(self, bucket: str, key: str) -> bytes | None:
        try:
            body: bytes = self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code", "")) in NOT_FOUND:
                return None
            raise
        return body

    def _download(self, uri: str, dest: Path) -> Path:
        bucket, key = parse_s3_uri(uri)
        try:
            self.s3.download_file(bucket, key, str(dest))
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code", "")) in NOT_FOUND:
                raise StepError(
                    "step_failed", f"A stored product is missing: {uri}.", {"uri": uri}
                ) from exc
            raise
        return dest

    def _sidecar(self, tif_key: str) -> dict[str, Any] | None:
        raw = self._get(self.data_bucket, sidecar_key(tif_key))
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def _upload_product(self, tif: Path, sidecar: Mapping[str, Any]) -> None:
        """COG first, sidecar last (cog-layout.md section 6 rule 2)."""
        key = str(sidecar["key"])
        self.s3.upload_file(str(tif), self.data_bucket, key, ExtraArgs={"ContentType": COG_TYPE})
        self.s3.put_object(
            Bucket=self.data_bucket,
            Key=sidecar_key(key),
            Body=(json.dumps(sidecar, indent=2, sort_keys=True) + "\n").encode(),
            ContentType="application/json",
        )

    @contextlib.contextmanager
    def _scratch(self, ctx: StepContext, step: str) -> Iterator[tuple[Path, RunLogger]]:
        """Temp dir plus a run logger whose lines are appended to the S3 run log on exit."""
        with tempfile.TemporaryDirectory(dir=self.workdir) as tmp:
            root = Path(tmp)
            log = RunLogger(root / "log.jsonl", str(ctx.run_id))
            log.event("step_start", step=step, kind=ctx.message["kind"])
            try:
                yield root, log
            except StepError as exc:
                log.event("step_failed", step=step, code=exc.code, error=exc.message)
                raise
            except Exception as exc:
                log.event("step_crashed", step=step, error=f"{type(exc).__name__}: {exc}")
                raise
            else:
                log.event("step_done", step=step)
            finally:
                self._append_log(ctx.run_id, log.path)

    def _append_log(self, run_id: uuid.UUID, path: Path) -> None:
        key = f"runs/{run_id}/log.jsonl"
        try:
            new = path.read_bytes() if path.is_file() else b""
            old = self._get(self.artifacts_bucket, key) or b""
            self.s3.put_object(
                Bucket=self.artifacts_bucket,
                Key=key,
                Body=old + new,
                ContentType="application/x-ndjson",
            )
        except Exception:  # the run log is best effort; never fail a step over it
            logger.exception("could not append run log for %s", run_id)

    # ---- DB lookups ----------------------------------------------------------------------------

    def _site(self, site_id: uuid.UUID) -> Any:
        with self.session_factory() as session:
            row = session.execute(
                text("SELECT id, slug, boundary_uri, boundary_sha256 FROM sites WHERE id = :id"),
                {"id": site_id},
            ).one()
            session.commit()
            return row

    def _run_model(self, run_id: uuid.UUID) -> Any:
        with self.session_factory() as session:
            row = session.execute(
                text(
                    "SELECT m.id, m.name, m.version FROM runs r JOIN models m ON m.id = r.model_id "
                    "WHERE r.id = :id"
                ),
                {"id": run_id},
            ).one()
            session.commit()
            return row

    # ---- step 1: composites --------------------------------------------------------------------

    def _boundary(self, site: Any, dest: Path) -> Path:
        bucket, key = parse_s3_uri(site.boundary_uri)
        raw = self._get(bucket, key)
        if raw is None:
            raise StepError(
                "step_failed",
                "The site boundary is missing from storage. Upload the site again.",
                {"boundary_uri": site.boundary_uri},
            )
        digest = hashlib.sha256(raw).hexdigest()
        if digest != str(site.boundary_sha256).strip():
            raise StepError(
                "step_failed",
                "The stored site boundary does not match its checksum. Upload the site again.",
                {"boundary_uri": site.boundary_uri, "expected": site.boundary_sha256},
            )
        dest.write_bytes(raw)
        return dest

    def composites(self, ctx: StepContext) -> Mapping[str, Any]:
        site = self._site(ctx.site_id)
        params = ctx.message.get("params") or {}
        cloud = float(params.get("max_cloud_cover_pct", DEFAULT_CLOUD_PCT))
        package = f"pipeline {_package_version('peatwatch-pipeline')}"
        ids: list[str] = []
        with self._scratch(ctx, "pipeline") as (root, log):
            boundary = self._boundary(site, root / "boundary.geojson")
            for window in ctx.message["windows"]:
                cfg = RunConfig(
                    site=site.slug,
                    boundary_path=boundary,
                    window=DateWindow(start=window["start"], end=window["end"]),
                    cloud_threshold=cloud,
                    output_dir=root / "out",
                    buckets=Buckets(data=self.data_bucket, artifacts=self.artifacts_bucket),
                )
                try:
                    result = self.runner(cfg, log, package=package, s3=self.s3)
                except NoUsableScenesError as exc:
                    raise StepError(
                        "no_usable_scenes",
                        f"No usable Sentinel-2 scenes between {window['start']} and "
                        f"{window['end']} at or below {cloud:g}% cloud. Try a longer date "
                        "window or a higher cloud threshold.",
                        {"window": dict(window), "max_cloud_cover_pct": cloud},
                    ) from exc
                except SceneNotBoaCorrectedError as exc:
                    raise StepError(
                        "step_failed",
                        f"Scenes for {window['start']}..{window['end']} are not BOA-corrected.",
                        {"window": dict(window), "reason": str(exc)},
                    ) from exc
                sidecar = self._sidecar(result.key)
                if sidecar is None:
                    raise RuntimeError(f"composite sidecar missing after run: {result.key}")
                if sidecar.get("season") != window["season"]:
                    logger.warning(
                        "composite stored as %s, requested season %s (pipeline writes custom "
                        "window keys only)",
                        sidecar.get("season"),
                        window["season"],
                    )
                ids.append(str(self._upsert_composite(ctx, site.id, sidecar)))
        return {"composite_ids": ids}

    def _upsert_composite(
        self, ctx: StepContext, site_id: uuid.UUID, sidecar: Mapping[str, Any]
    ) -> uuid.UUID:
        versions = sidecar["versions"]
        code = str(versions["code"])[:7]
        package = versions.get("package")
        values: dict[str, Any] = {
            "site_id": site_id,
            "year": int(sidecar["year"]),
            "season": sidecar["season"],
            "date_start": dt.date.fromisoformat(sidecar["date_range"]["start"]),
            "date_end": dt.date.fromisoformat(sidecar["date_range"]["end"]),
            "cog_uri": f"s3://{self.data_bucket}/{sidecar['key']}",
            "pipeline_version": f"{package} ({code})" if package else code,
            "scene_ids": list(sidecar["scene_ids"]),
            "cloud_stats": dict(sidecar["cloud_stats"]),
            "parameters": dict(sidecar["parameters"]),
            "idempotency_key": sidecar["idempotency_key"],
            "run_id": ctx.run_id,
            "created_at": _created(sidecar["created_at"]),
        }
        natural = ("site_id", "year", "season")
        return self._upsert(Composite, values, natural)

    def _upsert(self, table: Any, values: Mapping[str, Any], natural: Sequence[str]) -> uuid.UUID:
        """Insert or replace by natural key, only when the idempotency key changed (C3 s.6)."""
        insert = pg_insert(table).values(**values)
        stmt = insert.on_conflict_do_update(
            index_elements=list(natural),
            set_={c: insert.excluded[c] for c in values if c not in natural},
            where=table.idempotency_key != insert.excluded.idempotency_key,
        ).returning(table.id)
        with self.session_factory() as session:
            new_id = session.execute(stmt).scalar()
            if new_id is None:  # row unchanged: same key already stored
                cond = [getattr(table, c) == values[c] for c in natural]
                new_id = session.execute(select(table.id).where(*cond)).scalar_one()
            session.commit()
            return uuid.UUID(str(new_id))

    # ---- step 2: classification ----------------------------------------------------------------

    def _check_model(self, ctx: StepContext) -> Any:
        model = self._run_model(ctx.run_id)
        if model.name != BASELINE_NAME:
            tag = model_tag(model.name, model.version)
            raise StepError(
                "step_failed",
                f"Model {tag} is not available in this worker yet; use "
                f"{model_tag(BASELINE_NAME, BASELINE_VERSION)}.",
                {"model": tag},
            )
        return model

    def classify(self, ctx: StepContext) -> Mapping[str, Any]:
        model = self._check_model(ctx)
        site = self._site(ctx.site_id)
        ids: list[str] = []
        with self._scratch(ctx, "inference") as (root, log):
            for cid in ctx.outputs.get("composite_ids", []):
                ids.append(str(self._classify_one(ctx, site, model, uuid.UUID(cid), root, log)))
        return {"classification_ids": ids, "change_id": None}

    def _composite(self, composite_id: uuid.UUID) -> Composite:
        with self.session_factory() as session:
            comp = session.get(Composite, composite_id)
            if comp is None:
                raise StepError(
                    "step_failed", "A composite for this run is missing.", {"id": str(composite_id)}
                )
            session.expunge(comp)
            session.commit()
            return comp

    def _classify_one(
        self,
        ctx: StepContext,
        site: Any,
        model: Any,
        composite_id: uuid.UUID,
        root: Path,
        log: RunLogger,
    ) -> uuid.UUID:
        comp = self._composite(composite_id)
        tag = model_tag(model.name, model.version)
        key = f"classifications/{site.slug}/{comp.year}/{comp.season}/{tag}.tif"
        params = baseline_parameters()
        ikey = idempotency_key(
            kind="classification",
            site=site.slug,
            boundary_sha=site.boundary_sha256,
            start=comp.date_start,
            end=comp.date_end,
            parameters=params,
            code_version=self.code,
            model=tag,
            inputs=[comp.idempotency_key],
        )
        tif = root / f"class-{comp.id}.tif"
        stored = self._sidecar(key)
        if stored is not None and stored.get("idempotency_key") == ikey:
            existing = self._id_by_key(Classification, ikey)
            if existing is not None:
                log.event("skipped", product="classification", key=key)
                return existing
            self._download(f"s3://{self.data_bucket}/{key}", tif)
            sidecar = stored
        else:
            src = self._download(comp.cog_uri, root / f"composite-{comp.id}.tif")
            predict_cog(
                src,
                tif,
                ndvi_baseline_model(),
                window_size=INFER_WINDOW,
                overlap=INFER_OVERLAP,
            )
            counts = value_counts(tif)
            cloud = {
                k: v
                for k, v in comp.cloud_stats.items()
                if k in ("scene_count", "mean_scene_cloud_pct", "min_valid_observations")
            }
            cloud.setdefault("scene_count", len(comp.scene_ids))
            cloud.setdefault("mean_scene_cloud_pct", None)
            cloud["valid_pixel_fraction"] = valid_fraction(counts)
            sidecar = self._product_sidecar(
                kind="classification",
                key=key,
                site=site.slug,
                start=comp.date_start,
                end=comp.date_end,
                year=comp.year,
                season=comp.season,
                band="class",
                scene_ids=comp.scene_ids,
                model=tag,
                parameters=params,
                cloud_stats=cloud,
                inputs=[comp.idempotency_key],
                ikey=ikey,
                run_id=ctx.run_id,
            )
            self._upload_product(tif, sidecar)
            log.event("uploaded", product="classification", key=key)
        values = {
            "composite_id": comp.id,
            "model_id": model.id,
            "cog_uri": f"s3://{self.data_bucket}/{key}",
            "class_areas_ha": class_areas_ha(value_counts(tif)),
            "idempotency_key": ikey,
            "run_id": ctx.run_id,
            "created_at": _created(sidecar["created_at"]),
        }
        return self._upsert(Classification, values, ("composite_id", "model_id"))

    def _id_by_key(self, table: Any, ikey: str) -> uuid.UUID | None:
        with self.session_factory() as session:
            value = session.execute(select(table.id).where(table.idempotency_key == ikey)).scalar()
            session.commit()
            return None if value is None else uuid.UUID(str(value))

    def _product_sidecar(
        self,
        *,
        kind: str,
        key: str,
        site: str,
        start: dt.date,
        end: dt.date,
        year: int,
        season: str,
        band: str,
        scene_ids: Sequence[str],
        model: str,
        parameters: Mapping[str, Any],
        cloud_stats: Mapping[str, Any],
        inputs: Sequence[str],
        ikey: str,
        run_id: uuid.UUID,
        change_pair: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        sidecar: dict[str, Any] = {
            "schema_version": 1,
            "kind": kind,
            "key": key,
            "site": site,
            "run_id": str(run_id),
            "date_range": {"start": start.isoformat(), "end": end.isoformat()},
            "year": year,
            "season": season,
            "crs": "EPSG:32630",
            "resolution_m": 10,
            "dtype": "uint8",
            "nodata": NODATA,
            "bands": [band],
            "scene_ids": sorted(set(scene_ids)),
            "versions": {
                "code": self.code,
                "package": f"ml {_package_version('peatwatch-ml')}",
                "model": model,
                "classes_contract": CLASSES_CONTRACT,
                "bands_contract": BANDS_CONTRACT,
            },
            "parameters": dict(parameters),
            "cloud_stats": dict(cloud_stats),
            "inputs": sorted(inputs),
            "idempotency_key": ikey,
            "created_at": _now(),
        }
        if change_pair is not None:
            sidecar["change_pair"] = dict(change_pair)
        validate_sidecar(sidecar)
        return sidecar

    # ---- step 3: change ------------------------------------------------------------------------

    def _pair(
        self, site_id: uuid.UUID, window: Mapping[str, Any], model: Any, label: str
    ) -> tuple[Composite, Classification]:
        with self.session_factory() as session:
            comp = session.scalar(
                select(Composite).where(
                    Composite.site_id == site_id,
                    Composite.year == int(window["year"]),
                    Composite.season == window["season"],
                )
            )
            cls = (
                session.scalar(
                    select(Classification).where(
                        Classification.composite_id == comp.id,
                        Classification.model_id == model.id,
                    )
                )
                if comp is not None
                else None
            )
            if comp is None or cls is None:
                tag = model_tag(model.name, model.version)
                raise StepError(
                    "classification_missing",
                    f"No {tag} classification exists for {window['year']}-{window['season']}. "
                    "Run a classification for that date first.",
                    {
                        "year": window["year"],
                        "season": window["season"],
                        "model": tag,
                        "window": label,
                    },
                )
            session.expunge(comp)
            session.expunge(cls)
            session.commit()
            return comp, cls

    def change(self, ctx: StepContext) -> Mapping[str, Any]:
        model = self._run_model(ctx.run_id)
        site = self._site(ctx.site_id)
        w_from, w_to = ctx.message["windows"]
        frm, cls_from = self._pair(site.id, w_from, model, "from")
        to, cls_to = self._pair(site.id, w_to, model, "to")
        if frm.id == to.id or (frm.date_start, frm.date_end) >= (to.date_start, to.date_end):
            raise StepError(
                "step_failed",
                "The 'from' window must start before the 'to' window.",
                {"from": dict(w_from), "to": dict(w_to)},
            )
        tag = model_tag(model.name, model.version)
        key = f"changes/{site.slug}/{frm.year}-{frm.season}__{to.year}-{to.season}/{tag}.tif"
        params = change_parameters()
        inputs = [cls_from.idempotency_key, cls_to.idempotency_key]
        ikey = idempotency_key(
            kind="change",
            site=site.slug,
            boundary_sha=site.boundary_sha256,
            start=frm.date_start,
            end=to.date_end,
            parameters=params,
            code_version=self.code,
            model=tag,
            inputs=inputs,
        )
        with self._scratch(ctx, "change") as (root, log):
            tif = root / "change.tif"
            stored = self._sidecar(key)
            if stored is not None and stored.get("idempotency_key") == ikey:
                existing = self._id_by_key(Change, ikey)
                if existing is not None:
                    log.event("skipped", product="change", key=key)
                    return self._change_outputs(existing)
                self._download(f"s3://{self.data_bucket}/{key}", tif)
                ml_stats = build_stats(change_matrix(value_counts(tif)))
                sidecar = stored
            else:
                a = self._download(cls_from.cog_uri, root / "from.tif")
                b = self._download(cls_to.cog_uri, root / "to.tif")
                ml_stats = detect_change(a, b, tif, window_size=CHANGE_WINDOW)
                counts = value_counts(tif)
                sidecar = self._product_sidecar(
                    kind="change",
                    key=key,
                    site=site.slug,
                    start=frm.date_start,
                    end=to.date_end,
                    year=to.year,
                    season=to.season,
                    band="change",
                    scene_ids=[*frm.scene_ids, *to.scene_ids],
                    model=tag,
                    parameters=params,
                    cloud_stats={
                        "scene_count": len(set(frm.scene_ids) | set(to.scene_ids)),
                        "mean_scene_cloud_pct": None,
                        "valid_pixel_fraction": valid_fraction(counts),
                    },
                    inputs=inputs,
                    ikey=ikey,
                    run_id=ctx.run_id,
                    change_pair={
                        "from": {"year": frm.year, "season": frm.season},
                        "to": {"year": to.year, "season": to.season},
                    },
                )
                self._upload_product(tif, sidecar)
                log.event("uploaded", product="change", key=key)
            csv_key = f"exports/{ctx.run_id}/stats.csv"
            self.s3.put_object(
                Bucket=self.artifacts_bucket,
                Key=csv_key,
                Body=stats_csv(ml_stats).encode(),
                ContentType="text/csv",
            )
            values = {
                "site_id": site.id,
                "from_composite_id": frm.id,
                "to_composite_id": to.id,
                "from_classification_id": cls_from.id,
                "to_classification_id": cls_to.id,
                "model_id": model.id,
                "cog_uri": f"s3://{self.data_bucket}/{key}",
                "stats": db_change_stats(ml_stats),
                "stats_csv_uri": f"s3://{self.artifacts_bucket}/{csv_key}",
                "idempotency_key": ikey,
                "run_id": ctx.run_id,
                "created_at": _created(sidecar["created_at"]),
            }
            change_id = self._upsert(
                Change, values, ("from_composite_id", "to_composite_id", "model_id")
            )
        return self._change_outputs(change_id)

    @staticmethod
    def _change_outputs(change_id: uuid.UUID) -> dict[str, Any]:
        return {"composite_ids": [], "classification_ids": [], "change_id": str(change_id)}

    # ---- model registry ------------------------------------------------------------------------

    def register_baseline(self) -> uuid.UUID:
        """Ensure the `ndvi-baseline` model row exists (default when no default is set).

        Uploads a model card with the rule thresholds. There is no weights file, so
        `artifact_uri` points at the model card as well.
        """
        prefix = f"models/{BASELINE_NAME}/{BASELINE_VERSION}"
        card_uri = f"s3://{self.artifacts_bucket}/{prefix}/model-card.json"
        card = {
            "name": BASELINE_NAME,
            "version": BASELINE_VERSION,
            "kind": "rule-based",
            "description": "NDVI/NDWI/NDMI/elevation thresholds (ml.baseline_ndvi).",
            "parameters": baseline_parameters(),
            "classes_contract": CLASSES_CONTRACT,
        }
        try:
            self.s3.put_object(
                Bucket=self.artifacts_bucket,
                Key=f"{prefix}/model-card.json",
                Body=json.dumps(card, indent=2, sort_keys=True).encode(),
                ContentType="application/json",
            )
        except Exception:
            logger.exception("could not upload the baseline model card")
        with self.session_factory() as session:
            session.execute(
                text(
                    "INSERT INTO models (name, version, metrics, artifact_uri, model_card_uri, "
                    "git_commit, classes_contract, is_default) "
                    "SELECT :name, :version, '{}'::jsonb, :uri, :uri, :commit, :classes, "
                    "NOT EXISTS (SELECT 1 FROM models WHERE is_default) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "name": BASELINE_NAME,
                    "version": BASELINE_VERSION,
                    "uri": card_uri,
                    "commit": self.code,
                    "classes": CLASSES_CONTRACT,
                },
            )
            model_id = session.execute(
                text("SELECT id FROM models WHERE name = :name AND version = :version"),
                {"name": BASELINE_NAME, "version": BASELINE_VERSION},
            ).scalar_one()
            session.commit()
            return uuid.UUID(str(model_id))
