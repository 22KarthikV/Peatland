"""Per-pixel median compositing and scene reprojection onto the target grid."""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import yaml
from numpy.typing import NDArray
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import reproject

from pipeline.grid import Grid
from pipeline.mask import valid_mask


def _find_bands_yaml() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "contracts" / "bands.yaml"
        if cand.is_file():
            return cand
    raise FileNotFoundError("contracts/bands.yaml not found")


def load_s2_bands(path: Path | None = None) -> tuple[list[str], float]:
    """Return (ordered Sentinel-2 band names, DN scale) from contracts/bands.yaml."""
    raw = yaml.safe_load((path or _find_bands_yaml()).read_text(encoding="utf-8"))
    s2 = raw["sentinel2_l2a"]
    return [b["name"] for b in s2["bands"]], float(s2["scale"])


def median_composite(
    scenes: NDArray[np.floating],
    valid: NDArray[np.bool_],
) -> tuple[NDArray[np.float32], NDArray[np.uint16]]:
    """Median over scenes of valid observations.

    ``scenes`` is (scene, band, y, x) float32; ``valid`` is (scene, y, x) bool
    (from ``valid_mask``). NaN band values are also ignored, per band. Returns
    (composite (band, y, x) float32, NaN where no valid observation;
    coverage (y, x) uint16 = number of valid scenes).
    """
    if scenes.ndim != 4:
        raise ValueError("scenes must be (scene, band, y, x)")
    if valid.shape != (scenes.shape[0], scenes.shape[2], scenes.shape[3]):
        raise ValueError("valid must be (scene, y, x) matching scenes")
    if scenes.shape[0] == 0:
        comp = np.full(scenes.shape[1:], np.nan, dtype=np.float32)
        return comp, np.zeros(scenes.shape[2:], dtype=np.uint16)
    masked = np.where(valid[:, None, :, :], scenes, np.nan).astype(np.float32, copy=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slice -> NaN, intended
        comp = np.nanmedian(masked, axis=0).astype(np.float32, copy=False)
    coverage = valid.sum(axis=0).astype(np.uint16)
    return comp, coverage


def _warp(
    href: str,
    grid: Grid,
    resampling: Resampling,
    dtype: str,
    dst_nodata: float,
) -> NDArray[Any]:
    dst = np.full(grid.shape, dst_nodata, dtype=dtype)
    with rasterio.open(href) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            resampling=resampling,
            src_nodata=src.nodata if src.nodata is not None else 0,
            dst_nodata=dst_nodata,
        )
    return dst


def read_scene(
    band_hrefs: Mapping[str, str],
    scl_href: str,
    grid: Grid,
    band_names: Sequence[str] | None = None,
    scale: float | None = None,
    dilate_px: int = 0,
) -> tuple[NDArray[np.float32], NDArray[np.bool_]]:
    """Read one scene onto ``grid``.

    Reflectance bands are warped bilinearly, SCL with nearest. DN 0 (or the
    source nodata) becomes NaN and values are multiplied by ``scale`` (default
    from bands.yaml). Returns (bands (band, y, x) float32; valid (y, x) bool
    from SCL, False where SCL is nodata or outside the scene footprint).
    Bands are ordered by ``band_names`` (default: bands.yaml order); every name
    must be present in ``band_hrefs``.
    """
    yaml_names, yaml_scale = load_s2_bands()
    names = list(band_names) if band_names is not None else yaml_names
    scale = yaml_scale if scale is None else scale
    missing = [n for n in names if n not in band_hrefs]
    if missing:
        raise KeyError(f"missing band hrefs: {missing}")
    stack = np.empty((len(names), *grid.shape), dtype=np.float32)
    for i, name in enumerate(names):
        dn = _warp(band_hrefs[name], grid, Resampling.bilinear, "float32", float("nan"))
        stack[i] = dn * np.float32(scale)
    scl = _warp(scl_href, grid, Resampling.nearest, "uint8", 0)
    return stack, valid_mask(scl, dilate_px=dilate_px)


def build_composite(
    scenes: Sequence[tuple[Mapping[str, str], str]],
    grid: Grid,
    band_names: Sequence[str] | None = None,
    strip_rows: int | None = None,
    max_strip_bytes: int = 3 * 1024**3,
) -> tuple[NDArray[np.float32], NDArray[np.uint16]]:
    """Read every (band_hrefs, scl_href) scene onto ``grid`` and composite.

    Rows are processed in strips (``strip_rows``, or sized from ``max_strip_bytes``);
    the result is identical to compositing the whole grid at once.
    """
    names = list(band_names) if band_names is not None else load_s2_bands()[0]
    if not scenes:
        empty = np.empty((0, len(names), *grid.shape), dtype=np.float32)
        return median_composite(empty, np.empty((0, *grid.shape), dtype=bool))
    # The median needs every scene at once, so work in row strips to bound memory
    # (stack + masked copy + nanmedian temporaries are about 3x the stack size).
    per_row = len(scenes) * len(names) * grid.width * 4 * 3
    rows = grid.height if strip_rows is None else strip_rows
    if strip_rows is None:
        rows = max(16, min(grid.height, int(max_strip_bytes // max(per_row, 1))))
    comp = np.empty((len(names), *grid.shape), dtype=np.float32)
    coverage = np.empty(grid.shape, dtype=np.uint16)
    for y0 in range(0, grid.height, rows):
        h = min(rows, grid.height - y0)
        sub = Grid(
            transform=grid.transform * Affine.translation(0, y0),
            width=grid.width,
            height=h,
            crs=grid.crs,
        )
        data: list[NDArray[np.float32]] = []
        masks: list[NDArray[np.bool_]] = []
        for hrefs, scl in scenes:
            b, m = read_scene(hrefs, scl, sub, band_names=names)
            data.append(b)
            masks.append(m)
        comp[:, y0 : y0 + h], coverage[y0 : y0 + h] = median_composite(
            np.stack(data), np.stack(masks)
        )
    return comp, coverage
