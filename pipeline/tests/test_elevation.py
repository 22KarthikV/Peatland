"""Elevation tests with a synthetic GLO-30-like raster (no network)."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from pipeline.elevation import NoDemCoverageError, fetch_elevation
from rasterio.transform import from_bounds, from_origin
from rasterio.warp import transform as warp_transform

M_PER_DEG_LON = 111_320.0 * np.cos(np.radians(55.1))
GRADIENT = 0.1  # m/m eastward -> atan(0.1) = 5.71 degrees
LON0, LAT0, RES = -4.45, 55.15, 0.0004  # DEM: 250 x 250 px, EPSG:4326


class _Asset:
    def __init__(self, href: str) -> None:
        self.href = href


class _Item:
    def __init__(self, href: str) -> None:
        self.assets = {"data": _Asset(href)}


class _Result:
    def __init__(self, hrefs: list[str]) -> None:
        self._hrefs = hrefs

    def items(self) -> Any:
        return iter(_Item(h) for h in self._hrefs)


class FakeClient:
    def __init__(self, hrefs: list[str]) -> None:
        self.hrefs = hrefs
        self.kwargs: dict[str, Any] = {}

    def search(self, **kwargs: Any) -> _Result:
        self.kwargs = kwargs
        return _Result(self.hrefs)


def _write_dem(path: Path, nan_patch: bool = False) -> str:
    n = 250
    lon = LON0 + (np.arange(n) + 0.5) * RES
    z = np.tile(((lon - LON0) * M_PER_DEG_LON * GRADIENT + 100.0).astype(np.float32), (n, 1))
    if nan_patch:
        z[100:130, 100:130] = np.nan
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=n,
        width=n,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(LON0, LAT0, RES, RES),
        nodata=np.nan,
    ) as dst:
        dst.write(z, 1)
    return str(path)


def _grid() -> tuple[Any, int, int, str]:
    xs, ys = warp_transform("EPSG:4326", "EPSG:32630", [-4.40], [55.10])
    x, y = xs[0], ys[0]
    size = 60
    tr = from_bounds(x, y, x + size * 10, y + size * 10, size, size)
    return tr, size, size, "EPSG:32630"


def test_shape_dtype_and_slope(tmp_path: Path) -> None:
    href = _write_dem(tmp_path / "dem.tif")
    tr, w, h, crs = _grid()
    client = FakeClient([href])
    elev, slope, twi = fetch_elevation(tr, w, h, crs, client=client)
    for a in (elev, slope, twi):
        assert a.shape == (h, w)
        assert a.dtype == np.float32
    assert client.kwargs["collections"] == ["cop-dem-glo-30"]
    west, south, east, north = client.kwargs["bbox"]
    assert west < -4.40 < east and south < 55.10 < north
    assert not np.isnan(elev).any()
    assert np.nanmin(elev) > 100.0
    expected = np.degrees(np.arctan(GRADIENT))
    assert abs(float(np.median(slope[5:-5, 5:-5])) - expected) < 0.5
    assert np.isfinite(twi).all()


def test_nan_patch_propagates(tmp_path: Path) -> None:
    href = _write_dem(tmp_path / "dem.tif", nan_patch=True)
    tr, w, h, crs = _grid()
    elev, slope, twi = fetch_elevation(tr, w, h, crs, client=FakeClient([href]))
    assert np.isnan(elev).any()
    assert not np.isnan(elev).all()
    nan_cells = np.isnan(elev)
    # NaN elevation implies NaN slope and TWI at the same cells
    assert np.isnan(slope[nan_cells]).all()
    assert np.isnan(twi[nan_cells]).all()


def test_outside_tile_is_nan_and_no_coverage_raises(tmp_path: Path) -> None:
    href = _write_dem(tmp_path / "dem.tif")
    xs, ys = warp_transform("EPSG:4326", "EPSG:32630", [-5.5], [56.5])
    tr = from_bounds(xs[0], ys[0], xs[0] + 300, ys[0] + 300, 30, 30)
    elev, _, _ = fetch_elevation(tr, 30, 30, "EPSG:32630", client=FakeClient([href]))
    assert np.isnan(elev).all()
    with pytest.raises(NoDemCoverageError):
        fetch_elevation(tr, 30, 30, "EPSG:32630", client=FakeClient([]))
