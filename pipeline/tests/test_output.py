"""Tests for the COG writer, sidecar, idempotency key and S3 store."""

from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from numpy.typing import NDArray
from pipeline.grid import Grid
from pipeline.output import (
    SceneNotBoaCorrectedError,
    band_order,
    boundary_sha256,
    build_sidecar,
    canonical_json,
    cloud_stats,
    composite_key,
    idempotency_key,
    stack_bands,
    validate_sidecar,
    write_composite_cog,
    write_sidecar,
)
from rasterio.transform import Affine

SCENE = "S2B_30UUG_20250408_0_L2A"
START = END = date(2025, 4, 8)


@pytest.fixture
def grid() -> Grid:
    return Grid(Affine(10.0, 0.0, 400000.0, 0.0, -10.0, 6100000.0), 1100, 600)


@pytest.fixture
def bands(grid: Grid) -> NDArray[np.float32]:
    rng = np.random.default_rng(0)
    arr = rng.random((18, *grid.shape), dtype=np.float32)
    arr[:, :5, :5] = np.nan
    return arr


def _sidecar(grid: Grid, bands: NDArray[np.float32], **over: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "site": "test-site",
        "start": START,
        "end": END,
        "key": composite_key("test-site", START, END),
        "bands": band_order(),
        "scene_ids": [SCENE],
        "scene_boa_offset_applied": {SCENE: True},
        "parameters": {"cloud_threshold": 40.0, "collection": "sentinel-2-l2a"},
        "boundary_sha": "a" * 64,
        "cloud_stats": cloud_stats(bands, np.ones(grid.shape, np.uint16), [12.5]),
        "code_version": "abcdef1234567",
    }
    kwargs.update(over)
    return build_sidecar(**kwargs)


def test_band_order_is_18_per_contract() -> None:
    names = band_order()
    assert len(names) == 18
    assert names[0] == "B01" and names[11] == "B12"
    assert names[12:] == ["ndvi", "ndwi", "ndmi", "elevation", "slope", "twi"]


def test_stack_bands() -> None:
    out = stack_bands(np.zeros((12, 4, 4), np.float32), np.ones((6, 4, 4), np.float32))
    assert out.shape == (18, 4, 4) and out.dtype == np.float32


def test_cog_profile(tmp_path: Path, grid: Grid, bands: NDArray[np.float32]) -> None:
    out = write_composite_cog(tmp_path / "c.tif", bands, grid)
    with rasterio.open(out) as src:
        assert src.count == 18
        assert src.dtypes[0] == "float32"
        assert src.crs.to_epsg() == 32630
        assert src.transform == grid.transform
        assert src.nodata is not None and np.isnan(src.nodata)
        assert list(src.descriptions) == band_order()
        assert src.block_shapes[0] == (512, 512)
        assert src.interleaving.name == "band"
        assert src.profile["compress"].lower() == "deflate"
        assert src.tags(ns="IMAGE_STRUCTURE")["PREDICTOR"] == "3"
        assert src.overviews(1)
        got = src.read()
    np.testing.assert_array_equal(got, bands)


def test_cog_is_deterministic(tmp_path: Path, grid: Grid, bands: NDArray[np.float32]) -> None:
    a = write_composite_cog(tmp_path / "a.tif", bands, grid)
    b = write_composite_cog(tmp_path / "b.tif", bands, grid)
    assert a.read_bytes() == b.read_bytes()


def test_cog_rejects_bad_shapes(tmp_path: Path, grid: Grid, bands: NDArray[np.float32]) -> None:
    with pytest.raises(ValueError):
        write_composite_cog(tmp_path / "x.tif", bands[:17], grid)
    with pytest.raises(ValueError):
        write_composite_cog(tmp_path / "x.tif", bands[:, :10, :10], grid)


def test_stack_bands_feeds_writer(tmp_path: Path, grid: Grid) -> None:
    comp = np.full((12, *grid.shape), 0.1, np.float32)
    feats = np.full((6, *grid.shape), 0.5, np.float32)
    out = write_composite_cog(tmp_path / "s.tif", stack_bands(comp, feats), grid)
    with rasterio.open(out) as src:
        assert src.count == 18


def test_sidecar_validates_and_roundtrips(
    tmp_path: Path, grid: Grid, bands: NDArray[np.float32]
) -> None:
    sc = _sidecar(grid, bands)
    validate_sidecar(sc)
    p = write_sidecar(tmp_path / "c.json", sc)
    assert json.loads(p.read_text()) == sc
    assert sc["season"] == "w20250408-20250408"
    assert sc["parameters"]["boa_offset_applied"] == {SCENE: True}
    assert sc["key"] == "composites/test-site/2025/w20250408-20250408.tif"


def test_sidecar_schema_rejects_bad(grid: Grid, bands: NDArray[np.float32]) -> None:
    import jsonschema

    sc = _sidecar(grid, bands)
    sc["bands"] = sc["bands"][:17]
    with pytest.raises(jsonschema.ValidationError):
        validate_sidecar(sc)


def test_refuses_scene_without_boa_offset(grid: Grid, bands: NDArray[np.float32]) -> None:
    with pytest.raises(SceneNotBoaCorrectedError):
        _sidecar(grid, bands, scene_boa_offset_applied={SCENE: False})
    with pytest.raises(SceneNotBoaCorrectedError):
        _sidecar(grid, bands, scene_boa_offset_applied={})


def test_key_is_hex_sha256_of_canonical_json() -> None:
    params = {"b": 1, "a": [1.5, "x"]}
    key = idempotency_key(
        kind="composite",
        site="s1",
        boundary_sha="b" * 64,
        start=START,
        end=END,
        parameters=params,
        code_version="abc1234",
    )
    expected = {
        "kind": "composite",
        "site": "s1",
        "boundary_sha256": "b" * 64,
        "date_range": {"start": "2025-04-08", "end": "2025-04-08"},
        "parameters": params,
        "versions": {
            "code": "abc1234",
            "model": None,
            "classes_contract": 1,
            "bands_contract": 1,
        },
        "inputs": [],
    }
    assert key == hashlib.sha256(canonical_json(expected)).hexdigest()
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_key_same_inputs_same_and_changes_with_param(
    grid: Grid, bands: NDArray[np.float32]
) -> None:
    a = _sidecar(grid, bands)["idempotency_key"]
    assert a == _sidecar(grid, bands)["idempotency_key"]
    changed = _sidecar(grid, bands, parameters={"cloud_threshold": 30.0, "collection": "x"})
    assert changed["idempotency_key"] != a
    assert _sidecar(grid, bands, code_version="abcdef7654321")["idempotency_key"] != a
    assert _sidecar(grid, bands, boundary_sha="c" * 64)["idempotency_key"] != a


def test_boundary_sha(tmp_path: Path) -> None:
    p = tmp_path / "b.geojson"
    p.write_bytes(b'{"type":"Point"}')
    assert boundary_sha256(p) == hashlib.sha256(b'{"type":"Point"}').hexdigest()


def test_store_skip_logic_with_fake_client(tmp_path: Path) -> None:
    from pipeline.store import upload_product

    class Err(Exception):
        def __init__(self) -> None:
            self.response = {"Error": {"Code": "404"}}

    class Fake:
        class exceptions:  # noqa: N801
            ClientError = Err

        def __init__(self) -> None:
            self.objs: dict[str, bytes] = {}
            self.uploads: list[str] = []

        def head_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
            if Key not in self.objs:
                raise Err()

        def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
            return {"Body": io.BytesIO(self.objs[Key])}

        def upload_file(
            self,
            path: str,
            Bucket: str,  # noqa: N803
            Key: str,  # noqa: N803
            ExtraArgs: dict[str, str],  # noqa: N803
        ) -> None:
            self.objs[Key] = Path(path).read_bytes()
            self.uploads.append(Key)

    tif = tmp_path / "t.tif"
    tif.write_bytes(b"x")
    side = tmp_path / "t.json"
    side.write_text(json.dumps({"key": "composites/s/2025/a.tif", "idempotency_key": "k" * 64}))
    fake = Fake()
    assert upload_product(tif, side, client=fake) == "uploaded"
    assert fake.uploads == ["composites/s/2025/a.tif", "composites/s/2025/a.json"]
    assert upload_product(tif, side, client=fake) == "skipped"
    side.write_text(json.dumps({"key": "composites/s/2025/a.tif", "idempotency_key": "z" * 64}))
    assert upload_product(tif, side, client=fake) == "uploaded"


@pytest.mark.localstack
@pytest.mark.skipif(os.environ.get("RUN_LOCALSTACK") != "1", reason="set RUN_LOCALSTACK=1")
def test_localstack_roundtrip(tmp_path: Path, grid: Grid, bands: NDArray[np.float32]) -> None:
    from pipeline.store import s3_client, upload_product

    site = "pytest-p7"
    key = composite_key(site, START, END)
    skey = key.replace(".tif", ".json")
    sc = _sidecar(grid, bands, site=site, key=key)
    tif = write_composite_cog(tmp_path / "c.tif", bands, grid)
    side = write_sidecar(tmp_path / "c.json", sc)
    s3 = s3_client()
    bucket = "peatwatch-data"
    for k in (key, skey):
        s3.delete_object(Bucket=bucket, Key=k)
    try:
        assert upload_product(tif, side, bucket) == "uploaded"
        assert upload_product(tif, side, bucket) == "skipped"
        stored = json.loads(s3.get_object(Bucket=bucket, Key=skey)["Body"].read())
        assert stored["idempotency_key"] == sc["idempotency_key"]
        assert s3.head_object(Bucket=bucket, Key=key)["ContentLength"] == tif.stat().st_size
        changed = _sidecar(grid, bands, site=site, key=key, parameters={"cloud_threshold": 10.0})
        side2 = write_sidecar(tmp_path / "c2.json", changed)
        assert upload_product(tif, side2, bucket) == "uploaded"
    finally:
        for k in (key, skey):
            s3.delete_object(Bucket=bucket, Key=k)
