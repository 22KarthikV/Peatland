"""Real worker steps (api.steps) with a fake S3, a fake pipeline runner and tiny synthetic COGs.

DB tests use the PostGIS fixtures from test_db (skipped without PostGIS). The `localstack`
test repeats the flow against LocalStack S3 (RUN_LOCALSTACK=1); no test touches the network.
"""
# ruff: noqa: F811  (pytest fixtures imported from test_db are redefined as parameters)

from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import rasterio
from api.db.models import Run, Site
from api.queue import InMemoryJobQueue
from api.settings import Settings
from api.steps import (
    BASELINE_NAME,
    BASELINE_VERSION,
    WorkerSteps,
    change_matrix,
    class_areas_ha,
    code_version,
    db_change_stats,
    parse_s3_uri,
    s3_client_from_settings,
    valid_fraction,
)
from api.worker import StepContext, StepError, Worker, load_validator
from botocore.exceptions import ClientError
from ml.stats import build_stats
from pipeline.config import RunConfig
from pipeline.grid import Grid
from pipeline.log import RunLogger
from pipeline.output import (
    band_order,
    boundary_sha256,
    build_sidecar,
    composite_key,
    validate_sidecar,
    write_composite_cog,
    write_sidecar,
)
from pipeline.runner import RunResult
from pipeline.stac import NoUsableScenesError
from pipeline.store import upload_product
from rasterio.transform import from_origin
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_db import conn, db_url, engine, make_model, make_site, session  # noqa: F401

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"
DATA = "peatwatch-data"
ARTIFACTS = "peatwatch-artifacts"
H, W = 20, 30
W_A = {"year": 2025, "season": "w20250319-20250319", "start": "2025-03-19", "end": "2025-03-19"}
W_B = {"year": 2025, "season": "w20250408-20250408", "start": "2025-04-08", "end": "2025-04-08"}
RING = [[-4.4, 55.1], [-4.39, 55.1], [-4.39, 55.11], [-4.4, 55.1]]
BOUNDARY = json.dumps({"type": "Polygon", "coordinates": [RING]}).encode()


# ---- fakes ------------------------------------------------------------------------------------


def _missing(op: str) -> ClientError:
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, op)


class FakeS3:
    """The boto3 S3 calls used by api.steps and pipeline.store, kept in a dict."""

    exceptions = SimpleNamespace(ClientError=ClientError)

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.writes: list[str] = []

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise _missing("HeadObject")
        return {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket: str, Key: str, Body: bytes, **_: Any) -> None:  # noqa: N803
        self.objects[(Bucket, Key)] = bytes(Body)
        self.writes.append(Key)

    def upload_file(self, Filename: str, Bucket: str, Key: str, **_: Any) -> None:  # noqa: N803
        self.objects[(Bucket, Key)] = Path(Filename).read_bytes()
        self.writes.append(Key)

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise _missing("HeadObject")
        Path(Filename).write_bytes(self.objects[(Bucket, Key)])

    def json(self, bucket: str, key: str) -> Any:
        return json.loads(self.objects[(bucket, key)])


def composite_array(later: bool) -> np.ndarray:
    """18 bands; left half woodland, right half bog (earlier) or bare (later); row 0 nodata."""
    a = np.full((18, H, W), 0.1, dtype=np.float32)
    a[12, :, : W // 2] = 0.7  # ndvi: woodland
    a[12, :, W // 2 :] = 0.1 if later else 0.4  # bare / mid
    a[13] = -0.2  # ndwi: not water
    a[14] = 0.1  # ndmi: wet
    a[15] = 100.0  # elevation
    a[:, 0, :] = np.nan
    return a


class FakeRunner:
    """Stands in for pipeline.runner.run_pipeline: writes a synthetic composite to the store."""

    def __init__(self, arrays: Mapping[str, np.ndarray]) -> None:
        self.arrays = arrays
        self.calls: list[str] = []

    def __call__(
        self, cfg: RunConfig, logger: RunLogger, *, package: str | None, s3: Any
    ) -> RunResult:
        start, end = cfg.window.start, cfg.window.end
        self.calls.append(start.isoformat())
        if start.isoformat() not in self.arrays:
            raise NoUsableScenesError("zero scenes")
        key = composite_key(cfg.site, start, end)
        scene = f"S2B_30UVG_{start:%Y%m%d}_0_L2A"
        sidecar = build_sidecar(
            site=cfg.site,
            start=start,
            end=end,
            key=key,
            bands=band_order(),
            scene_ids=[scene],
            scene_boa_offset_applied={scene: True},
            parameters={"cloud_threshold": cfg.cloud_threshold, "composite": "median"},
            boundary_sha=boundary_sha256(cfg.boundary_path),
            cloud_stats={
                "scene_count": 1,
                "mean_scene_cloud_pct": 5.0,
                "valid_pixel_fraction": 0.95,
                "min_valid_observations": 1,
            },
            code_version="abcdef1",
            package=package,
            run_id=logger.run_id,
        )
        grid = Grid(transform=from_origin(500000, 6100000, 10, 10), width=W, height=H)
        tif = write_composite_cog(cfg.output_dir / key, self.arrays[start.isoformat()], grid)
        side = write_sidecar(cfg.output_dir / (key[:-4] + ".json"), sidecar)
        outcome = upload_product(tif, side, cfg.buckets.data, client=s3)
        return RunResult(outcome, key, [scene], tif, side)


# ---- pure helpers -------------------------------------------------------------------------------


def test_parse_s3_uri() -> None:
    assert parse_s3_uri("s3://b/k/x.tif") == ("b", "k/x.tif")
    for bad in ("http://b/k", "s3://b", "s3:///k"):
        with pytest.raises(ValueError):
            parse_s3_uri(bad)


def test_code_version_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEATWATCH_CODE_VERSION", "ABCDEF1")
    assert code_version() == "abcdef1"
    monkeypatch.setenv("PEATWATCH_CODE_VERSION", "not-a-sha")
    assert code_version() == "0000000"


def test_counts_to_stats() -> None:
    counts = np.zeros(256, dtype=np.int64)
    counts[2 * 16 + 2] = 300  # woodland unchanged
    counts[1 * 16 + 3] = 100  # bog -> bare
    counts[255] = 100  # nodata
    matrix = change_matrix(counts)
    assert matrix[2, 2] == 300 and matrix[1, 3] == 100 and matrix.sum() == 400
    stats = db_change_stats(build_stats(matrix))
    assert stats["total_ha"] == 4.0 and stats["pixel_area_ha"] == 0.01
    assert {(t["from_class_id"], t["to_class_id"]) for t in stats["transitions"]} == {
        (2, 2),
        (1, 3),
    }
    assert stats["class_areas_ha"]["from"] == {"0": 0.0, "1": 1.0, "2": 3.0, "3": 0.0}
    assert stats["class_areas_ha"]["to"] == {"0": 0.0, "1": 0.0, "2": 3.0, "3": 1.0}
    assert valid_fraction(counts) == 0.8
    assert class_areas_ha(counts)["0"] == 0.0


def test_s3_client_from_settings_uses_endpoint() -> None:
    client = s3_client_from_settings(
        Settings(aws_endpoint_url="http://localhost:4566", aws_region="us-east-1")
    )
    assert client.meta.endpoint_url == "http://localhost:4566"


# ---- DB-backed steps ----------------------------------------------------------------------------


@pytest.fixture
def s3() -> FakeS3:
    return FakeS3()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner({"2025-03-19": composite_array(False), "2025-04-08": composite_array(True)})


def _factory(session: Session) -> Any:  # noqa: F811
    @contextmanager
    def factory() -> Iterator[Session]:
        yield session

    return factory


@pytest.fixture
def steps(
    session: Session,  # noqa: F811
    s3: FakeS3,
    runner: FakeRunner,
    tmp_path: Path,
) -> WorkerSteps:
    return WorkerSteps(_factory(session), s3, runner=runner, workdir=tmp_path, code="1234567")


def stored_site(session: Session, store: Any) -> Site:  # noqa: F811
    site = make_site(session)
    site.boundary_sha256 = hashlib.sha256(BOUNDARY).hexdigest()
    session.flush()
    bucket, key = parse_s3_uri(site.boundary_uri)
    store.put_object(Bucket=bucket, Key=key, Body=BOUNDARY)
    return site


def new_run(session: Session, site: Site, model_id: uuid.UUID, kind: str = "pipeline") -> Run:  # noqa: F811
    run = Run(
        site_id=site.id,
        kind=kind,
        model_id=model_id,
        params={},
        idempotency_key=uuid.uuid4().hex + uuid.uuid4().hex,
    )
    session.add(run)
    session.flush()
    return run


def message(run: Run, site: Site, kind: str, windows: list[dict[str, Any]]) -> dict[str, Any]:
    msg: dict[str, Any] = json.loads((FIXTURES / f"queue.{kind}.json").read_text())
    msg.update(
        run_id=str(run.id),
        site={"id": str(site.id), "slug": site.slug},
        windows=windows,
        model={"name": BASELINE_NAME, "version": BASELINE_VERSION},
        idempotency_key=run.idempotency_key,
    )
    return msg


def ctx(run: Run, msg: dict[str, Any], outputs: Mapping[str, Any] | None = None) -> StepContext:
    return StepContext(run.id, run.site_id, msg, dict(outputs or {}))


def run_pipeline_kind(
    steps: WorkerSteps,
    session: Session,
    site: Site,
    model_id: uuid.UUID,  # noqa: F811
) -> tuple[Run, dict[str, Any]]:
    run = new_run(session, site, model_id)
    msg = message(run, site, "pipeline", [W_A, W_B])
    out = dict(steps.pipeline_step(ctx(run, msg)))
    out.update(steps.inference_step(ctx(run, msg, out)))
    return run, out


def run_change_kind(
    steps: WorkerSteps,
    session: Session,
    site: Site,
    model_id: uuid.UUID,  # noqa: F811
) -> tuple[Run, dict[str, Any]]:
    run = new_run(session, site, model_id, "change")
    msg = message(run, site, "change", [W_A, W_B])
    out = dict(steps.pipeline_step(ctx(run, msg)))
    assert out == {}
    out.update(steps.inference_step(ctx(run, msg, out)))
    return run, out


def test_register_baseline_is_idempotent_and_default(
    steps: WorkerSteps,
    session: Session,
    s3: FakeS3,  # noqa: F811
) -> None:
    has_default = session.execute(text("SELECT count(*) FROM models WHERE is_default")).scalar()
    first = steps.register_baseline()
    assert steps.register_baseline() == first
    row = session.execute(
        text("SELECT name, version, is_default, artifact_uri FROM models WHERE id = :id"),
        {"id": first},
    ).one()
    assert (row.name, row.version) == (BASELINE_NAME, BASELINE_VERSION)
    assert row.is_default is (has_default == 0)
    card = s3.json(ARTIFACTS, f"models/{BASELINE_NAME}/{BASELINE_VERSION}/model-card.json")
    assert card["parameters"]["model"] == "ndvi-baseline-1.0.0"


def test_full_flow_composite_classify_change_and_rerun(
    steps: WorkerSteps,
    session: Session,  # noqa: F811
    s3: FakeS3,
    runner: FakeRunner,
) -> None:
    model_id = steps.register_baseline()
    site = stored_site(session, s3)
    run, out = run_pipeline_kind(steps, session, site, model_id)
    assert len(out["composite_ids"]) == 2 and len(out["classification_ids"]) == 2
    assert out["change_id"] is None

    comps = session.execute(
        text("SELECT season, cog_uri, run_id FROM composites WHERE site_id = :s ORDER BY season"),
        {"s": site.id},
    ).all()
    assert [c.season for c in comps] == [W_A["season"], W_B["season"]]
    assert comps[0].cog_uri == f"s3://{DATA}/composites/{site.slug}/2025/{W_A['season']}.tif"
    assert all(c.run_id == run.id for c in comps)

    cls_key = f"classifications/{site.slug}/2025/{W_A['season']}/ndvi-baseline-1.0.0"
    sidecar = s3.json(DATA, cls_key + ".json")
    validate_sidecar(sidecar)
    comp_sidecar = s3.json(DATA, parse_s3_uri(comps[0].cog_uri)[1][:-4] + ".json")
    assert sidecar["inputs"] == [comp_sidecar["idempotency_key"]]
    assert sidecar["cloud_stats"]["valid_pixel_fraction"] == pytest.approx(19 / 20)
    areas = session.execute(
        text(
            "SELECT k.class_areas_ha, k.cog_uri FROM classifications k "
            "JOIN composites c ON c.id = k.composite_id WHERE c.site_id = :s ORDER BY c.season"
        ),
        {"s": site.id},
    ).all()
    assert areas[0].cog_uri == f"s3://{DATA}/{cls_key}.tif"
    assert areas[0].class_areas_ha == {"0": 0.0, "1": 2.85, "2": 2.85, "3": 0.0}
    assert areas[1].class_areas_ha == {"0": 0.0, "1": 0.0, "2": 2.85, "3": 2.85}
    with rasterio.open(io.BytesIO(s3.objects[(DATA, cls_key + ".tif")])) as src:
        assert src.dtypes[0] == "uint8" and src.nodata == 255
        assert src.descriptions[0] == "class" and src.colormap(1)[1][:3] == (0, 0x72, 0xB2)
    log = s3.objects[(ARTIFACTS, f"runs/{run.id}/log.jsonl")].decode().splitlines()
    assert [json.loads(line)["event"] for line in log][-1] == "step_done"

    change_run, change_out = run_change_kind(steps, session, site, model_id)
    assert change_out["composite_ids"] == [] and change_out["change_id"]
    row = session.execute(
        text("SELECT * FROM changes WHERE id = :id"), {"id": change_out["change_id"]}
    ).one()
    pair = f"2025-{W_A['season']}__2025-{W_B['season']}"
    assert row.cog_uri == f"s3://{DATA}/changes/{site.slug}/{pair}/ndvi-baseline-1.0.0.tif"
    assert row.stats_csv_uri == f"s3://{ARTIFACTS}/exports/{change_run.id}/stats.csv"
    assert row.stats["total_ha"] == 5.7
    trans = {(t["from_class_id"], t["to_class_id"]): t for t in row.stats["transitions"]}
    assert set(trans) == {(2, 2), (1, 3)}
    assert trans[(1, 3)]["area_ha"] == 2.85 and trans[(1, 3)]["percent"] == 50.0
    assert row.stats["class_areas_ha"]["to"]["3"] == 2.85
    change_sidecar = s3.json(DATA, row.cog_uri[len(f"s3://{DATA}/") : -4] + ".json")
    validate_sidecar(change_sidecar)
    assert change_sidecar["change_pair"]["from"] == {"year": 2025, "season": W_A["season"]}
    csv = s3.objects[(ARTIFACTS, f"exports/{change_run.id}/stats.csv")].decode()
    assert csv.splitlines()[0].startswith("from_class_id,from_class")

    # Rerun: every product is skipped by its idempotency key; rows and ids are reused.
    writes = [k for k in s3.writes if not k.startswith(("runs/", "exports/"))]
    _, again = run_pipeline_kind(steps, session, site, model_id)
    assert again["composite_ids"] == out["composite_ids"]
    assert again["classification_ids"] == out["classification_ids"]
    _, change_again = run_change_kind(steps, session, site, model_id)
    assert change_again["change_id"] == change_out["change_id"]
    assert [k for k in s3.writes if not k.startswith(("runs/", "exports/"))] == writes


def test_rows_rebuilt_from_stored_products(
    steps: WorkerSteps,
    session: Session,
    s3: FakeS3,  # noqa: F811
) -> None:
    """Sidecar key matches but the DB row is gone: the row is rebuilt from the stored COG."""
    model_id = steps.register_baseline()
    site = stored_site(session, s3)
    _, out = run_pipeline_kind(steps, session, site, model_id)
    _, change_out = run_change_kind(steps, session, site, model_id)
    before = session.execute(
        text("SELECT stats FROM changes WHERE id = :id"), {"id": change_out["change_id"]}
    ).scalar()
    session.execute(text("DELETE FROM changes WHERE id = :id"), {"id": change_out["change_id"]})
    session.execute(
        text("DELETE FROM classifications WHERE id = ANY(:ids)"),
        {"ids": [uuid.UUID(i) for i in out["classification_ids"]]},
    )
    writes = len(s3.writes)
    _, out2 = run_pipeline_kind(steps, session, site, model_id)
    _, change2 = run_change_kind(steps, session, site, model_id)
    assert len(out2["classification_ids"]) == 2
    after = session.execute(
        text("SELECT stats FROM changes WHERE id = :id"), {"id": change2["change_id"]}
    ).scalar()
    assert after == before
    data_writes = [k for k in s3.writes[writes:] if k.endswith((".tif", ".json"))]
    assert data_writes == []


def test_no_usable_scenes(
    steps: WorkerSteps,
    session: Session,
    s3: FakeS3,  # noqa: F811
) -> None:
    model_id = steps.register_baseline()
    site = stored_site(session, s3)
    run = new_run(session, site, model_id)
    window = {"year": 2024, "season": "summer", "start": "2024-06-01", "end": "2024-08-31"}
    with pytest.raises(StepError) as err:
        steps.pipeline_step(ctx(run, message(run, site, "pipeline", [window])))
    assert err.value.code == "no_usable_scenes"
    assert err.value.detail["window"] == window
    log = s3.objects[(ARTIFACTS, f"runs/{run.id}/log.jsonl")].decode()
    assert '"step_failed"' in log


def test_boundary_checksum_mismatch_and_missing(
    steps: WorkerSteps,
    session: Session,
    s3: FakeS3,  # noqa: F811
) -> None:
    model_id = steps.register_baseline()
    site = stored_site(session, s3)
    run = new_run(session, site, model_id)
    bucket, key = parse_s3_uri(site.boundary_uri)
    s3.objects[(bucket, key)] = b"tampered"
    with pytest.raises(StepError, match="checksum"):
        steps.pipeline_step(ctx(run, message(run, site, "pipeline", [W_A])))
    del s3.objects[(bucket, key)]
    with pytest.raises(StepError, match="missing"):
        steps.pipeline_step(ctx(run, message(run, site, "pipeline", [W_A])))


def test_other_model_is_refused(
    steps: WorkerSteps,
    session: Session,
    s3: FakeS3,  # noqa: F811
) -> None:
    other = make_model(session, "unet", "1.0.0")
    site = stored_site(session, s3)
    run = new_run(session, site, other.id)
    with pytest.raises(StepError) as err:
        steps.inference_step(ctx(run, message(run, site, "pipeline", [W_A]), {"composite_ids": []}))
    assert err.value.code == "step_failed" and err.value.detail == {"model": "unet-1.0.0"}


def test_change_without_classification(
    steps: WorkerSteps,
    session: Session,
    s3: FakeS3,  # noqa: F811
) -> None:
    model_id = steps.register_baseline()
    site = stored_site(session, s3)
    run = new_run(session, site, model_id, "change")
    with pytest.raises(StepError) as err:
        steps.inference_step(ctx(run, message(run, site, "change", [W_A, W_B])))
    assert err.value.code == "classification_missing"
    assert err.value.detail["window"] == "from"


def test_change_windows_out_of_order(
    steps: WorkerSteps,
    session: Session,
    s3: FakeS3,  # noqa: F811
) -> None:
    model_id = steps.register_baseline()
    site = stored_site(session, s3)
    run_pipeline_kind(steps, session, site, model_id)
    run = new_run(session, site, model_id, "change")
    with pytest.raises(StepError, match="must start before"):
        steps.inference_step(ctx(run, message(run, site, "change", [W_B, W_A])))


def test_worker_end_to_end_with_real_steps(
    steps: WorkerSteps,
    session: Session,
    s3: FakeS3,  # noqa: F811
) -> None:
    model_id = steps.register_baseline()
    site = stored_site(session, s3)
    queue = InMemoryJobQueue()
    worker = Worker(
        queue,
        _factory(session),
        steps.pipeline_step,
        steps.inference_step,
        validator=load_validator(),
        heartbeat_interval=3600,
        wait_seconds=0,
    )
    run = new_run(session, site, model_id)
    queue.send(message(run, site, "pipeline", [W_A, W_B]))
    worker.poll_once()
    change = new_run(session, site, model_id, "change")
    queue.send(message(change, site, "change", [W_A, W_B]))
    worker.poll_once()
    rows = {
        r.id: r
        for r in session.execute(
            text("SELECT id, status, outputs, error FROM runs WHERE id = ANY(:ids)"),
            {"ids": [run.id, change.id]},
        )
    }
    assert rows[run.id].status == "succeeded", rows[run.id].error
    assert set(rows[run.id].outputs) == {"composite_ids", "classification_ids", "change_id"}
    assert rows[change.id].status == "succeeded", rows[change.id].error
    assert rows[change.id].outputs["change_id"] is not None


# ---- LocalStack ---------------------------------------------------------------------------------


@pytest.mark.localstack
@pytest.mark.skipif(os.environ.get("RUN_LOCALSTACK") != "1", reason="set RUN_LOCALSTACK=1")
def test_localstack_end_to_end(
    session: Session,  # noqa: F811
    runner: FakeRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three steps against LocalStack S3 and PostGIS; only the STAC/DEM part is faked."""
    for var, value in (("AWS_ACCESS_KEY_ID", "test"), ("AWS_SECRET_ACCESS_KEY", "test")):
        monkeypatch.setenv(var, os.environ.get(var, value))
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
    client = s3_client_from_settings(Settings(aws_endpoint_url=endpoint, aws_region="us-east-1"))
    steps = WorkerSteps(_factory(session), client, runner=runner, workdir=tmp_path)
    model_id = steps.register_baseline()
    site = stored_site(session, client)
    run_ids: list[uuid.UUID] = []
    try:
        run, out = run_pipeline_kind(steps, session, site, model_id)
        change_run, change_out = run_change_kind(steps, session, site, model_id)
        run_ids += [run.id, change_run.id]
        assert len(out["classification_ids"]) == 2 and change_out["change_id"]
        row = session.execute(
            text("SELECT cog_uri, stats FROM changes WHERE id = :id"),
            {"id": change_out["change_id"]},
        ).one()
        assert row.stats["total_ha"] == 5.7
        bucket, key = parse_s3_uri(row.cog_uri)
        head = client.head_object(Bucket=bucket, Key=key)
        assert head["ContentType"].startswith("image/tiff")
        sidecar = json.loads(
            client.get_object(Bucket=bucket, Key=key[:-4] + ".json")["Body"].read()
        )
        validate_sidecar(sidecar)
        again_run, again = run_change_kind(steps, session, site, model_id)
        run_ids.append(again_run.id)
        assert again["change_id"] == change_out["change_id"]
        assert client.head_object(Bucket=bucket, Key=key)["ETag"] == head["ETag"]
    finally:  # remove only what this test wrote
        prefixes = [(DATA, f"{p}/{site.slug}/") for p in ("sites", "composites")]
        prefixes += [(DATA, f"{p}/{site.slug}/") for p in ("classifications", "changes")]
        prefixes += [(ARTIFACTS, f"{p}/{r}/") for r in run_ids for p in ("runs", "exports")]
        for bucket, prefix in prefixes:
            listing = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
            for obj in listing.get("Contents", []):
                client.delete_object(Bucket=bucket, Key=obj["Key"])
