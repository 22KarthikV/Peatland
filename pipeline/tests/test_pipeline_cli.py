import json
from pathlib import Path

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
    assert result.exit_code == 0, result.output
    first = (tmp_path / "out" / "runs" / "r1" / "log.jsonl").read_text().splitlines()[0]
    line = json.loads(first)
    assert line["parameters"]["cloud_threshold"] == 40.0
    assert "code" in line["versions"]


def test_cli_missing_config_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 2
