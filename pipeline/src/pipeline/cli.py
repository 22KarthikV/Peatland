"""Command line entry point: ``peatwatch-pipeline``."""

from __future__ import annotations

import subprocess
import uuid
from importlib import metadata
from pathlib import Path
from typing import Annotated

import typer

from pipeline.config import RunConfig, load_config
from pipeline.log import RunLogger

app = typer.Typer(help="PeatWatch Sentinel-2 pipeline.", no_args_is_help=True)


def _code_version() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _package_version() -> str:
    try:
        return metadata.version("peatwatch-pipeline")
    except metadata.PackageNotFoundError:
        return "unknown"


def start_run(cfg: RunConfig, run_id: str | None = None) -> RunLogger:
    """Create the run log and record resolved parameters and versions."""
    rid = run_id or uuid.uuid4().hex[:12]
    logger = RunLogger(cfg.output_dir / "runs" / rid / "log.jsonl", rid)
    logger.run_start(
        parameters=cfg.model_dump(mode="json"),
        versions={"code": _code_version(), "package": f"pipeline {_package_version()}"},
        scene_ids=[],
    )
    return logger


@app.callback()
def main() -> None:
    """PeatWatch pipeline commands."""


@app.command()
def run(
    config: Annotated[Path, typer.Argument(help="Path to the YAML run config.")],
    run_id: Annotated[str | None, typer.Option(help="Override the generated run id.")] = None,
) -> None:
    """Run the pipeline for one site and date window."""
    try:
        cfg = load_config(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    logger = start_run(cfg, run_id)
    typer.echo(f"run {logger.run_id} started; log at {logger.path}")
