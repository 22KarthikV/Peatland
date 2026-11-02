"""Check the hand-label template loads and the schema accepts/rejects features."""

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import jsonschema
import pytest

LABELS = Path(__file__).resolve().parents[2] / "docs" / "labels"


def _schema() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((LABELS / "handlabel.schema.json").read_text("utf-8"))
    return data


def _feature(**overrides: object) -> dict[str, Any]:
    props: dict[str, Any] = {
        "class": "bog",
        "label_date": "2025-04-08",
        "confidence": "high",
        "source": "S2 2025-04-08 + aerial basemap",
        "labeller": "KM",
        "notes": "",
    }
    props.update(overrides)
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-4.2, 55.0], [-4.199, 55.0], [-4.199, 55.001], [-4.2, 55.0]]],
        },
        "properties": props,
    }


def test_template_loads_and_validates() -> None:
    template = json.loads((LABELS / "handlabel.template.geojson").read_text("utf-8"))
    jsonschema.validate(template, _schema())
    assert template["features"] == []
    gdf = gpd.read_file(LABELS / "handlabel.template.geojson")
    assert len(gdf) == 0


def test_sample_feature_validates_and_loads() -> None:
    collection = {"type": "FeatureCollection", "features": [_feature()]}
    jsonschema.validate(collection, _schema())
    gdf = gpd.GeoDataFrame.from_features(collection["features"], crs="EPSG:4326")
    assert list(gdf["class"]) == ["bog"]


@pytest.mark.parametrize(
    "bad",
    [
        {"class": "heath"},
        {"label_date": "08/04/2025"},
        {"confidence": "certain"},
        {"labeller": ""},
        {"extra": 1},
    ],
)
def test_bad_feature_rejected(bad: dict[str, object]) -> None:
    collection = {"type": "FeatureCollection", "features": [_feature(**bad)]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(collection, _schema())
