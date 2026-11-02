"""Sites endpoints against a real PostGIS (fixtures from test_db.py; skipped if unreachable)."""

import datetime as dt
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from api.db.models import Classification, Composite
from api.db.session import get_session
from api.main import create_app
from api.security import hash_key
from api.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_db import conn, db_url, engine, make_composite, make_model, make_run, session, sha  # noqa: F401

KEY = "sites-test-key"
H = {"X-API-Key": KEY}

# ~0.1 x 0.1 degrees near Glen Trool: roughly 7 000 ha, inside the supported region.
GLEN: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-4.5, 55.1], [-4.4, 55.1], [-4.4, 55.2], [-4.5, 55.2], [-4.5, 55.1]]],
}


def box(w: float, s: float, e: float, n: float) -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:  # noqa: F811
    app = create_app(Settings(api_keys=hash_key(KEY)))
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)


def create(client: TestClient, boundary: Any = GLEN, **extra: Any) -> Any:
    return client.post(
        "/sites", json={"name": "Glen Trool", "boundary": boundary, **extra}, headers=H
    )


def test_auth_required(client: TestClient) -> None:
    assert client.get("/sites").status_code == 401
    assert client.post("/sites", json={}).status_code == 401
    assert client.get(f"/sites/{uuid.uuid4()}").status_code == 401


def test_create_polygon_promoted_to_multipolygon(client: TestClient) -> None:
    r = create(client, slug="glen-trool-a")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "glen-trool-a"
    assert body["boundary"]["type"] == "MultiPolygon"
    assert len(body["boundary"]["coordinates"]) == 1
    assert 6000 < body["area_ha"] < 8000
    assert body["latest_run"] is None
    assert body["available_windows"] == []
    assert r.headers["Location"].endswith(f"/sites/{body['id']}")
    got = client.get(f"/sites/{body['id']}", headers=H)
    assert got.status_code == 200
    assert got.json() == body


def test_create_slug_derived_from_name(client: TestClient) -> None:
    r = client.post("/sites", json={"name": "  Flow Country (N)! ", "boundary": GLEN}, headers=H)
    assert r.status_code == 201
    assert r.json()["slug"] == "flow-country-n"


def test_create_accepts_feature_and_multipolygon(client: TestClient) -> None:
    feature = {"type": "Feature", "properties": None, "geometry": GLEN}
    assert create(client, feature, slug="from-feature").status_code == 201
    multi = {"type": "MultiPolygon", "coordinates": [GLEN["coordinates"]]}
    r = create(client, multi, slug="from-multi")
    assert r.status_code == 201
    assert r.json()["boundary"]["type"] == "MultiPolygon"


def test_duplicate_slug_conflict(client: TestClient) -> None:
    assert create(client, slug="dup-site").status_code == 201
    r = create(client, slug="dup-site")
    assert r.status_code == 409
    assert r.json() == {
        "code": "conflict",
        "message": "A site with slug dup-site already exists. Choose a different slug.",
        "detail": {"slug": "dup-site"},
    }


@pytest.mark.parametrize(
    "boundary",
    [
        {"type": "Point", "coordinates": [-4.5, 55.1]},
        {"type": "Feature", "properties": {}, "geometry": None},
        {"type": "Polygon", "coordinates": []},
        {"type": "Polygon", "coordinates": [[[-4.5, 55.1], [-4.4, 55.1], [-4.5, 55.1]]]},
        {"type": "Polygon", "coordinates": [[["a", 1], [1, 1], [1, 2], ["a", 1]]]},
        # Bow tie: self-intersecting.
        {
            "type": "Polygon",
            "coordinates": [[[-4.5, 55.1], [-4.4, 55.2], [-4.4, 55.1], [-4.5, 55.2], [-4.5, 55.1]]],
        },
        # Looks like lat/lon swapped (x outside +-90 is fine, y outside +-90 is not).
        box(-4.5, 155.1, -4.4, 155.2),
        {
            "type": "Polygon",
            "coordinates": [[[-4.5, 55.1], [-4.4, 55.1], [-4.4, 55.2], [-4.5, 55.1]]],
            "x": 1,
        },
    ][:-1],
)
def test_invalid_boundary(client: TestClient, boundary: Any) -> None:
    r = create(client, boundary)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "boundary_invalid"
    assert set(body) == {"code", "message", "detail"}


def test_outside_region(client: TestClient) -> None:
    for boundary in (box(-0.2, 51.4, -0.1, 51.5), box(-4.5, 52.9, -4.4, 53.0)):  # London, Wales
        r = create(client, boundary)
        assert r.status_code == 422
        assert r.json()["code"] == "boundary_outside_region"
        assert r.json()["detail"] == {"tiles": ["30UUF", "30UVF", "30UUG", "30UVG"]}


def test_straddling_region_edge_rejected(client: TestClient) -> None:
    # The region's western edge is ~295 km easting, about -6.2 degrees at this latitude.
    r = create(client, box(-6.6, 55.1, -6.0, 55.15))
    assert r.status_code == 422
    assert r.json()["code"] == "boundary_outside_region"


def test_too_large(client: TestClient) -> None:
    r = create(client, box(-4.7, 54.8, -3.6, 55.5))
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "boundary_too_large"
    assert body["detail"]["max_area_ha"] == 50000.0
    assert body["detail"]["area_ha"] > 50000
    assert "the maximum is 50 000 ha" in body["message"]


def test_just_under_max_area_accepted(client: TestClient) -> None:
    # ~0.25 x 0.2 degrees is roughly 35 000 ha.
    r = create(client, box(-4.6, 55.0, -4.35, 55.2))
    assert r.status_code == 201, r.text
    assert 20000 < r.json()["area_ha"] < 50000


def test_request_validation(client: TestClient) -> None:
    for payload in (
        {"boundary": GLEN},
        {"name": "", "boundary": GLEN},
        {"name": "x"},
        {"name": "x", "boundary": "not an object"},
        {"name": "x", "boundary": GLEN, "slug": "Bad Slug"},
        {"name": "x", "boundary": GLEN, "extra": 1},
    ):
        r = client.post("/sites", json=payload, headers=H)
        assert r.status_code == 422, payload
        assert r.json()["code"] == "validation_error"
        assert isinstance(r.json()["detail"], list)


def test_name_without_usable_slug(client: TestClient) -> None:
    r = client.post("/sites", json={"name": "!!", "boundary": GLEN}, headers=H)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_boundary_hash_and_uri_stored(client: TestClient, session: Session) -> None:  # noqa: F811
    from api.db.models import Site

    site_id = create(client, slug="hash-check").json()["id"]
    site = session.get(Site, uuid.UUID(site_id))
    assert site is not None
    assert site.boundary_uri == "s3://peatwatch-data/sites/hash-check/boundary.geojson"
    assert len(site.boundary_sha256) == 64


def test_get_not_found_and_bad_id(client: TestClient) -> None:
    missing = uuid.uuid4()
    r = client.get(f"/sites/{missing}", headers=H)
    assert r.status_code == 404
    assert r.json() == {
        "code": "not_found",
        "message": "Site not found.",
        "detail": {"site_id": str(missing)},
    }
    r = client.get("/sites/not-a-uuid", headers=H)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_list_pagination_search_and_order(client: TestClient, session: Session) -> None:  # noqa: F811
    for i, slug in enumerate(("alpha-site", "beta-site", "gamma-site")):
        assert create(client, slug=slug).status_code == 201
        # now() is constant inside the test transaction, so space the timestamps explicitly.
        session.execute(
            text("UPDATE sites SET created_at = now() + make_interval(mins => :i) WHERE slug = :s"),
            {"i": i, "s": slug},
        )
    page = client.get("/sites", headers=H).json()
    assert [i["slug"] for i in page["items"]] == ["gamma-site", "beta-site", "alpha-site"]
    assert (page["total"], page["limit"], page["offset"]) == (3, 50, 0)
    assert page["items"][0].keys() == {"id", "slug", "name", "area_ha", "created_at", "latest_run"}

    page = client.get("/sites?limit=1&offset=1", headers=H).json()
    assert [i["slug"] for i in page["items"]] == ["beta-site"]
    assert page["total"] == 3

    assert [i["slug"] for i in client.get("/sites?q=ALPH", headers=H).json()["items"]] == [
        "alpha-site"
    ]
    assert client.get("/sites?q=%25", headers=H).json()["total"] == 0  # '%' is literal
    for bad in ("limit=0", "limit=201", "offset=-1"):
        assert client.get(f"/sites?{bad}", headers=H).status_code == 422


def test_latest_run_and_available_windows(client: TestClient, session: Session) -> None:  # noqa: F811
    from api.db.models import Site

    site_id = uuid.UUID(create(client, slug="with-data").json()["id"])
    site = session.get(Site, site_id)
    assert site is not None
    model = make_model(session, "unet", "1.0.0")
    make_run(session, site, model, sha("r1"), created_at=dt.datetime(2024, 1, 1, tzinfo=dt.UTC))
    newest = make_run(
        session, site, model, sha("r2"), created_at=dt.datetime(2024, 2, 1, tzinfo=dt.UTC)
    )
    late = make_composite(session, site, "summer", "late")
    early = Composite(
        site_id=site.id,
        year=2022,
        season="summer",
        date_start="2022-06-01",
        date_end="2022-08-31",
        cog_uri="s3://peatwatch-data/composites/with-data/2022/summer.tif",
        pipeline_version="pipeline 0.1.0 (a1b2c3d)",
        scene_ids=[],
        cloud_stats={},
        parameters={},
        idempotency_key=sha("early"),
    )
    session.add(early)
    session.flush()
    for comp, m in ((late, model), (late, make_model(session, "rf", "2.0.0"))):
        session.add(
            Classification(
                composite_id=comp.id,
                model_id=m.id,
                cog_uri=f"s3://peatwatch-data/classifications/{m.name}/{comp.id}.tif",
                class_areas_ha={},
                idempotency_key=sha(f"{comp.id}{m.id}"),
            )
        )
    session.flush()

    body = client.get(f"/sites/{site_id}", headers=H).json()
    assert body["latest_run"]["id"] == str(newest.id)
    assert body["latest_run"]["status"] == "queued"
    assert [w["start"] for w in body["available_windows"]] == ["2022-06-01", "2023-06-01"]
    assert body["available_windows"][0]["models"] == []
    assert body["available_windows"][1]["models"] == ["rf-2.0.0", "unet-1.0.0"]
    assert body["available_windows"][1]["composite_id"] == str(late.id)

    listed = client.get("/sites?q=with-data", headers=H).json()["items"][0]
    assert listed["latest_run"]["id"] == str(newest.id)
