"""Spatial block split: no cross-split overlap or adjacency, demo site held out, determinism."""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from ml.dataset import PatchDataset
from ml.split import (
    DEMO_BBOX_WGS84,
    SPLITS,
    assign_blocks,
    dataset_patch_bounds,
    holdout_bounds,
    split_bounds,
    split_dataset,
)
from rasterio.transform import from_origin

CRS = "EPSG:32630"
PATCH_M = 2560.0  # 256 px at 10 m


def _grid(stride: float = PATCH_M, margin: float = 40000.0) -> list[tuple[float, ...]]:
    """Patch bounds tiling a box around the demo site (UTM 30N)."""
    left, bottom, right, top = holdout_bounds(CRS, [DEMO_BBOX_WGS84])[0]
    x0, y0 = left - margin + 123.0, bottom - margin + 77.0  # off the block grid on purpose
    x1, y1 = right + margin, top + margin
    out = []
    y = y0
    while y + PATCH_M <= y1:
        x = x0
        while x + PATCH_M <= x1:
            out.append((x, y, x + PATCH_M, y + PATCH_M))
            x += stride
        y += stride
    return out


def _gap(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise Chebyshev-style gap between boxes (negative = overlap, 0 = touching)."""
    dx = np.maximum(a[:, None, 0] - b[None, :, 2], b[None, :, 0] - a[:, None, 2])
    dy = np.maximum(a[:, None, 1] - b[None, :, 3], b[None, :, 1] - a[:, None, 3])
    return np.maximum(dx, dy)


def _assert_separated(bounds: list[tuple[float, ...]], res, min_gap: float) -> None:
    arr = np.asarray(bounds)
    for i, s in enumerate(SPLITS):
        for t in SPLITS[i + 1 :]:
            a, b = arr[res.indices(s)], arr[res.indices(t)]
            if len(a) and len(b):
                gap = _gap(a, b)
                assert (gap > 0).all(), f"{s}/{t} patches overlap or touch"
                assert gap.min() >= min_gap - 1e-6, f"{s}/{t} closer than buffer"


@pytest.mark.parametrize("stride", [PATCH_M, PATCH_M / 2])
@pytest.mark.parametrize("buffer", [500.0, 1000.0, 3000.0])
def test_no_patch_overlaps_or_neighbours_across_splits(stride: float, buffer: float) -> None:
    bounds = _grid(stride)
    res = split_bounds(bounds, CRS, buffer=buffer, seed=3)
    if buffer <= 1000.0:  # a 3 km buffer around 2.56 km patches leaves few 5 km blocks usable
        assert all(len(res.indices(s)) > 0 for s in SPLITS)
    _assert_separated(bounds, res, buffer)


def test_zero_buffer_still_never_overlaps() -> None:
    bounds = _grid(PATCH_M / 2)
    res = split_bounds(bounds, CRS, buffer=0.0, seed=1)
    arr = np.asarray(bounds)
    for i, s in enumerate(SPLITS):
        for t in SPLITS[i + 1 :]:
            assert (_gap(arr[res.indices(s)], arr[res.indices(t)]) >= 0).all()


def test_demo_site_fully_held_out() -> None:
    bounds = _grid(PATCH_M / 2)
    buffer = 1000.0
    res = split_bounds(bounds, CRS, buffer=buffer, seed=7)
    demo = np.asarray(holdout_bounds(CRS, [DEMO_BBOX_WGS84]))
    arr = np.asarray(bounds)
    for s in ("train", "val"):
        gap = _gap(arr[res.indices(s)], demo)
        assert (gap >= buffer - 1e-6).all(), f"{s} patch within buffer of the demo site"
    # Every block under the demo box is test, and every patch inside the demo box is kept as test.
    d = demo[0]
    inside = [
        i
        for i, (x0, y0, x1, y1) in enumerate(bounds)
        if x0 >= d[0] and y0 >= d[1] and x1 <= d[2] and y1 <= d[3]
    ]
    assert len(inside) > 20
    assert {res.patch_split[i] for i in inside} == {"test"}
    for c, r in {(c, r) for (c, r), s in res.block_split.items() if s != "test"}:
        bl = np.array([[c * 5000.0, r * 5000.0, (c + 1) * 5000.0, (r + 1) * 5000.0]])
        assert (_gap(bl, demo) >= 0).all()


def test_buffer_taken_from_lower_priority_split() -> None:
    # Patches filling every block whole: test/val patches stay, train patches near them drop.
    bounds = [
        (x, y, x + 1000.0, y + 1000.0)
        for x in np.arange(600000.0, 650000.0, 1000.0)
        for y in np.arange(6000000.0, 6050000.0, 1000.0)
    ]
    res = split_bounds(bounds, CRS, buffer=1000.0, seed=0, holdout_bboxes_wgs84=())
    by_block: dict[str, int] = {s: 0 for s in SPLITS}
    for i, b in enumerate(bounds):
        own = res.block_split[(int(b[0] // 5000), int(b[1] // 5000))]
        if res.patch_split[i] is None:
            by_block[own] += 1
    assert by_block["test"] == 0 and by_block["train"] > 0
    _assert_separated(bounds, res, 1000.0)


def test_block_fractions_near_60_20_20() -> None:
    res = split_bounds(_grid(margin=80000.0), CRS, seed=0)
    counts = {s: sum(v == s for v in res.block_split.values()) for s in SPLITS}
    n = sum(counts.values())
    assert n > 100
    assert abs(counts["train"] / n - 0.6) < 0.03
    assert abs(counts["val"] / n - 0.2) < 0.03
    assert abs(counts["test"] / n - 0.2) < 0.03


def test_no_holdout_and_custom_fractions() -> None:
    res = split_bounds(_grid(), CRS, holdout_bboxes_wgs84=(), fractions=(0.5, 0.5, 0.0))
    assert "test" not in set(res.block_split.values())
    assert set(res.counts()) == {"train", "val", "test", "dropped"}


def test_deterministic_given_seed() -> None:
    bounds = _grid()
    a = split_bounds(bounds, CRS, seed=42)
    b = split_bounds(bounds, CRS, seed=42)
    c = split_bounds(bounds, CRS, seed=43)
    assert a.patch_split == b.patch_split and a.block_split == b.block_split
    assert a.block_split != c.block_split


def test_assign_blocks_forced_and_errors() -> None:
    blocks = [(i, j) for i in range(10) for j in range(10)]
    forced = [(0, 0), (0, 1)]
    out = assign_blocks(blocks, forced_test=forced, seed=5)
    assert all(out[b] == "test" for b in forced)
    assert sum(v == "test" for v in out.values()) == 20
    assert sum(v == "val" for v in out.values()) == 20
    with pytest.raises(ValueError):
        assign_blocks(blocks, fractions=(0.5, 0.5))
    with pytest.raises(ValueError):
        split_bounds([(0, 0, 1, 1)], "EPSG:4326")
    with pytest.raises(ValueError):
        split_bounds([(0, 0, 1, 1)], CRS, buffer=-1)


def test_forced_blocks_without_patches_do_not_use_the_test_quota() -> None:
    blocks = [(i, j) for i in range(10) for j in range(10)]
    far_forced = [(50, 50), (50, 51), (50, 52)]  # hold-out blocks with no patches
    out = assign_blocks(blocks, forced_test=far_forced, seed=5)
    assert all(out[b] == "test" for b in far_forced)
    held_test = sum(v == "test" for b, v in out.items() if b in set(blocks))
    assert held_test == 20 and sum(v == "val" for v in out.values()) == 20


def test_split_dataset_matches_patch_bounds(tmp_path: Path) -> None:
    h = w = 1600  # 16 km at 10 m
    path = tmp_path / "comp.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=h,
        width=w,
        count=1,
        dtype="float32",
        crs=CRS,
        transform=from_origin(300000.0, 6200000.0, 10, 10),
    ) as dst:
        dst.write(np.zeros((1, h, w), dtype=np.float32))
    ds = PatchDataset(path, size=128, stride=64, edge="shift")
    bounds = dataset_patch_bounds(ds)
    assert len(bounds) == len(ds)
    for i in (0, len(ds) // 2, len(ds) - 1):
        assert np.allclose(bounds[i], ds[i].bounds)
    res = split_dataset(ds, block_size=2000.0, buffer=500.0, seed=2)
    assert len(res.patch_split) == len(ds)
    _assert_separated(bounds, res, 500.0)
