from __future__ import annotations

from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
import rasterio
from ml.baseline_ndvi import classify_array
from ml.cog import class_colormap
from ml.infer import HardLabelAdapter, hann_weights, ndvi_baseline_model, predict_cog
from rasterio.transform import from_origin

H, W, C, K = 700, 900, 18, 4


def _make_composite(path: Path, h: int = H, w: int = W, seed: int = 0) -> Path:
    rng = np.random.default_rng(seed)
    a = rng.random((C, h, w), dtype=np.float32)
    a[12] = rng.uniform(0.0, 0.9, (h, w))  # ndvi
    a[13] = rng.uniform(-0.5, 0.2, (h, w))  # ndwi
    a[14] = rng.uniform(-0.3, 0.3, (h, w))  # ndmi
    a[15] = rng.uniform(0, 500, (h, w))  # elevation
    a[:, :5, :7] = np.nan
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=w,
        height=h,
        count=C,
        dtype="float32",
        crs="EPSG:32630",
        transform=from_origin(400000, 6000000, 10, 10),
        nodata=np.nan,
    ) as dst:
        dst.write(a)
    return path


@pytest.fixture(scope="module")
def composite(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _make_composite(tmp_path_factory.mktemp("c") / "comp.tif")


def _read(p: Path) -> npt.NDArray[np.uint8]:
    with rasterio.open(p) as s:
        return s.read(1)


class ConstModel:
    def __init__(self, cls: int) -> None:
        self.cls = cls

    def predict_proba(self, window: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        out = np.zeros((K, *window.shape[1:]), dtype=np.float32)
        out[self.cls] = 1.0
        return out


def test_constant_model_uniform(composite: Path, tmp_path: Path) -> None:
    out = predict_cog(composite, tmp_path / "o.tif", ConstModel(2), window_size=256, overlap=96)
    assert np.all(_read(out) == 2)


def test_window_size_independent(composite: Path, tmp_path: Path) -> None:
    m = ndvi_baseline_model()
    a = _read(predict_cog(composite, tmp_path / "a.tif", m, window_size=128, overlap=40))
    b = _read(predict_cog(composite, tmp_path / "b.tif", m, window_size=400, overlap=100))
    c = _read(predict_cog(composite, tmp_path / "c.tif", m, window_size=2000, overlap=0))
    np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(a, c)
    with rasterio.open(composite) as s:
        arr = s.read().astype(np.float32)
    np.testing.assert_array_equal(a, classify_array(arr))
    assert (a[:5, :7] == 255).all()


def test_blend_of_disagreeing_windows_is_defined(composite: Path, tmp_path: Path) -> None:
    class Ctx:
        def predict_proba(self, window: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
            out = np.zeros((K, *window.shape[1:]), dtype=np.float32)
            out[int(abs(window[0, 10, 10]) * 1e6) % 2] = 1.0  # per-window class
            return out

    out = _read(predict_cog(composite, tmp_path / "x.tif", Ctx(), window_size=128, overlap=64))
    assert set(np.unique(out)) <= {0, 1}


def test_hann_positive() -> None:
    w = hann_weights(8, 5)
    assert w.shape == (8, 5) and (w > 0).all()


def test_adapter_one_hot() -> None:
    lab = np.array([[0, 3], [255, 1]], dtype=np.uint8)
    p = HardLabelAdapter(lambda _a: lab).predict_proba(np.zeros((C, 2, 2), np.float32))
    assert p.shape == (K, 2, 2)
    assert p[3, 0, 1] == 1 and p[0, 0, 0] == 1 and np.isnan(p[:, 1, 0]).all()


def test_cog_valid(composite: Path, tmp_path: Path) -> None:
    out = predict_cog(composite, tmp_path / "o.tif", ndvi_baseline_model(), window_size=256)
    with rasterio.open(out) as s:
        assert s.dtypes == ("uint8",) and s.count == 1
        assert s.nodata == 255
        assert s.crs is not None and s.crs.to_epsg() == 32630
        assert abs(s.res[0] - 10) < 1e-9
        assert s.profile["tiled"] and s.block_shapes == [(512, 512)]
        assert s.compression is not None and s.compression.value == "DEFLATE"
        st = s.tags(ns="IMAGE_STRUCTURE")
        assert st["LAYOUT"] == "COG" and st["PREDICTOR"] == "2"
        assert s.overviews(1) == [2]  # 900x700 -> 450x350 fits one tile
        assert s.descriptions == ("class",)
        cmap = s.colormap(1)
        assert cmap[1] == (0, 114, 178, 255) and cmap[255][3] == 0
        for k, v in class_colormap().items():
            assert cmap[k] == v
        assert s.tags().get("TIFFTAG_DATETIME") is None
    with rasterio.open(out, OVERVIEW_LEVEL=0) as ov:
        assert ov.shape == (350, 450)


def test_deterministic(composite: Path, tmp_path: Path) -> None:
    m = ndvi_baseline_model()
    a = predict_cog(composite, tmp_path / "a.tif", m, window_size=300)
    b = predict_cog(composite, tmp_path / "b.tif", m, window_size=300)
    assert a.read_bytes() == b.read_bytes()


def test_tiny_raster(tmp_path: Path) -> None:
    src = _make_composite(tmp_path / "s.tif", 20, 30)
    out = predict_cog(src, tmp_path / "o.tif", ConstModel(1))
    assert _read(out).shape == (20, 30) and (_read(out) == 1).all()


def test_bad_args(composite: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        predict_cog(composite, tmp_path / "o.tif", ConstModel(0), window_size=64, overlap=64)
