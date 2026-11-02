import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_classes_valid() -> None:
    data = load("classes.yaml")
    classes = data["classes"]
    ids = [c["id"] for c in classes]
    assert ids == list(range(len(ids)))
    assert data["nodata"] not in ids
    assert 0 <= data["nodata"] <= 255
    assert len({c["name"] for c in classes}) == len(classes)
    assert len({c["hex"] for c in classes}) == len(classes)
    assert all(re.fullmatch(r"#[0-9A-Fa-f]{6}", c["hex"]) for c in classes)


def test_bands_valid() -> None:
    data = load("bands.yaml")
    names = [b["name"] for b in data["sentinel2_l2a"]["bands"]]
    assert len(names) == 12
    assert len(set(names)) == 12
    assert "B10" not in names
    feats = [f["name"] for f in data["features"]]
    assert len(set(feats)) == len(feats)
    assert not set(feats) & set(names)
