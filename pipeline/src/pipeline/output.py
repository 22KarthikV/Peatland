"""COG writer, sidecar and idempotency key (contracts/cog-layout.md sections 4-6)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import yaml
from numpy.typing import NDArray
from rasterio.shutil import copy as rio_copy

from pipeline.composite import load_s2_bands
from pipeline.grid import Grid

BANDS_CONTRACT = 1
CLASSES_CONTRACT = 1
SCHEMA_VERSION = 1
BLOCK_SIZE = 512


def _contracts_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "contracts"
        if (cand / "sidecar.schema.json").is_file():
            return cand
    raise FileNotFoundError("contracts/sidecar.schema.json not found")


def band_order() -> list[str]:
    """The 18 composite band names: 12 S2 bands then 6 features (contracts/bands.yaml)."""
    raw = yaml.safe_load((_contracts_dir() / "bands.yaml").read_text(encoding="utf-8"))
    return load_s2_bands()[0] + [f["name"] for f in raw["features"]]


def stack_bands(
    composite: NDArray[np.float32], features: NDArray[np.float32] | Sequence[NDArray[np.float32]]
) -> NDArray[np.float32]:
    """Concatenate the (12, y, x) composite and (6, y, x) features into (18, y, x)."""
    feats = np.asarray(features, dtype=np.float32)
    return np.concatenate([np.asarray(composite, dtype=np.float32), feats], axis=0)


# ---------------------------------------------------------------- keys and tokens


def season_token(start: date, end: date) -> str:
    """Custom window token ``w<YYYYMMDD>-<YYYYMMDD>`` (inclusive dates)."""
    return f"w{start:%Y%m%d}-{end:%Y%m%d}"


def composite_key(site: str, start: date, end: date, season: str | None = None) -> str:
    """S3 key of the composite COG; the year is the window start's year."""
    return f"composites/{site}/{start.year}/{season or season_token(start, end)}.tif"


def sidecar_key(tif_key: str) -> str:
    return tif_key.removesuffix(".tif") + ".json"


def canonical_json(obj: Any) -> bytes:
    """Canonical JSON per cog-layout.md section 6."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def boundary_sha256(boundary_path: Path) -> str:
    return sha256_bytes(Path(boundary_path).read_bytes())


def git_sha(cwd: Path | None = None) -> str:
    """Current git commit sha of the producing package, or 0000000 if git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd or Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "0000000"


def idempotency_key(
    *,
    kind: str,
    site: str,
    boundary_sha: str,
    start: date,
    end: date,
    parameters: Mapping[str, Any],
    code_version: str,
    model: str | None = None,
    inputs: Sequence[str] = (),
) -> str:
    """Lowercase hex SHA-256 of the canonical JSON defined in cog-layout.md section 6."""
    payload = {
        "kind": kind,
        "site": site,
        "boundary_sha256": boundary_sha,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "parameters": dict(parameters),
        "versions": {
            "code": code_version,
            "model": model,
            "classes_contract": CLASSES_CONTRACT,
            "bands_contract": BANDS_CONTRACT,
        },
        "inputs": sorted(inputs),
    }
    return sha256_bytes(canonical_json(payload))


# ---------------------------------------------------------------- COG


def write_composite_cog(
    path: Path,
    bands: NDArray[np.float32],
    grid: Grid,
    band_names: Sequence[str] | None = None,
) -> Path:
    """Write an (18, y, x) float32 array as a tiled, overviewed, deflate COG.

    No wall-clock tags are written, so identical inputs give identical bytes.
    """
    names = list(band_names) if band_names is not None else band_order()
    if bands.ndim != 3 or bands.shape[0] != len(names):
        raise ValueError(f"expected ({len(names)}, y, x) array, got {bands.shape}")
    if bands.shape[1:] != grid.shape:
        raise ValueError(f"array {bands.shape[1:]} does not match grid {grid.shape}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.ascontiguousarray(bands, dtype=np.float32)
    with tempfile.TemporaryDirectory() as tmp:
        src_path = Path(tmp) / "src.tif"
        with rasterio.open(
            src_path,
            "w",
            driver="GTiff",
            width=grid.width,
            height=grid.height,
            count=len(names),
            dtype="float32",
            crs=grid.crs,
            transform=grid.transform,
            nodata=float("nan"),
        ) as dst:
            dst.write(data)
            for i, name in enumerate(names, start=1):
                dst.set_band_description(i, name)
        rio_copy(
            src_path,
            path,
            driver="COG",
            BLOCKSIZE=BLOCK_SIZE,
            COMPRESS="DEFLATE",
            PREDICTOR=3,
            INTERLEAVE="BAND",
            OVERVIEW_RESAMPLING="AVERAGE",
            BIGTIFF="IF_SAFER",
            NUM_THREADS=1,
        )
    return path


# ---------------------------------------------------------------- sidecar


class SceneNotBoaCorrectedError(ValueError):
    """Raised when a scene has not had the baseline-04.00 BOA offset removed."""


def build_sidecar(
    *,
    site: str,
    start: date,
    end: date,
    key: str,
    bands: Sequence[str],
    scene_ids: Sequence[str],
    scene_boa_offset_applied: Mapping[str, bool],
    parameters: Mapping[str, Any],
    boundary_sha: str,
    cloud_stats: Mapping[str, Any],
    code_version: str,
    season: str | None = None,
    package: str | None = None,
    run_id: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Build and validate a composite sidecar.

    ``scene_boa_offset_applied`` maps every scene id to Earth Search's
    ``earthsearch:boa_offset_applied``; scenes where it is false (or missing) are
    refused. The sidecar schema forbids extra top-level fields, so the per-scene
    flags are recorded under ``parameters["boa_offset_applied"]`` (and so are hashed).
    """
    bad = [s for s in scene_ids if scene_boa_offset_applied.get(s) is not True]
    if bad:
        raise SceneNotBoaCorrectedError(
            f"boa_offset_applied is not true for scenes: {', '.join(sorted(bad))}"
        )
    params = dict(parameters)
    params["boa_offset_applied"] = {s: True for s in sorted(scene_ids)}
    ikey = idempotency_key(
        kind="composite",
        site=site,
        boundary_sha=boundary_sha,
        start=start,
        end=end,
        parameters=params,
        code_version=code_version,
    )
    sidecar: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "composite",
        "key": key,
        "site": site,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "year": start.year,
        "season": season or season_token(start, end),
        "crs": "EPSG:32630",
        "resolution_m": 10,
        "dtype": "float32",
        "nodata": "nan",
        "bands": list(bands),
        "scene_ids": sorted(scene_ids),
        "versions": {
            "code": code_version,
            "model": None,
            "classes_contract": CLASSES_CONTRACT,
            "bands_contract": BANDS_CONTRACT,
        },
        "parameters": params,
        "cloud_stats": dict(cloud_stats),
        "inputs": [],
        "idempotency_key": ikey,
        "created_at": (created_at or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if package:
        sidecar["versions"]["package"] = package
    if run_id:
        sidecar["run_id"] = run_id
    validate_sidecar(sidecar)
    return sidecar


def validate_sidecar(sidecar: Mapping[str, Any]) -> None:
    """Validate against contracts/sidecar.schema.json (JSON Schema 2020-12)."""
    import jsonschema  # type: ignore[import-untyped,unused-ignore]  # lazy; dep request

    schema = json.loads((_contracts_dir() / "sidecar.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    ).validate(dict(sidecar))


def write_sidecar(path: Path, sidecar: Mapping[str, Any]) -> Path:
    validate_sidecar(sidecar)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def cloud_stats(
    bands: NDArray[np.float32],
    coverage: NDArray[np.uint16],
    scene_cloud_pct: Sequence[float],
) -> dict[str, Any]:
    """Cloud/coverage statistics for the sidecar from the composite and coverage count."""
    valid = ~np.all(np.isnan(bands), axis=0)
    return {
        "scene_count": len(scene_cloud_pct),
        "mean_scene_cloud_pct": (
            round(float(np.mean(scene_cloud_pct)), 3) if len(scene_cloud_pct) else None
        ),
        "valid_pixel_fraction": float(valid.mean()) if valid.size else 0.0,
        "min_valid_observations": int(coverage.min()) if coverage.size else 0,
    }
