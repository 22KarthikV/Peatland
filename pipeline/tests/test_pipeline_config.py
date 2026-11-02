import json
from pathlib import Path

import pytest
from pipeline.config import load_config
from pipeline.log import RunLogger
from pydantic import ValidationError

YAML = """
site: glen-trool
boundary_path: boundary.geojson
window: {start: 2025-03-01, end: 2025-05-31}
cloud_threshold: 30
"""


def test_config_loads_and_resolves_boundary(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(YAML)
    cfg = load_config(p)
    assert cfg.site == "glen-trool"
    assert cfg.boundary_path == tmp_path / "boundary.geojson"
    assert cfg.buckets.data == "peatwatch-data"


def test_config_rejects_bad_window(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(YAML.replace("2025-05-31", "2025-01-01"))
    with pytest.raises(ValidationError, match="before start"):
        load_config(p)


def test_config_rejects_unknown_key_and_bad_threshold(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(YAML + "bogus: 1\n")
    with pytest.raises(ValidationError):
        load_config(p)
    p.write_text(YAML.replace("30", "130"))
    with pytest.raises(ValidationError):
        load_config(p)


def test_run_logger_writes_jsonl(tmp_path: Path) -> None:
    log = RunLogger(tmp_path / "runs" / "r1" / "log.jsonl", "r1")
    log.run_start(
        {"a": 1},
        {"code": "abc1234"},
        ["S2B_30UVG_20250319_0_L2A", "S2A_30UVG_20250301_0_L2A"],
    )
    log.event("done")
    lines = [json.loads(x) for x in log.path.read_text().splitlines()]
    assert lines[0]["scene_ids"][0] == "S2A_30UVG_20250301_0_L2A"
    assert lines[0]["parameters"] == {"a": 1}
    assert lines[1]["event"] == "done"
    assert lines[1]["run_id"] == "r1"
