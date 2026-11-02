"""Target grid: 10 m EPSG:32630 pixel grid for a site (contracts/cog-layout.md).

The origin is snapped outward to a multiple of 10 m so every product for a site
(and any site) shares one pixel lattice.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rasterio.crs import CRS
from rasterio.transform import Affine
from rasterio.warp import transform as warp_transform

TARGET_CRS = CRS.from_epsg(32630)
RESOLUTION_M = 10.0


@dataclass(frozen=True)
class Grid:
    """Pixel grid: affine transform (north-up), size and CRS."""

    transform: Affine
    width: int
    height: int
    crs: CRS = TARGET_CRS

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) in grid CRS."""
        t = self.transform
        return (t.c, t.f + t.e * self.height, t.c + t.a * self.width, t.f)


def _iter_coords(obj: Any) -> list[tuple[float, float]]:
    """Collect all (x, y) positions from nested GeoJSON coordinates."""
    if isinstance(obj, (list, tuple)):
        if len(obj) >= 2 and all(isinstance(v, (int, float)) for v in obj[:2]):
            return [(float(obj[0]), float(obj[1]))]
        out: list[tuple[float, float]] = []
        for item in obj:
            out.extend(_iter_coords(item))
        return out
    return []


def _geometry_coords(gj: dict[str, Any]) -> list[tuple[float, float]]:
    kind = gj.get("type")
    out: list[tuple[float, float]] = []
    if kind == "FeatureCollection":
        for f in gj.get("features", []):
            out.extend(_geometry_coords(f))
    elif kind == "Feature":
        geom = gj.get("geometry")
        if geom:
            out.extend(_geometry_coords(geom))
    elif kind == "GeometryCollection":
        for g in gj.get("geometries", []):
            out.extend(_geometry_coords(g))
    else:
        out.extend(_iter_coords(gj.get("coordinates", [])))
    return out


def build_grid(
    boundary: dict[str, Any] | str | Path,
    buffer_m: float = 0.0,
    src_crs: str | CRS = "EPSG:4326",
) -> Grid:
    """Build the 10 m EPSG:32630 grid covering ``boundary`` plus ``buffer_m``.

    ``boundary`` is a GeoJSON dict (Feature, FeatureCollection or geometry) or a
    path to one, in ``src_crs`` (default EPSG:4326). The bounding box of the
    reprojected vertices is expanded by ``buffer_m`` and snapped outward to
    multiples of 10 m.
    """
    if buffer_m < 0:
        raise ValueError("buffer_m must be >= 0")
    if isinstance(boundary, (str, Path)):
        boundary = json.loads(Path(boundary).read_text(encoding="utf-8"))
    if not isinstance(boundary, dict):
        raise ValueError("boundary must be a GeoJSON object")
    coords = _geometry_coords(boundary)
    if not coords:
        raise ValueError("boundary contains no coordinates")
    src = CRS.from_user_input(src_crs)
    xs, ys = warp_transform(src, TARGET_CRS, [c[0] for c in coords], [c[1] for c in coords])
    res = RESOLUTION_M
    west = math.floor((min(xs) - buffer_m) / res) * res
    east = math.ceil((max(xs) + buffer_m) / res) * res
    south = math.floor((min(ys) - buffer_m) / res) * res
    north = math.ceil((max(ys) + buffer_m) / res) * res
    width = max(1, round((east - west) / res))
    height = max(1, round((north - south) / res))
    return Grid(Affine(res, 0.0, west, 0.0, -res, north), width, height)
