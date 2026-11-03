"""Tests for grid construction and compositing using synthetic rasters."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from numpy.typing import NDArray
from pipeline.composite import build_composite, load_s2_bands, median_composite, read_scene
from pipeline.grid import Grid, build_grid
from rasterio.crs import CRS
from rasterio.transform import Affine, from_origin

SITE = {
    "type": "Feature",
    "properties": {},
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [[-3.01, 55.0], [-3.0, 55.0], [-3.0, 55.01], [-3.01, 55.01], [-3.01, 55.0]]
        ],
    },
}
BANDS = ["B02", "B08"]


def _write(
    path: Path, data: NDArray[np.generic], grid: Grid, res: float, pad: int, crs: CRS | None = None
) -> str:
    """Write ``data`` (2-D) covering the grid bounds plus ``pad`` metres, at ``res``."""
    w, s, e, n = grid.bounds
    nx = int((e - w + 2 * pad) / res)
    ny = int((n - s + 2 * pad) / res)
    arr = np.broadcast_to(data, (ny, nx)) if np.ndim(data) == 0 else data
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=nx,
        height=ny,
        count=1,
        dtype=str(arr.dtype),
        crs=crs or grid.crs,
        transform=from_origin(w - pad, n + pad, res, res),
    ) as dst:
        dst.write(arr, 1)
    return str(path)


@pytest.fixture
def grid() -> Grid:
    return build_grid(SITE, buffer_m=100)


def _scene(
    tmp: Path, grid: Grid, name: str, dn: tuple[int, int], scl: int
) -> tuple[dict[str, str], str]:
    hrefs = {
        b: _write(tmp / f"{name}_{b}.tif", np.full((1,), v, dtype=np.uint16)[0], grid, 20.0, 200)
        for b, v in zip(BANDS, dn, strict=True)
    }
    return hrefs, _write(tmp / f"{name}_SCL.tif", np.uint8(scl), grid, 20.0, 200)


def test_grid_crs_resolution_and_snap(grid: Grid) -> None:
    assert grid.crs == CRS.from_epsg(32630)
    t = grid.transform
    assert (t.a, t.e) == (10.0, -10.0)
    assert t.c % 10 == 0 and t.f % 10 == 0
    w, s, e, n = grid.bounds
    assert (e - w) / 10 == grid.width and (n - s) / 10 == grid.height


def test_grid_buffer_expands_and_covers_site() -> None:
    g0 = build_grid(SITE, buffer_m=0)
    g1 = build_grid(SITE, buffer_m=100)
    assert g1.width - g0.width in (20, 21, 22)
    assert g1.bounds[0] <= g0.bounds[0] - 100 + 10
    # ~0.01 deg lon at 55N is ~640 m; lat ~1113 m
    assert 60 <= g0.width <= 70
    assert 110 <= g0.height <= 114


def test_grid_other_crs_and_path(tmp_path: Path) -> None:
    g_ll = build_grid(SITE, buffer_m=50)
    # Same site expressed in EPSG:32630 should give the same grid
    from rasterio.warp import transform_geom

    geom = transform_geom("EPSG:4326", "EPSG:32630", SITE["geometry"])
    p = tmp_path / "site.geojson"
    import json

    p.write_text(json.dumps({"type": "Feature", "properties": {}, "geometry": geom}))
    g_utm = build_grid(p, buffer_m=50, src_crs="EPSG:32630")
    assert g_utm.transform == g_ll.transform and g_utm.shape == g_ll.shape


def test_grid_errors() -> None:
    with pytest.raises(ValueError):
        build_grid(SITE, buffer_m=-1)
    with pytest.raises(ValueError):
        build_grid({"type": "FeatureCollection", "features": []})


def test_median_and_coverage_synthetic() -> None:
    scenes = np.zeros((3, 2, 2, 2), dtype=np.float32)
    scenes[:, 0] = np.array([1, 5, 3], dtype=np.float32)[:, None, None]
    scenes[:, 1] = 10
    valid = np.ones((3, 2, 2), dtype=bool)
    valid[1, 0, 0] = False  # drop the 5 at (0,0): median of [1,3] = 2
    valid[:, 1, 1] = False  # no observations
    comp, cov = median_composite(scenes, valid)
    assert comp.dtype == np.float32 and cov.dtype == np.uint16
    assert comp[0, 0, 0] == 2.0
    assert comp[0, 0, 1] == 3.0
    assert np.isnan(comp[:, 1, 1]).all()
    assert cov.tolist() == [[2, 3], [3, 0]]
    # no unexpected NaN elsewhere
    assert np.isfinite(comp[:, cov > 0]).all()


def test_median_empty_and_shape_errors() -> None:
    comp, cov = median_composite(np.empty((0, 2, 3, 4), np.float32), np.empty((0, 3, 4), bool))
    assert comp.shape == (2, 3, 4) and np.isnan(comp).all() and cov.shape == (3, 4)
    with pytest.raises(ValueError):
        median_composite(np.zeros((1, 2, 3, 4), np.float32), np.ones((1, 3, 3), bool))


def test_bands_yaml_order() -> None:
    names, scale = load_s2_bands()
    assert names[0] == "B01" and names[-1] == "B12" and len(names) == 12
    assert scale == 0.0001


def test_read_scene_grid_alignment_and_values(tmp_path: Path, grid: Grid) -> None:
    hrefs, scl = _scene(tmp_path, grid, "a", (1000, 3000), 4)
    bands, valid = read_scene(hrefs, scl, grid, band_names=BANDS)
    assert bands.shape == (2, *grid.shape) and bands.dtype == np.float32
    assert valid.shape == grid.shape and valid.all()
    assert np.allclose(bands[0], 0.1) and np.allclose(bands[1], 0.3)
    assert not np.isnan(bands).any()


def test_read_scene_reprojects_from_other_crs(tmp_path: Path, grid: Grid) -> None:
    # Source in EPSG:32629 (different zone) with constant DN; footprint is larger than grid.
    from rasterio.warp import calculate_default_transform

    w, s, e, n = grid.bounds
    src_t, sw, sh = calculate_default_transform(
        grid.crs,
        CRS.from_epsg(32629),
        grid.width + 100,
        grid.height + 100,
        w - 500,
        s - 500,
        e + 500,
        n + 500,
        resolution=20,
    )
    hrefs = {}
    for b in BANDS:
        p = tmp_path / f"{b}.tif"
        with rasterio.open(
            p,
            "w",
            driver="GTiff",
            width=sw,
            height=sh,
            count=1,
            dtype="uint16",
            crs=CRS.from_epsg(32629),
            transform=src_t,
        ) as dst:
            dst.write(np.full((sh, sw), 2000, dtype=np.uint16), 1)
        hrefs[b] = str(p)
    p = tmp_path / "scl.tif"
    with rasterio.open(
        p,
        "w",
        driver="GTiff",
        width=sw,
        height=sh,
        count=1,
        dtype="uint8",
        crs=CRS.from_epsg(32629),
        transform=src_t,
    ) as dst:
        dst.write(np.full((sh, sw), 5, dtype=np.uint8), 1)
    bands, valid = read_scene(hrefs, str(p), grid, band_names=BANDS)
    assert valid.all()
    assert np.allclose(bands, 0.2)


def test_footprint_gap_is_invalid_and_nan(tmp_path: Path, grid: Grid) -> None:
    # Source covers only part of the grid (pad negative via small array placed at origin).
    w, s, e, n = grid.bounds
    half = grid.width // 2
    arr = np.full((grid.height, half), 1500, dtype=np.uint16)
    scl_arr = np.full((grid.height, half), 4, dtype=np.uint8)

    def put(name: str, data: NDArray[np.generic]) -> str:
        p = tmp_path / name
        with rasterio.open(
            p,
            "w",
            driver="GTiff",
            width=half,
            height=grid.height,
            count=1,
            dtype=str(data.dtype),
            crs=grid.crs,
            transform=Affine(10, 0, w, 0, -10, n),
        ) as dst:
            dst.write(data, 1)
        return str(p)

    hrefs = {b: put(f"{b}.tif", arr) for b in BANDS}
    bands, valid = read_scene(hrefs, put("scl.tif", scl_arr), grid, band_names=BANDS)
    assert valid[:, :half].all() and not valid[:, half:].any()
    assert np.isnan(bands[:, :, half:]).all()
    assert not np.isnan(bands[:, :, :half]).any()


def test_build_composite_end_to_end(tmp_path: Path, grid: Grid) -> None:
    scenes = [
        _scene(tmp_path, grid, "s1", (1000, 1000), 4),
        _scene(tmp_path, grid, "s2", (2000, 2000), 5),
        _scene(tmp_path, grid, "s3", (9000, 9000), 9),  # cloud: excluded
    ]
    comp, cov = build_composite(scenes, grid, band_names=BANDS)
    assert comp.shape == (2, *grid.shape) and comp.dtype == np.float32
    assert cov.dtype == np.uint16 and (cov == 2).all()
    assert np.allclose(comp, 0.15)
    assert not np.isnan(comp).any()


def test_build_composite_strips_match_whole_grid(tmp_path: Path, grid: Grid) -> None:
    scenes = [
        _scene(tmp_path, grid, "s1", (1000, 1000), 4),
        _scene(tmp_path, grid, "s2", (2000, 2000), 5),
    ]
    whole, cov_whole = build_composite(scenes, grid, band_names=BANDS, strip_rows=grid.height)
    strips, cov_strips = build_composite(scenes, grid, band_names=BANDS, strip_rows=3)
    assert np.array_equal(whole, strips, equal_nan=True)
    assert np.array_equal(cov_whole, cov_strips)


def test_build_composite_no_scenes(grid: Grid) -> None:
    comp, cov = build_composite([], grid, band_names=BANDS)
    assert np.isnan(comp).all() and (cov == 0).all()


def test_read_scene_missing_band(tmp_path: Path, grid: Grid) -> None:
    hrefs, scl = _scene(tmp_path, grid, "a", (1000, 3000), 4)
    del hrefs["B08"]
    with pytest.raises(KeyError):
        read_scene(hrefs, scl, grid, band_names=BANDS)
