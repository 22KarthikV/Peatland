"""Site boundary validation: GeoJSON shape, validity, supported region, maximum area.

Error codes and the 422 status follow contracts/openapi.yaml (`boundary_invalid`,
`boundary_outside_region`, `boundary_too_large`). Region and area are evaluated in EPSG:32630 by
PostGIS so the area matches the stored `sites.area_ha` (contracts/db-schema.md section 1).
"""

import math
import re
from typing import Any

from shapely.errors import GeometryTypeError, ShapelyError
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.validation import explain_validity
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.errors import ApiError

MAX_SITE_AREA_HA = 50_000.0

# Supported region: Sentinel-2 tiles 30UUF, 30UVF, 30UUG, 30UVG. The contracts name the tiles but
# give no coordinates, so it is derived here in EPSG:32630 (UTM 30N). MGRS squares U and V in zone
# 30 span eastings 300-500 km; rows F and G (even zone, band U) span northings 6000-6200 km. Each
# S2 tile footprint is 109.8 km, i.e. 4.9 km beyond the 100 km square on every side.
REGION_TILES = ("30UUF", "30UVF", "30UUG", "30UVG")
REGION_EPSG = 32630
_MARGIN_M = 4_900.0
REGION_BOUNDS_M = (
    300_000.0 - _MARGIN_M,
    6_000_000.0 - _MARGIN_M,
    500_000.0 + _MARGIN_M,
    6_200_000.0 + _MARGIN_M,
)

_MEASURE_SQL = text(
    """
    SELECT ST_Area(g) / 10000.0 AS area_ha,
           ST_Within(g, ST_MakeEnvelope(:x0, :y0, :x1, :y1, :epsg)) AS inside
    FROM (SELECT ST_Transform(ST_GeomFromText(:wkt, 4326), :epsg) AS g) AS t
    """
)


def boundary_invalid(message: str, detail: Any = None) -> ApiError:
    return ApiError(422, "boundary_invalid", message, detail)


def _all_coords(value: Any) -> list[float]:
    if isinstance(value, bool):
        raise ValueError("boolean coordinate")
    if isinstance(value, int | float):
        return [float(value)]
    if isinstance(value, list | tuple):
        out: list[float] = []
        for item in value:
            out.extend(_all_coords(item))
        return out
    raise ValueError("coordinates must be numbers")


def parse_boundary(boundary: dict[str, Any]) -> MultiPolygon:
    """Turn a GeoJSON Polygon/MultiPolygon/Feature into a valid shapely MultiPolygon (lon/lat)."""
    geometry: Any = boundary
    if boundary.get("type") == "Feature":
        geometry = boundary.get("geometry")
        if not isinstance(geometry, dict):
            raise boundary_invalid("The Feature has no geometry.")
    gtype = geometry.get("type")
    if gtype not in ("Polygon", "MultiPolygon"):
        raise boundary_invalid(
            "The boundary must be a Polygon or MultiPolygon (or a Feature holding one).",
            {"type": gtype if isinstance(gtype, str) else None},
        )
    try:
        coords = _all_coords(geometry.get("coordinates"))
        geom = shape(geometry)
    except (ValueError, TypeError, ShapelyError, GeometryTypeError, IndexError) as exc:
        raise boundary_invalid("The boundary coordinates are malformed.") from exc
    if not coords or not all(math.isfinite(c) for c in coords):
        raise boundary_invalid("The boundary coordinates must be finite numbers.")
    if geom.is_empty:
        raise boundary_invalid("The boundary is empty.")
    if not isinstance(geom, Polygon | MultiPolygon):  # pragma: no cover - guarded above
        raise boundary_invalid("The boundary must be a Polygon or MultiPolygon.")
    # Positions are [lon, lat(, z)]; check every x and y against WGS 84 limits.
    for x, y, *_ in _positions(geometry["coordinates"]):
        if abs(x) > 180.0 or abs(y) > 90.0:
            raise boundary_invalid(
                "Coordinates must be longitude/latitude in WGS 84 (EPSG:4326), as [lon, lat]."
            )
    if not geom.is_valid:
        reason = explain_validity(geom)
        raise boundary_invalid(
            f"The boundary is not a valid polygon: {reason}.", {"reason": reason}
        )
    return MultiPolygon([geom]) if isinstance(geom, Polygon) else geom


def _positions(value: Any) -> list[list[float]]:
    """Flatten nested coordinate arrays into positions (lists of numbers)."""
    if value and isinstance(value[0], int | float):
        return [value]
    out: list[list[float]] = []
    for item in value:
        out.extend(_positions(item))
    return out


def check_region_and_area(
    session: Session, geom: MultiPolygon, max_area_ha: float = MAX_SITE_AREA_HA
) -> float:
    """Raise if outside the supported region or too large; return the area in hectares."""
    x0, y0, x1, y1 = REGION_BOUNDS_M
    row = session.execute(
        _MEASURE_SQL,
        {"wkt": geom.wkt, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "epsg": REGION_EPSG},
    ).one()
    area_ha = float(row.area_ha)
    if not row.inside:
        raise ApiError(
            422,
            "boundary_outside_region",
            "The boundary must lie entirely inside the supported region "
            f"(Sentinel-2 tiles {', '.join(REGION_TILES)}, southern Scotland and northern "
            "England).",
            {"tiles": list(REGION_TILES)},
        )
    if area_ha > max_area_ha:
        raise ApiError(
            422,
            "boundary_too_large",
            f"The boundary is {_fmt(area_ha)} ha; the maximum is {_fmt(max_area_ha)} ha.",
            {"area_ha": round(area_ha, 2), "max_area_ha": max_area_ha},
        )
    return area_ha


def _fmt(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def is_valid_slug(slug: str) -> bool:
    return _SLUG_RE.match(slug) is not None


def slugify(name: str) -> str | None:
    """Derive a C2 `<site>` slug from a name; None when nothing usable is left."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:63].strip("-")
    return slug if is_valid_slug(slug) else None
