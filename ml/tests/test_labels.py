"""Label rasterisation on a synthetic grid."""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from ml.dataset import PatchDataset
from ml.labels import (
    load_classes,
    rasterize_handlabels,
    rasterize_labels,
    rasterize_to_composite,
    read_handlabels,
)
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

TF = from_origin(400000.0, 6100000.0, 10, 10)  # 100 x 100 px = 1 km square
SHAPE = (100, 100)
CRS = "EPSG:32630"


def _box(c0: int, r0: int, c1: int, r1: int) -> Polygon:
    """Polygon covering pixel cols c0..c1, rows r0..r1 (exclusive end)."""
    return box(400000 + c0 * 10, 6100000 - r1 * 10, 400000 + c1 * 10, 6100000 - r0 * 10)


def test_classes_contract() -> None:
    mapping, nodata = load_classes()
    assert mapping == {"other": 0, "bog": 1, "woodland": 2, "felled_or_burnt_bare": 3}
    assert nodata == 255


def test_basic_burn_and_nodata() -> None:
    gdf = gpd.GeoDataFrame(
        {"class": ["bog", "woodland"]},
        geometry=[_box(0, 0, 10, 10), _box(20, 20, 30, 40)],
        crs=CRS,
    )
    out = rasterize_labels(gdf, transform=TF, shape=SHAPE, crs=CRS)
    assert out.dtype == np.uint8 and out.shape == SHAPE
    assert (out[0:10, 0:10] == 1).all()
    assert (out[20:40, 20:30] == 2).all()
    assert (out == 255).sum() == 100 * 100 - 100 - 200


def test_integer_class_column_and_custom_column() -> None:
    gdf = gpd.GeoDataFrame({"cls": [3, 0]}, geometry=[_box(0, 0, 5, 5), _box(5, 0, 10, 5)], crs=CRS)
    out = rasterize_labels(gdf, transform=TF, shape=SHAPE, crs=CRS, class_column="cls")
    assert (out[0:5, 0:5] == 3).all() and (out[0:5, 5:10] == 0).all()


def test_overlap_later_wins() -> None:
    a, b = _box(0, 0, 20, 20), _box(10, 10, 30, 30)
    gdf = gpd.GeoDataFrame({"class": ["bog", "woodland"]}, geometry=[a, b], crs=CRS)
    out = rasterize_labels(gdf, transform=TF, shape=SHAPE, crs=CRS)
    assert out[15, 15] == 2 and out[5, 5] == 1
    rev = rasterize_labels(gdf.iloc[::-1], transform=TF, shape=SHAPE, crs=CRS)
    assert rev[15, 15] == 1 and rev[25, 25] == 2


def test_reproject_from_other_crs() -> None:
    poly = _box(10, 10, 30, 30)
    t_to_27700 = Transformer.from_crs(CRS, "EPSG:27700", always_xy=True)
    t_to_4326 = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    for epsg, tr in (("EPSG:27700", t_to_27700), ("EPSG:4326", t_to_4326)):
        coords = [tr.transform(x, y) for x, y in poly.exterior.coords]
        gdf = gpd.GeoDataFrame({"class": ["bog"]}, geometry=[Polygon(coords)], crs=epsg)
        out = rasterize_labels(gdf, transform=TF, shape=SHAPE, crs=CRS)
        # reprojection of straight edges is only approximately axis-aligned
        assert (out[12:28, 12:28] == 1).all()
        assert (out[0:8, :] == 255).all()
        assert abs(int((out == 1).sum()) - 400) < 60


def test_errors_and_skips() -> None:
    gdf = gpd.GeoDataFrame({"class": ["heath"]}, geometry=[_box(0, 0, 5, 5)], crs=CRS)
    with pytest.raises(ValueError, match="unknown class name"):
        rasterize_labels(gdf, transform=TF, shape=SHAPE, crs=CRS)
    with pytest.raises(ValueError, match="unknown class id"):
        rasterize_labels(gdf.assign(**{"class": 9}), transform=TF, shape=SHAPE, crs=CRS)
    with pytest.raises(KeyError):
        rasterize_labels(gdf, transform=TF, shape=SHAPE, crs=CRS, class_column="nope")
    nocrs = gpd.GeoDataFrame({"class": ["bog"]}, geometry=[_box(0, 0, 5, 5)])
    with pytest.raises(ValueError, match="no CRS"):
        rasterize_labels(nocrs, transform=TF, shape=SHAPE, crs=CRS)
    assert (rasterize_labels(nocrs, transform=TF, shape=SHAPE, crs=CRS, assume_crs=CRS) == 1).any()
    empty = gpd.GeoDataFrame({"class": []}, geometry=[], crs=CRS)
    assert (rasterize_labels(empty, transform=TF, shape=SHAPE, crs=CRS) == 255).all()
    # polygon outside the grid and null geometry are ignored
    far = gpd.GeoDataFrame(
        {"class": ["bog", "bog"]}, geometry=[_box(500, 500, 510, 510), None], crs=CRS
    )
    assert (rasterize_labels(far, transform=TF, shape=SHAPE, crs=CRS) == 255).all()


def test_handlabels_low_confidence_excluded(tmp_path: Path) -> None:
    def feat(cls: str, conf: str, ring: list[list[float]]) -> dict[str, object]:
        return {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "class": cls,
                "label_date": "2025-04-08",
                "confidence": conf,
                "source": "S2",
                "labeller": "KM",
            },
        }

    inv = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)

    def ring(c0: int, r0: int, c1: int, r1: int) -> list[list[float]]:
        pts = list(_box(c0, r0, c1, r1).exterior.coords)
        return [list(inv.transform(x, y)) for x, y in pts]

    fc = {
        "type": "FeatureCollection",
        "features": [
            feat("bog", "high", ring(0, 0, 20, 20)),
            feat("woodland", "low", ring(40, 40, 60, 60)),
        ],
    }
    path = tmp_path / "hl.geojson"
    path.write_text(json.dumps(fc), "utf-8")
    gdf = read_handlabels(path)
    assert len(gdf) == 2
    out = rasterize_handlabels(gdf, transform=TF, shape=SHAPE, crs=CRS)
    assert out[10, 10] == 1 and out[50, 50] == 255
    out_all = rasterize_handlabels(
        gdf, transform=TF, shape=SHAPE, crs=CRS, include_low_confidence=True
    )
    assert out_all[50, 50] == 2

    bad = tmp_path / "bad.geojson"
    fc["features"][0]["properties"]["class"] = "heath"  # type: ignore[index]
    bad.write_text(json.dumps(fc), "utf-8")
    with pytest.raises(Exception):  # noqa: B017  jsonschema.ValidationError
        read_handlabels(bad)


def test_rasterize_to_composite_feeds_dataset(tmp_path: Path) -> None:
    path = tmp_path / "comp.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=300,
        width=300,
        count=2,
        dtype="float32",
        crs=CRS,
        transform=from_origin(400000.0, 6100000.0, 10, 10),
        nodata=np.nan,
    ) as dst:
        dst.write(np.ones((2, 300, 300), dtype=np.float32))
    gdf = gpd.GeoDataFrame(
        {"class": ["felled_or_burnt_bare"]}, geometry=[_box(0, 0, 256, 256)], crs=CRS
    )
    labels = rasterize_to_composite(gdf, path)
    assert labels.shape == (300, 300)
    p = PatchDataset(path, labels=labels)[0]
    assert p.label is not None and (p.label == 3).all()
