import json
from pathlib import Path

import pytest
from pipeline.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_cli_run_help() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "YAML run config" in result.output


def test_cli_run_writes_log(tmp_path: Path) -> None:
    out = (tmp_path / "out").as_posix()
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        f"site: glen-trool\nboundary_path: b.geojson\noutput_dir: '{out}'\n"
        "window: {start: 2025-03-01, end: 2025-05-31}\n"
    )
    result = runner.invoke(app, ["run", str(cfg), "--run-id", "r1"])
    # The boundary file does not exist: the run fails after logging its start.
    assert result.exit_code == 1, result.output
    first = (tmp_path / "out" / "runs" / "r1" / "log.jsonl").read_text().splitlines()[0]
    line = json.loads(first)
    assert line["parameters"]["cloud_threshold"] == 40.0
    assert "code" in line["versions"]


def test_cli_missing_config_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 2


def test_cli_zero_scenes_exits_3_with_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipeline.stac import NoUsableScenesError

    from pipeline import runner

    def _none(*_: object, **__: object) -> None:
        raise NoUsableScenesError("no Sentinel-2 L2A scenes with cloud cover <= 40%")

    monkeypatch.setattr(runner, "search_scenes", _none)
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        f"site: glen-trool\nboundary_path: b.geojson\noutput_dir: '{(tmp_path / 'o').as_posix()}'\n"
        "window: {start: 2025-03-01, end: 2025-05-31}\n"
    )
    result = CliRunner().invoke(app, ["run", str(cfg), "--run-id", "r2"])
    assert result.exit_code == 3
    assert "no Sentinel-2 L2A scenes" in result.output
