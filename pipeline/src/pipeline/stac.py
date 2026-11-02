"""STAC query and scene filter against AWS Earth Search (Sentinel-2 L2A)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from pipeline.config import DateWindow

EARTH_SEARCH_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"

# contracts/bands.yaml band name -> Earth Search asset key.
BAND_ASSETS: dict[str, str] = {
    "B01": "coastal",
    "B02": "blue",
    "B03": "green",
    "B04": "red",
    "B05": "rededge1",
    "B06": "rededge2",
    "B07": "rededge3",
    "B08": "nir",
    "B8A": "nir08",
    "B09": "nir09",
    "B11": "swir16",
    "B12": "swir22",
}
SCL_ASSET = "scl"


class NoUsableScenesError(RuntimeError):
    """Raised when a query yields zero scenes under the cloud threshold."""


class SceneRecord(BaseModel):
    """One Sentinel-2 L2A scene with the asset links the pipeline needs."""

    model_config = ConfigDict(frozen=True)

    id: str
    datetime: datetime
    cloud_cover: float
    tile: str  # MGRS tile, e.g. "30UUG"
    assets: dict[str, str]  # band name (B01..B12, "SCL") -> href


class _Search(Protocol):
    def items(self) -> Any: ...


class StacClient(Protocol):
    """The slice of pystac_client.Client used here (lets tests inject a fake)."""

    def search(self, **kwargs: Any) -> _Search: ...


def load_geometry(boundary: Path | Mapping[str, Any]) -> dict[str, Any]:
    """Return a Polygon/MultiPolygon geometry from a GeoJSON path or dict."""
    if isinstance(boundary, Mapping):
        data: Mapping[str, Any] = boundary
    else:
        if not boundary.is_file():
            raise FileNotFoundError(f"boundary file not found: {boundary}")
        data = json.loads(boundary.read_text(encoding="utf-8"))
    kind = data.get("type")
    if kind == "FeatureCollection":
        geoms = [f["geometry"] for f in data.get("features", []) if f.get("geometry")]
    elif kind == "Feature":
        geoms = [data["geometry"]] if data.get("geometry") else []
    else:
        geoms = [dict(data)]
    polys: list[Any] = []
    for g in geoms:
        if g.get("type") == "Polygon":
            polys.append(g["coordinates"])
        elif g.get("type") == "MultiPolygon":
            polys.extend(g["coordinates"])
        else:
            raise ValueError(f"unsupported boundary geometry type: {g.get('type')!r}")
    if not polys:
        raise ValueError("boundary contains no polygon geometry")
    if len(polys) == 1:
        return {"type": "Polygon", "coordinates": polys[0]}
    return {"type": "MultiPolygon", "coordinates": polys}


def _window_interval(window: DateWindow) -> str:
    return f"{window.start.isoformat()}T00:00:00Z/{window.end.isoformat()}T23:59:59Z"


def _tile(props: Mapping[str, Any]) -> str:
    code = props.get("grid:code")
    if isinstance(code, str) and code.startswith("MGRS-"):
        return code.removeprefix("MGRS-")
    mgrs = props.get("s2:mgrs_tile")
    return str(mgrs) if mgrs else ""


def _record(item: Any) -> SceneRecord | None:
    props: Mapping[str, Any] = item.properties
    cloud = props.get("eo:cloud_cover")
    if cloud is None or item.datetime is None:
        return None
    assets: dict[str, str] = {}
    for band, key in {**BAND_ASSETS, "SCL": SCL_ASSET}.items():
        asset = item.assets.get(key)
        if asset is None:
            return None  # scene lacks a required band: unusable
        assets[band] = asset.href
    return SceneRecord(
        id=item.id,
        datetime=item.datetime.astimezone(UTC),
        cloud_cover=float(cloud),
        tile=_tile(props),
        assets=assets,
    )


def search_scenes(
    boundary: Path | Mapping[str, Any],
    window: DateWindow,
    cloud_threshold: float = 40.0,
    *,
    client: StacClient | None = None,
) -> list[SceneRecord]:
    """Find scenes intersecting ``boundary`` in ``window`` with cloud <= threshold.

    Returns records sorted by datetime (then id). Raises NoUsableScenesError if none.
    Pass ``client`` to avoid the network (tests); otherwise Earth Search is used.
    """
    geometry = load_geometry(boundary)
    api: Any = client
    if api is None:
        from pystac_client import Client

        api = Client.open(EARTH_SEARCH_URL)
    search = api.search(
        collections=[COLLECTION],
        intersects=geometry,
        datetime=_window_interval(window),
        query={"eo:cloud_cover": {"lte": cloud_threshold}},
    )
    scenes = [
        rec
        for item in search.items()
        if (rec := _record(item)) is not None and rec.cloud_cover <= cloud_threshold
    ]
    if not scenes:
        raise NoUsableScenesError(
            f"no Sentinel-2 L2A scenes with cloud cover <= {cloud_threshold}% between "
            f"{window.start} and {window.end}; widen the date window or raise cloud_threshold"
        )
    return sorted(scenes, key=lambda s: (s.datetime, s.id))
