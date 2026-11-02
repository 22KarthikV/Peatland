"""RF baseline on small synthetic composites (no network)."""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from ml.baseline_ndvi import BARE, BOG, NODATA, WOODLAND
from ml.baseline_rf import load_model, sample_pixels, train_rf
from ml.dataset import PatchDataset
from ml.evaluate import evaluate, write_metrics_json
from ml.infer import N_CLASSES, predict_cog
from ml.split import SplitResult
from rasterio.transform import from_origin

SIZE = 16
Setup = tuple[Path, PatchDataset, SplitResult]


def _truth() -> np.ndarray:
    t = np.zeros((SIZE, SIZE), np.uint8)
    t[:, 4:8], t[:, 8:12], t[:, 12:] = BOG, WOODLAND, BARE
    return t


def _write(path: Path, truth: np.ndarray) -> None:
    rng = np.random.default_rng(0)
    data = rng.normal(0, 0.05, (18, SIZE, SIZE)).astype(np.float32)
    for cls in range(4):
        data[:, truth == cls] += cls  # separable classes in every band
    data[0, 3, 3] = np.nan  # one invalid pixel
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=SIZE,
        width=SIZE,
        count=18,
        dtype="float32",
        crs="EPSG:32630",
        transform=from_origin(400000, 6100000, 10, 10),
        nodata=np.nan,
    ) as dst:
        dst.write(data)


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    truth = _truth()
    tif = tmp_path / "c.tif"
    _write(tif, truth)
    ds = PatchDataset(tif, size=8, labels=truth)
    split = SplitResult(["train", "train", "test", "test"], {}, 5000.0, 0.0, 0)
    return tif, ds, split


def test_sample_balanced_and_capped(setup: Setup) -> None:
    _, ds, _ = setup
    x, y = sample_pixels(iter(ds), max_samples=40, seed=1)
    assert len(y) <= 40 and x.shape == (len(y), 18)
    assert np.isfinite(x).all()
    assert np.bincount(y, minlength=4).max() <= 10
    x2, y2 = sample_pixels(iter(ds), max_samples=40, seed=1)
    assert np.array_equal(x, x2) and np.array_equal(y, y2)


def test_train_evaluate_and_roundtrip(setup: Setup, tmp_path: Path) -> None:
    _, ds, split = setup
    model = train_rf(ds, split, n_estimators=20, seed=3)
    m = evaluate(model, ds, split)
    assert m.mean_iou > 0.9
    assert write_metrics_json(tmp_path / "m.json", m, {"model": "rf"}).exists()

    loaded = load_model(model.save(tmp_path / "sub" / "rf.joblib"))
    p = ds[2]
    assert np.array_equal(model(p), loaded(p))
    assert np.array_equal(
        model.predict_proba(p.array), loaded.predict_proba(p.array), equal_nan=True
    )


def test_determinism(setup: Setup) -> None:
    _, ds, split = setup
    a = train_rf(ds, split, n_estimators=10, seed=5)
    b = train_rf(ds, split, n_estimators=10, seed=5)
    p = ds[3]
    assert np.array_equal(a.predict_proba(p.array), b.predict_proba(p.array), equal_nan=True)


def test_nodata_for_nonfinite(setup: Setup) -> None:
    _, ds, split = setup
    model = train_rf(ds, split, n_estimators=10)
    p = ds[0]  # contains the NaN pixel at (3, 3)
    assert model(p)[3, 3] == NODATA
    proba = model.predict_proba(p.array)
    assert proba.shape == (N_CLASSES, 8, 8)
    assert np.isnan(proba[:, 3, 3]).all()
    assert np.isclose(proba[:, 0, 0].sum(), 1.0)


def test_predict_cog(setup: Setup, tmp_path: Path) -> None:
    tif, ds, split = setup
    model = train_rf(ds, split, n_estimators=20)
    dst = predict_cog(tif, tmp_path / "out.tif", model, window_size=8, overlap=4)
    with rasterio.open(dst) as src:
        out = src.read(1)
    truth = _truth()
    assert out[3, 3] == NODATA
    ok = out != NODATA
    assert (out[ok] == truth[ok]).mean() > 0.95


def test_requires_raw_dataset(setup: Setup) -> None:
    tif, _, split = setup
    ds = PatchDataset(tif, size=8, labels=_truth(), nan_policy="zero")
    with pytest.raises(ValueError):
        train_rf(ds, split)
