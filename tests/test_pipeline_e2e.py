"""End-to-end pipeline test against LocalStack with recorded STAC and synthetic rasters.

Run with: RUN_LOCALSTACK=1 AWS_ENDPOINT_URL=http://localhost:4566 \
    uv run python -m pytest tests -k pipeline_e2e
No live network: the STAC responses are recorded, rasters are tiny local GeoTIFFs.
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from pipeline.config import Buckets, DateWindow, RunConfig
from pipeline.grid import build_grid
from pipeline.log import RunLogger
from pipeline.output import SceneNotBoaCorrectedError, band_order, validate_sidecar
from pipeline.runner import run_pipeline
from pipeline.stac import BAND_ASSETS, SCL_ASSET, NoUsableScenesError
from pipeline.store import s3_client
from pystac import ItemCollection
from rasterio.transform import from_origin

pytestmark = [
    pytest.mark.localstack,
    pytest.mark.skipif(os.environ.get("RUN_LOCALSTACK") != "1", reason="set RUN_LOCALSTACK=1"),
]

ROOT = Path(__file__).resolve().parents[1]
RECORDED = ROOT / "pipeline" / "tests" / "fixtures" / "earth_search_galloway_2025-04-08.json"
BOUNDARY = ROOT / "pipeline" / "sites" / "tiny-fixture.geojson"
BUCKET = "peatwatch-data"
WINDOW = DateWindow(start=date(2025, 4, 8), end=date(2025, 4, 8))
# Reflectance DN per band for the two scenes (scene B is brighter; both stay positive).
DN_A = {"B02": 500, "B03": 700, "B04": 600, "B08": 3000, "B11": 1800}
DN_B = {"B02": 700, "B03": 900, "B04": 800, "B08": 3400, "B11": 2200}


class _Result:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def items(self) -> Iterator[Any]:
        return iter(self._items)


class FakeStac:
    """Replays recorded items, rewriting asset hrefs to local synthetic files."""

    def __init__(self, features: list[dict[str, Any]]) -> None:
        data = json.loads(RECORDED.read_text(encoding="utf-8"))
        data["features"] = features
        self._items = ItemCollection.from_dict(data).items

    def search(self, **_: Any) -> _Result:
        return _Result(self._items)


class _Asset:
    def __init__(self, href: str) -> None:
        self.href = href


class _DemItem:
    def __init__(self, href: str) -> None:
        self.assets = {"data": _Asset(href)}


class FakeDemStac:
    def __init__(self, href: str) -> None:
        self._href = href

    def search(self, **_: Any) -> _Result:
        return _Result([_DemItem(self._href)])


def _write(
    path: Path, data: np.ndarray, crs: str, transform: Any, dtype: str, nodata: float | None
) -> str:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype=dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return str(path)


def _scene_files(
    root: Path, scene_id: str, dn: dict[str, int], grid: Any, cloudy_top_half: bool
) -> dict[str, str]:
    """Write 12 band files plus SCL covering the grid with a 500 m margin."""
    west, south, east, north = grid.bounds
    pad = 500.0
    h = int((north - south + 2 * pad) / 10)
    w = int((east - west + 2 * pad) / 10)
    tr = from_origin(west - pad, north + pad, 10, 10)
    d = root / scene_id
    d.mkdir(parents=True)
    hrefs: dict[str, str] = {}
    for band, asset in BAND_ASSETS.items():
        value = dn.get(band, 1000)
        hrefs[asset] = _write(
            d / f"{band}.tif",
            np.full((h, w), value, dtype=np.uint16),
            "EPSG:32630",
            tr,
            "uint16",
            0,
        )
    scl = np.full((h, w), 4, dtype=np.uint8)
    if cloudy_top_half:
        scl[: h // 2] = 9  # cloud high probability: must be masked out
    hrefs[SCL_ASSET] = _write(d / "SCL.tif", scl, "EPSG:32630", tr, "uint8", 0)
    return hrefs


def _features(root: Path, grid: Any) -> list[dict[str, Any]]:
    data = json.loads(RECORDED.read_text(encoding="utf-8"))
    feats: list[dict[str, Any]] = copy.deepcopy(data["features"])
    plan = [(DN_A, False), (DN_B, True)]
    for feat, (dn, cloudy) in zip(feats, plan, strict=True):
        hrefs = _scene_files(root, feat["id"], dn, grid, cloudy)
        for asset, href in hrefs.items():
            feat["assets"][asset]["href"] = href
    return feats


def _dem(root: Path) -> str:
    # A WGS84 ramp rising to the north, covering the fixture site generously.
    lon0, lat0, res = -4.52, 55.12, 0.0005
    n = 100
    lat = lat0 - np.arange(n) * res
    z = np.repeat((100 + (lat - lat.min()) * 2000).astype(np.float32)[:, None], n, axis=1)
    return _write(
        root / "dem.tif", z, "EPSG:4326", from_origin(lon0, lat0, res, res), "float32", -9999.0
    )


def _config(tmp_path: Path, site: str) -> RunConfig:
    return RunConfig(
        site=site,
        boundary_path=BOUNDARY,
        window=WINDOW,
        output_dir=tmp_path / "out",
        buckets=Buckets(data=BUCKET),
    )


@pytest.fixture
def s3() -> Any:
    return s3_client(os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"))


@pytest.fixture
def site(s3: Any) -> Iterator[str]:
    name = f"e2e-{uuid.uuid4().hex[:8]}"
    yield name
    for obj in s3.list_objects_v2(Bucket=BUCKET, Prefix=f"composites/{name}/").get("Contents", []):
        s3.delete_object(Bucket=BUCKET, Key=obj["Key"])


def _run(tmp_path: Path, site: str, s3: Any, features: list[dict[str, Any]], run_id: str) -> Any:
    cfg = _config(tmp_path, site)
    logger = RunLogger(cfg.output_dir / "runs" / run_id / "log.jsonl", run_id)
    return run_pipeline(
        cfg,
        logger,
        package="pipeline test",
        stac_client=FakeStac(features),
        dem_client=FakeDemStac(_dem(tmp_path)),
        s3=s3,
    ), logger


def test_pipeline_e2e_produces_cog_sidecar_and_skips_rerun(
    tmp_path: Path, s3: Any, site: str
) -> None:
    grid = build_grid(BOUNDARY)
    feats = _features(tmp_path / "scenes", grid)

    result, logger = _run(tmp_path, site, s3, feats, "run1")
    assert result.outcome == "uploaded"
    key = result.key
    assert key.startswith(f"composites/{site}/2025/")

    # Sidecar is stored, valid, and lists both scenes.
    body = s3.get_object(Bucket=BUCKET, Key=key.removesuffix(".tif") + ".json")["Body"].read()
    sidecar = json.loads(body)
    validate_sidecar(sidecar)
    assert sidecar["scene_ids"] == sorted(f["id"] for f in feats)
    assert sidecar["bands"] == band_order()
    assert sidecar["parameters"]["cloud_threshold"] == 40.0

    # The stored COG has 18 bands with sensible values.
    dl = tmp_path / "dl.tif"
    s3.download_file(BUCKET, key, str(dl))
    with rasterio.open(dl) as src:
        assert src.count == 18
        assert src.crs.to_epsg() == 32630
        assert src.res == (10.0, 10.0)
        assert list(src.descriptions) == band_order()
        arr = src.read()
    names = band_order()
    nir, red = arr[names.index("B08")], arr[names.index("B04")]
    # Top half: scene B is cloud-masked, so only scene A (0.3 / 0.06). Bottom half: median(A, B).
    top_nir = np.nanmedian(nir[: nir.shape[0] // 4])
    bottom_nir = np.nanmedian(nir[3 * nir.shape[0] // 4 :])
    assert top_nir == pytest.approx(0.3, abs=1e-4)
    assert bottom_nir == pytest.approx(0.32, abs=1e-4)
    ndvi = arr[names.index("ndvi")]
    assert np.nanmedian(ndvi[: ndvi.shape[0] // 4]) == pytest.approx((0.3 - 0.06) / 0.36, abs=1e-3)
    elev = arr[names.index("elevation")]
    assert np.nanmin(elev) > 100 and np.nanmax(elev) < 400
    assert np.isfinite(arr[names.index("slope")]).any()
    assert red.shape == nir.shape

    events = [json.loads(line) for line in logger.path.read_text().splitlines()]
    selected = next(e for e in events if e["event"] == "scenes_selected")
    assert selected["scene_ids"] == sorted(f["id"] for f in feats) or set(
        selected["scene_ids"]
    ) == {f["id"] for f in feats}

    # A second identical run is skipped without touching the stored object.
    etag = s3.head_object(Bucket=BUCKET, Key=key)["ETag"]
    again, _ = _run(tmp_path, site, s3, feats, "run2")
    assert again.outcome == "skipped"
    assert s3.head_object(Bucket=BUCKET, Key=key)["ETag"] == etag


def test_pipeline_e2e_zero_scenes_fails_clearly(tmp_path: Path, s3: Any, site: str) -> None:
    with pytest.raises(NoUsableScenesError, match="no Sentinel-2 L2A scenes"):
        _run(tmp_path, site, s3, [], "empty")
    listed = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"composites/{site}/")
    assert listed.get("KeyCount", 0) == 0


def test_pipeline_e2e_refuses_scene_without_boa_offset(tmp_path: Path, s3: Any, site: str) -> None:
    feats = _features(tmp_path / "scenes", build_grid(BOUNDARY))
    feats[0]["properties"]["earthsearch:boa_offset_applied"] = False
    with pytest.raises(SceneNotBoaCorrectedError):
        _run(tmp_path, site, s3, feats, "boa")
