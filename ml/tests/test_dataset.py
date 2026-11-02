"""PatchDataset on a synthetic 18-band float32 composite (EPSG:32630, 10 m)."""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from ml.dataset import BandNormalizer, PatchDataset, patch_origins
from rasterio.transform import from_origin

ORIGIN_X, ORIGIN_Y = 400000.0, 6100000.0


def _make(tmp_path: Path, h: int = 600, w: int = 520) -> tuple[Path, np.ndarray]:
    rng = np.random.default_rng(0)
    data = rng.random((18, h, w), dtype=np.float32)
    data[:, 10:20, 10:20] = np.nan  # cloud hole
    data[3, 300, 300] = np.nan  # one band only
    path = tmp_path / "comp.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=h,
        width=w,
        count=18,
        dtype="float32",
        crs="EPSG:32630",
        transform=from_origin(ORIGIN_X, ORIGIN_Y, 10, 10),
        nodata=np.nan,
    ) as dst:
        dst.write(data)
    return path, data


def test_origins_modes() -> None:
    assert patch_origins(600, 520, edge="drop") == [(0, 0), (0, 256), (256, 0), (256, 256)]
    pad = patch_origins(600, 520, edge="pad")
    assert len(pad) == 3 * 3
    assert pad[-1] == (512, 512)
    shift = patch_origins(600, 520, edge="shift")
    assert {r for r, _ in shift} == {0, 256, 344}
    assert {c for _, c in shift} == {0, 256, 264}
    assert patch_origins(256, 256, edge="shift") == [(0, 0)]
    assert patch_origins(100, 100, edge="drop") == []
    assert patch_origins(100, 100, edge="shift") == [(0, 0)]
    with pytest.raises(ValueError):
        patch_origins(10, 10, stride=0)


def test_stride_overlap(tmp_path: Path) -> None:
    path, _ = _make(tmp_path)
    ds = PatchDataset(path, stride=128, edge="drop")
    assert len(ds) == 3 * 3  # rows and cols 0,128,256
    assert ds[1].col_off == 128


def test_patch_content_bounds_and_transform(tmp_path: Path) -> None:
    path, data = _make(tmp_path)
    ds = PatchDataset(path, edge="drop")
    p = ds[3]  # (256, 256)
    assert (p.row_off, p.col_off) == (256, 256)
    assert p.array.shape == (18, 256, 256) and p.array.dtype == np.float32
    np.testing.assert_array_equal(p.array[:, 44:50, 44:50], data[:, 300:306, 300:306])
    left, bottom, right, top = p.bounds
    assert left == ORIGIN_X + 2560 and right == left + 2560
    assert top == ORIGIN_Y - 2560 and bottom == top - 2560
    assert p.transform @ (0, 0) == (left, top)
    assert [q.index for q in ds] == [0, 1, 2, 3]


def test_validity_and_nan_policies(tmp_path: Path) -> None:
    path, _ = _make(tmp_path)
    keep = PatchDataset(path)[0]
    assert np.isnan(keep.array).any()
    assert not keep.valid[10:20, 10:20].any()
    assert keep.valid[0, 0]
    zero = PatchDataset(path, nan_policy="zero", fill_value=-1.0)[0]
    assert np.isfinite(zero.array).all()
    assert (zero.array[:, 10:20, 10:20] == -1.0).all()
    np.testing.assert_array_equal(zero.valid, keep.valid)
    # single-band NaN marks pixel invalid by default, but not when band is excluded
    p3 = PatchDataset(path)[3]
    assert not p3.valid[44, 44]
    p3b = PatchDataset(path, valid_bands=[0, 1, 2])[3]
    assert p3b.valid[44, 44]


def test_pad_edge(tmp_path: Path) -> None:
    path, data = _make(tmp_path)
    labels = np.zeros((600, 520), dtype=np.uint8)
    ds = PatchDataset(path, edge="pad", nan_policy="zero", labels=labels)
    p = ds[len(ds) - 1]  # rows 512..600, cols 512..520
    assert p.array.shape == (18, 256, 256)
    np.testing.assert_array_equal(p.array[:, :88, :8], np.nan_to_num(data[:, 512:, 512:]))
    assert not p.valid[88:].any() and not p.valid[:, 8:].any()
    assert p.valid[:88, :8].any()
    assert p.label is not None
    assert (p.label[:88, :8] == 0).all() and (p.label[88:] == 255).all()


def test_shift_edge(tmp_path: Path) -> None:
    path, data = _make(tmp_path)
    ds = PatchDataset(path, edge="shift")
    p = ds[len(ds) - 1]
    assert (p.row_off, p.col_off) == (344, 264)
    np.testing.assert_array_equal(p.array, data[:, 344:600, 264:520])


def test_normalizer_hook(tmp_path: Path) -> None:
    path, data = _make(tmp_path)
    mean = np.full(18, 0.5)
    std = np.full(18, 0.25)
    norm = BandNormalizer(mean, std)
    p = PatchDataset(path, normalize=norm, nan_policy="zero")[0]
    np.testing.assert_allclose(p.array[0, 50, 50], (data[0, 50, 50] - 0.5) / 0.25, rtol=1e-5)
    assert p.array[0, 15, 15] == 0.0  # NaN filled after normalising
    fit = BandNormalizer.fit([data])
    assert fit.mean.shape == (18, 1, 1)
    assert abs(float(fit.mean[0, 0, 0]) - 0.5) < 0.01
    p2 = PatchDataset(path, normalize=lambda a: a * 2)[0]
    np.testing.assert_allclose(p2.array[1, 50, 50], data[1, 50, 50] * 2)


def test_bad_args(tmp_path: Path) -> None:
    path, _ = _make(tmp_path)
    with pytest.raises(ValueError):
        PatchDataset(path, labels=np.zeros((5, 5), dtype=np.uint8))
    with pytest.raises(ValueError):
        PatchDataset(path, nan_policy="bad")  # type: ignore[arg-type]
    with pytest.raises(IndexError):
        PatchDataset(path)[99]
