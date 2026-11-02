"""Contract C2: sidecar schema and STAC item template."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
KEY_A = "a" * 64
KEY_B = "b" * 64


def load_json(name: str) -> Any:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def band_names() -> list[str]:
    data = yaml.safe_load((ROOT / "bands.yaml").read_text(encoding="utf-8"))
    return [b["name"] for b in data["sentinel2_l2a"]["bands"]] + [
        f["name"] for f in data["features"]
    ]


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = load_json("sidecar.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def composite_sidecar() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "composite",
        "key": "composites/glen-trool/2025/w20250408-20250408.tif",
        "site": "glen-trool",
        "run_id": "run-0001",
        "date_range": {"start": "2025-04-08", "end": "2025-04-08"},
        "year": 2025,
        "season": "w20250408-20250408",
        "crs": "EPSG:32630",
        "resolution_m": 10,
        "dtype": "float32",
        "nodata": "nan",
        "bands": band_names(),
        "scene_ids": ["S2B_30UVG_20250408_0_L2A", "S2B_30UVF_20250408_0_L2A"],
        "versions": {
            "code": "8853f78",
            "package": "pipeline 0.1.0",
            "model": None,
            "classes_contract": 1,
            "bands_contract": 1,
        },
        "parameters": {"collection": "sentinel-2-l2a", "max_cloud_pct": 40, "method": "median"},
        "cloud_stats": {
            "scene_count": 2,
            "mean_scene_cloud_pct": 2.1,
            "valid_pixel_fraction": 0.97,
            "min_valid_observations": 1,
        },
        "inputs": [],
        "idempotency_key": KEY_A,
        "created_at": "2026-11-02T12:00:00Z",
    }


def change_sidecar() -> dict[str, Any]:
    side = composite_sidecar()
    side.update(
        kind="change",
        key="changes/glen-trool/2025-w20250319-20250319__2025-w20250408-20250408/unet-1.0.0.tif",
        date_range={"start": "2025-03-19", "end": "2025-04-08"},
        change_pair={
            "from": {"year": 2025, "season": "w20250319-20250319"},
            "to": {"year": 2025, "season": "w20250408-20250408"},
        },
        dtype="uint8",
        nodata=255,
        bands=["change"],
        scene_ids=[],
        inputs=[KEY_A, KEY_B],
        idempotency_key="c" * 64,
    )
    side["versions"]["model"] = "unet-1.0.0"
    return side


def test_composite_sidecar_valid(validator: Draft202012Validator) -> None:
    validator.validate(composite_sidecar())


def test_change_sidecar_valid(validator: Draft202012Validator) -> None:
    validator.validate(change_sidecar())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dtype", "uint8"),
        ("crs", "EPSG:27700"),
        ("idempotency_key", "not-a-hash"),
        ("season", "midsummer"),
        ("scene_ids", []),
        ("inputs", [KEY_B]),
        ("bands", ["B01"]),
    ],
)
def test_composite_sidecar_rejects(
    validator: Draft202012Validator, field: str, value: object
) -> None:
    side = composite_sidecar()
    side[field] = value
    assert not validator.is_valid(side)


def test_sidecar_rejects_missing_and_extra(validator: Draft202012Validator) -> None:
    side = composite_sidecar()
    del side["created_at"]
    assert not validator.is_valid(side)
    side = composite_sidecar()
    side["unexpected"] = 1
    assert not validator.is_valid(side)
    side = change_sidecar()
    del side["change_pair"]
    assert not validator.is_valid(side)


def test_composite_band_count_matches_bands_yaml() -> None:
    assert len(band_names()) == 18


def test_stac_item_required_fields() -> None:
    item = load_json("stac-item.example.json")
    required = ("type", "stac_version", "id", "geometry", "bbox", "properties", "links", "assets")
    for field in required:
        assert field in item, field
    assert item["type"] == "Feature"
    assert item["stac_version"] == "1.0.0"
    assert len(item["bbox"]) == 4
    props = item["properties"]
    assert "datetime" in props
    if props["datetime"] is None:
        assert props["start_datetime"] <= props["end_datetime"]
    assert props["proj:code"] == "EPSG:32630"
    assert any(link["rel"] == "self" for link in item["links"])
    for asset in item["assets"].values():
        assert "href" in asset
        assert "type" in asset


def test_stac_item_matches_layout_and_bands() -> None:
    item = load_json("stac-item.example.json")
    data = item["assets"]["data"]
    assert data["type"] == "image/tiff; application=geotiff; profile=cloud-optimized"
    key = data["href"].removeprefix("s3://peatwatch-data/")
    assert key.startswith("composites/")
    assert item["id"] == key.removesuffix(".tif").replace("/", ".")
    assert item["assets"]["sidecar"]["href"].endswith(key.removesuffix(".tif") + ".json")
    assert [b["name"] for b in data["eo:bands"]] == band_names()
    assert len(data["raster:bands"]) == len(band_names())
    assert all(b["data_type"] == "float32" for b in data["raster:bands"])
