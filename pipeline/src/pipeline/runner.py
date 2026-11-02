"""End-to-end run: STAC -> composite -> features -> COG + sidecar -> S3."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from numpy.typing import NDArray

from pipeline.composite import build_composite, load_s2_bands
from pipeline.config import RunConfig
from pipeline.elevation import fetch_elevation
from pipeline.features import ndmi, ndvi, ndwi
from pipeline.grid import build_grid
from pipeline.log import RunLogger
from pipeline.output import (
    band_order,
    boundary_sha256,
    build_sidecar,
    cloud_stats,
    composite_key,
    git_sha,
    sidecar_key,
    stack_bands,
    write_composite_cog,
    write_sidecar,
)
from pipeline.stac import SceneRecord, StacClient, load_geometry, search_scenes
from pipeline.store import Outcome, is_up_to_date, s3_client, upload_product

PLACEHOLDER_STATS: dict[str, Any] = {
    "scene_count": 0,
    "mean_scene_cloud_pct": None,
    "valid_pixel_fraction": 0.0,
    "min_valid_observations": 0,
}


@dataclass(frozen=True)
class RunResult:
    outcome: Outcome
    key: str
    scene_ids: list[str]
    tif_path: Path | None
    sidecar_path: Path | None


def _features(
    composite: NDArray[np.float32],
    elevation: NDArray[np.float32],
    slope: NDArray[np.float32],
    twi: NDArray[np.float32],
) -> NDArray[np.float32]:
    """Feature stack in contracts/bands.yaml order: ndvi, ndwi, ndmi, elevation, slope, twi."""
    idx = {n: i for i, n in enumerate(load_s2_bands()[0])}
    feats = [
        ndvi(composite[idx["B08"]], composite[idx["B04"]]),
        ndwi(composite[idx["B03"]], composite[idx["B08"]]),
        ndmi(composite[idx["B08"]], composite[idx["B11"]]),
        elevation,
        slope,
        twi,
    ]
    return np.stack(feats).astype(np.float32)


def run_pipeline(
    cfg: RunConfig,
    logger: RunLogger,
    *,
    package: str | None = None,
    stac_client: StacClient | None = None,
    dem_client: StacClient | None = None,
    dem_opener: Callable[[str], Any] = rasterio.open,
    s3: Any | None = None,
) -> RunResult:
    """Run one site/window. Raises NoUsableScenesError / SceneNotBoaCorrectedError."""
    t0 = time.monotonic()
    scenes: list[SceneRecord] = search_scenes(
        cfg.boundary_path, cfg.window, cfg.cloud_threshold, client=stac_client
    )
    scene_ids = [s.id for s in scenes]
    logger.event(
        "scenes_selected",
        scene_ids=scene_ids,
        cloud_cover={s.id: s.cloud_cover for s in scenes},
    )

    start, end = cfg.window.start, cfg.window.end
    key = composite_key(cfg.site, start, end)
    params: dict[str, Any] = {
        "cloud_threshold": cfg.cloud_threshold,
        "composite": "median",
        "grid_buffer_m": 0,
        **cfg.parameters,
    }
    common: dict[str, Any] = {
        "site": cfg.site,
        "start": start,
        "end": end,
        "key": key,
        "bands": band_order(),
        "scene_ids": scene_ids,
        "scene_boa_offset_applied": {s.id: s.boa_offset_applied for s in scenes},
        "parameters": params,
        "boundary_sha": boundary_sha256(cfg.boundary_path),
        "code_version": git_sha(),
        "package": package,
        "run_id": logger.run_id,
    }
    # Preliminary sidecar: fixes the idempotency key and refuses non-BOA-corrected scenes
    # before any heavy raster work.
    prelim = build_sidecar(cloud_stats=PLACEHOLDER_STATS, **common)
    client = s3 or s3_client()
    bucket = cfg.buckets.data
    if is_up_to_date(client, bucket, key, prelim["idempotency_key"]):
        logger.event("skipped", reason="idempotency key already stored", key=key)
        return RunResult("skipped", key, scene_ids, None, None)

    grid = build_grid(load_geometry(cfg.boundary_path))
    logger.event("grid", width=grid.width, height=grid.height, bounds=grid.bounds)
    composite, coverage = build_composite([(s.assets, s.assets["SCL"]) for s in scenes], grid)
    logger.event("composite_done", seconds=round(time.monotonic() - t0, 1))
    elevation, slope, twi = fetch_elevation(
        grid.transform,
        grid.width,
        grid.height,
        grid.crs,
        client=dem_client,
        opener=dem_opener,
    )
    bands = stack_bands(composite, _features(composite, elevation, slope, twi))

    stats = cloud_stats(bands, coverage, [s.cloud_cover for s in scenes])
    sidecar = build_sidecar(cloud_stats=stats, **common)
    tif = write_composite_cog(cfg.output_dir / key, bands, grid)
    side = write_sidecar(cfg.output_dir / sidecar_key(key), sidecar)
    outcome = upload_product(tif, side, bucket, client=client)
    logger.event(
        "finished",
        outcome=outcome,
        key=key,
        bucket=bucket,
        cloud_stats=stats,
        seconds=round(time.monotonic() - t0, 1),
    )
    return RunResult(outcome, key, scene_ids, tif, side)
