"""Rasterise vector labels onto a composite grid as uint8 class ids.

Class ids and the nodata value come from ``contracts/classes.yaml`` (nodata 255 means
"unlabelled"). The label class can be given as a class *name* (``bog``) or as an integer id.

Overlap rule: features are burned in row order of the GeoDataFrame, and a later feature
overwrites an earlier one where they overlap. Sort the frame first to change precedence.
By default a pixel is burned when its centre lies inside the polygon (``all_touched=False``).
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import rasterio
import yaml
from rasterio.crs import CRS
from rasterio.features import rasterize
from rasterio.transform import Affine

REPO_ROOT = Path(__file__).resolve().parents[3]
CLASSES_PATH = REPO_ROOT / "contracts" / "classes.yaml"
HANDLABEL_SCHEMA_PATH = REPO_ROOT / "docs" / "labels" / "handlabel.schema.json"

DEFAULT_CLASS_COLUMN = "class"


def load_classes(path: Path | str = CLASSES_PATH) -> tuple[dict[str, int], int]:
    """Return ``(name -> id, nodata)`` from a classes.yaml contract file."""
    data = yaml.safe_load(Path(path).read_text("utf-8"))
    mapping = {str(c["name"]): int(c["id"]) for c in data["classes"]}
    return mapping, int(data["nodata"])


def _class_ids(values: Any, mapping: Mapping[str, int], column: str) -> list[int]:
    valid_ids = set(mapping.values())
    out: list[int] = []
    for v in values:
        if isinstance(v, str):
            if v not in mapping:
                raise ValueError(f"unknown class name {v!r} in column {column!r}")
            out.append(mapping[v])
        elif v is None or (isinstance(v, float) and np.isnan(v)):
            raise ValueError(f"missing class value in column {column!r}")
        else:
            if int(v) not in valid_ids:
                raise ValueError(f"unknown class id {v!r} in column {column!r}")
            out.append(int(v))
    return out


def rasterize_labels(
    gdf: gpd.GeoDataFrame,
    *,
    transform: Affine,
    shape: tuple[int, int],
    crs: Any,
    class_column: str = DEFAULT_CLASS_COLUMN,
    classes: Mapping[str, int] | None = None,
    nodata: int | None = None,
    all_touched: bool = False,
    exclude_confidence: Collection[str] | None = None,
    assume_crs: Any = None,
) -> npt.NDArray[np.uint8]:
    """Burn polygon labels onto a grid.

    Args:
        gdf: Polygon/MultiPolygon features in any CRS. Needs ``class_column`` holding class
            names (per ``classes.yaml``) or integer ids.
        transform, shape, crs: Target grid (``shape`` is ``(height, width)``).
        classes: name -> id map; defaults to ``contracts/classes.yaml``.
        nodata: Fill for unlabelled pixels; defaults to the contract value (255).
        all_touched: Burn every pixel touched by a polygon instead of pixel centres only.
        exclude_confidence: Drop rows whose ``confidence`` column is in this collection
            (e.g. ``{"low"}`` for hand-label scoring).
        assume_crs: CRS to assume when ``gdf.crs`` is None; otherwise that is an error.

    Overlaps: later rows overwrite earlier rows (see module docstring).
    Null and empty geometries are skipped; invalid ones are repaired.

    Returns:
        ``(height, width)`` uint8 array of class ids, ``nodata`` where unlabelled.
    """
    default_map, default_nodata = load_classes()
    mapping = dict(classes) if classes is not None else default_map
    fill = default_nodata if nodata is None else nodata
    if not 0 <= fill <= 255 or fill in mapping.values():
        raise ValueError(f"nodata {fill} must be a uint8 value distinct from class ids")
    if class_column not in gdf.columns:
        raise KeyError(f"class column {class_column!r} not in GeoDataFrame")

    if gdf.crs is None:
        if assume_crs is None:
            raise ValueError("GeoDataFrame has no CRS; set it or pass assume_crs")
        gdf = gdf.set_crs(assume_crs)
    if exclude_confidence:
        if "confidence" not in gdf.columns:
            raise KeyError("exclude_confidence given but no 'confidence' column")
        gdf = gdf[~gdf["confidence"].isin(list(exclude_confidence))]

    out = np.full(shape, fill, dtype=np.uint8)
    if len(gdf) == 0:
        return out
    ids = _class_ids(gdf[class_column], mapping, class_column)
    gdf = gdf.to_crs(CRS.from_user_input(crs))
    shapes = []
    for geom, cid in zip(gdf.geometry, ids, strict=True):
        if geom is None or geom.is_empty:
            continue
        if not geom.is_valid:
            geom = geom.make_valid()
            if geom.is_empty:
                continue
        shapes.append((geom, cid))
    if not shapes:
        return out
    burned: npt.NDArray[np.uint8] = rasterize(
        shapes,
        out_shape=shape,
        transform=transform,
        fill=fill,
        all_touched=all_touched,
        dtype="uint8",
    )
    return burned


def rasterize_to_composite(
    gdf: gpd.GeoDataFrame, composite_path: Path | str, **kwargs: Any
) -> npt.NDArray[np.uint8]:
    """Rasterise onto the grid (transform, shape, CRS) of a composite COG."""
    with rasterio.open(composite_path) as src:
        return rasterize_labels(
            gdf, transform=src.transform, shape=(src.height, src.width), crs=src.crs, **kwargs
        )


def read_handlabels(path: Path | str, *, validate: bool = True) -> gpd.GeoDataFrame:
    """Read a hand-label GeoJSON, validating against ``handlabel.schema.json``."""
    if validate:
        import jsonschema

        schema = json.loads(HANDLABEL_SCHEMA_PATH.read_text("utf-8"))
        jsonschema.validate(json.loads(Path(path).read_text("utf-8")), schema)
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    return gdf


def rasterize_handlabels(
    gdf: gpd.GeoDataFrame,
    *,
    transform: Affine,
    shape: tuple[int, int],
    crs: Any,
    include_low_confidence: bool = False,
) -> npt.NDArray[np.uint8]:
    """Hand-label schema wrapper: ``class`` column, ``low`` confidence excluded by default."""
    return rasterize_labels(
        gdf,
        transform=transform,
        shape=shape,
        crs=crs,
        exclude_confidence=None if include_low_confidence else {"low"},
        assume_crs="EPSG:4326",
    )
