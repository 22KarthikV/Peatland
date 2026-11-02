"""STAC search tests: replay a recorded Earth Search response, no network."""

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pipeline.config import DateWindow
from pipeline.stac import BAND_ASSETS, NoUsableScenesError, load_geometry, search_scenes
from pystac import ItemCollection

FIXTURE = Path(__file__).parent / "fixtures" / "earth_search_galloway_2025-04-08.json"
W, S, E, N = -4.55, 55.05, -4.30, 55.30
GEOM = {"type": "Polygon", "coordinates": [[[W, S], [E, S], [E, N], [W, N], [W, S]]]}
WINDOW = DateWindow(start=date(2025, 4, 8), end=date(2025, 4, 8))


def _features() -> list[dict[str, Any]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return copy.deepcopy(data["features"])


class _Result:
    def __init__(self, coll: ItemCollection) -> None:
        self._coll = coll

    def items(self) -> Any:
        return iter(self._coll.items)


class FakeClient:
    """Replays the recorded response regardless of the query."""

    def __init__(self, features: list[dict[str, Any]] | None = None) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        if features is not None:
            data["features"] = features
        self.coll = ItemCollection.from_dict(data)
        self.kwargs: dict[str, Any] = {}

    def search(self, **kwargs: Any) -> _Result:
        self.kwargs = kwargs
        return _Result(self.coll)


def test_search_returns_typed_sorted_records() -> None:
    scenes = search_scenes(GEOM, WINDOW, 40, client=FakeClient())
    assert {s.id for s in scenes} == {"S2A_30UUG_20250408_0_L2A", "S2A_30UVG_20250408_1_L2A"}
    assert [s.datetime for s in scenes] == sorted(s.datetime for s in scenes)
    first = next(s for s in scenes if s.tile == "30UUG")
    assert first.tile == "30UUG"
    assert first.cloud_cover < 1
    assert first.datetime.year == 2025 and first.datetime.tzinfo is not None
    assert set(first.assets) == {*BAND_ASSETS, "SCL"}
    assert len(first.assets) == 13
    assert first.assets["B04"].endswith("/B04.tif")
    assert first.assets["SCL"].endswith("/SCL.tif")


def test_query_parameters_passed() -> None:
    client = FakeClient()
    search_scenes(GEOM, WINDOW, 25, client=client)
    assert client.kwargs["collections"] == ["sentinel-2-l2a"]
    assert client.kwargs["intersects"] == GEOM
    assert client.kwargs["datetime"] == "2025-04-08T00:00:00Z/2025-04-08T23:59:59Z"
    assert client.kwargs["query"] == {"eo:cloud_cover": {"lte": 25}}


def test_sorted_by_date_when_server_order_differs() -> None:
    feats = _features()
    later = copy.deepcopy(feats[0])
    later["id"] = "LATER"
    later["properties"]["datetime"] = "2025-04-20T11:00:00Z"
    scenes = search_scenes(GEOM, WINDOW, 40, client=FakeClient([later, *feats]))
    assert scenes[-1].id == "LATER"


def test_cloud_threshold_filters_client_side() -> None:
    feats = _features()
    feats[1]["properties"]["eo:cloud_cover"] = 80.0
    scenes = search_scenes(GEOM, WINDOW, 40, client=FakeClient(feats))
    assert [s.id for s in scenes] == ["S2A_30UUG_20250408_0_L2A"]


def test_zero_scenes_raises_clear_error() -> None:
    with pytest.raises(NoUsableScenesError, match=r"cloud cover <= 5\.0%.*2025-04-08"):
        search_scenes(GEOM, WINDOW, 5.0, client=FakeClient([]))


def test_all_scenes_too_cloudy_raises() -> None:
    feats = _features()
    for f in feats:
        f["properties"]["eo:cloud_cover"] = 99.0
    with pytest.raises(NoUsableScenesError):
        search_scenes(GEOM, WINDOW, 40, client=FakeClient(feats))


def test_scene_missing_band_is_unusable() -> None:
    feats = _features()
    del feats[0]["assets"]["swir16"]
    scenes = search_scenes(GEOM, WINDOW, 40, client=FakeClient(feats))
    assert [s.id for s in scenes] == ["S2A_30UVG_20250408_1_L2A"]


def test_geojson_path_feature_collection(tmp_path: Path) -> None:
    p = tmp_path / "b.geojson"
    feature = {"type": "Feature", "properties": {}, "geometry": GEOM}
    p.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))
    client = FakeClient()
    search_scenes(p, WINDOW, 40, client=client)
    assert client.kwargs["intersects"] == GEOM


def test_load_geometry_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_geometry(tmp_path / "missing.geojson")
    with pytest.raises(ValueError, match="unsupported"):
        load_geometry({"type": "Point", "coordinates": [0, 0]})
    with pytest.raises(ValueError, match="no polygon"):
        load_geometry({"type": "FeatureCollection", "features": []})
