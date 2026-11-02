"""NDVI baseline rules and the evaluation harness on small synthetic composites."""

import json
from pathlib import Path

import numpy as np
import rasterio
from ml.baseline_ndvi import (
    BARE,
    BOG,
    ELEVATION_BAND,
    NDMI_BAND,
    NDVI_BAND,
    NDWI_BAND,
    NODATA,
    OTHER,
    WOODLAND,
    NdviBaseline,
    classify_array,
)
from ml.dataset import Patch, PatchDataset
from ml.evaluate import evaluate, metrics_to_dict, write_metrics_json
from ml.metrics import segmentation_metrics
from ml.split import SplitResult
from rasterio.transform import from_origin


def _arr(ndvi: float, ndwi: float = -0.3, ndmi: float = 0.2, elev: float = 300.0) -> np.ndarray:
    a = np.zeros((18, 1, 1), np.float32)
    a[NDVI_BAND], a[NDWI_BAND], a[NDMI_BAND], a[ELEVATION_BAND] = ndvi, ndwi, ndmi, elev
    return a


def test_rules() -> None:
    cases = [
        (_arr(0.1), BARE),
        (_arr(0.8), WOODLAND),
        (_arr(0.4), BOG),
        (_arr(0.4, ndmi=-0.2), OTHER),
        (_arr(0.4, elev=900.0), OTHER),
        (_arr(0.1, ndwi=0.6), OTHER),  # water
        (_arr(np.nan), NODATA),
    ]
    for arr, want in cases:
        assert classify_array(arr)[0, 0] == want


def _write(path: Path, truth: np.ndarray, size: int) -> None:
    data = np.zeros((18, size, size), np.float32)
    data[NDWI_BAND], data[NDMI_BAND], data[ELEVATION_BAND] = -0.3, 0.2, 300.0
    data[NDVI_BAND][truth == WOODLAND] = 0.8
    data[NDVI_BAND][truth == BOG] = 0.4
    data[NDVI_BAND][truth == BARE] = 0.1
    data[NDVI_BAND][truth == OTHER] = 0.4
    data[NDMI_BAND][truth == OTHER] = -0.3
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=size,
        width=size,
        count=18,
        dtype="float32",
        crs="EPSG:32630",
        transform=from_origin(400000, 6100000, 10, 10),
        nodata=np.nan,
    ) as dst:
        dst.write(data)


def _bog_everywhere(patch: Patch) -> np.ndarray:
    assert patch.label is not None
    return np.full(patch.label.shape, BOG, np.uint8)


def test_evaluate_perfect_and_json(tmp_path: Path) -> None:
    size = 16
    truth = np.zeros((size, size), np.uint8)
    truth[:, 4:8], truth[:, 8:12], truth[:, 12:] = BOG, WOODLAND, BARE
    truth[0, 0] = 255  # unlabelled
    tif = tmp_path / "c.tif"
    _write(tif, truth, size)
    ds = PatchDataset(tif, size=8, labels=truth)
    split = SplitResult(["test", "train", "test", "val"], {}, 5000.0, 0.0, 0)

    m = evaluate(NdviBaseline(), ds, split)
    assert m.mean_iou == 1.0
    assert m.confusion.sum() == 2 * 64 - 1  # test patches 0 and 2, one unlabelled pixel

    bad = evaluate(_bog_everywhere, ds, split)
    assert bad.mean_iou < 1.0
    assert metrics_to_dict(bad)["iou"][OTHER] == 0.0

    out = write_metrics_json(tmp_path / "sub" / "m.json", m, {"model": "ndvi"})
    loaded = json.loads(out.read_text("utf-8"))
    assert loaded["model"] == "ndvi"
    assert loaded["classes"][BOG] == "bog"
    assert loaded["mean_iou"] == 1.0


def test_nan_to_null(tmp_path: Path) -> None:
    m = segmentation_metrics(np.array([1, 1], np.uint8), np.array([1, 1], np.uint8))
    out = write_metrics_json(tmp_path / "m.json", m)
    loaded = json.loads(out.read_text("utf-8"))
    assert loaded["iou"] == [None, 1.0, None, None]


def test_empty_split(tmp_path: Path) -> None:
    truth = np.zeros((8, 8), np.uint8)
    tif = tmp_path / "c.tif"
    _write(tif, truth, 8)
    ds = PatchDataset(tif, size=8, labels=truth)
    m = evaluate(NdviBaseline(), ds, SplitResult(["train"], {}, 5000.0, 0.0, 0))
    assert m.confusion.sum() == 0 and np.isnan(m.mean_iou)
