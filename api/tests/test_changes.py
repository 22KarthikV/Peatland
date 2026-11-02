"""Classification list, change creation, stats (JSON/CSV) and presigned links; fakes only."""

import datetime as dt
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from api.auth import hash_key
from api.db.models import Change, Classification, Composite, Model, Run, Site
from api.db.session import get_session
from api.main import create_app
from api.queue import InMemoryJobQueue, get_queue
from api.routes.changes import _parse_accept
from api.routes.classifications import CLASSES
from api.settings import Settings
from api.storage import InMemoryObjectStore, S3ObjectStore, get_storage
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session
from test_db import conn, db_url, engine, make_model, make_site, sha  # noqa: F401
from test_db import session as session  # noqa: F401

KEY = "changes-test-key"
H = {"X-API-Key": KEY}
CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
VALIDATOR = Draft202012Validator(
    json.loads((CONTRACTS / "queue.schema.json").read_text()),
    format_checker=Draft202012Validator.FORMAT_CHECKER,
)
W1 = "w20250319-20250319"
W2 = "w20250408-20250408"
BODY: dict[str, Any] = {
    "from": {"year": 2025, "season": W1},
    "to": {"year": 2025, "season": W2},
    "model": "unet-1.0.0",
}
STATS: dict[str, Any] = {
    "pixel_area_ha": 0.01,
    "total_ha": 100.0,
    "transitions": [
        {"from_class_id": 1, "to_class_id": 3, "area_ha": 10.5, "percent": 10.5},
        {"from_class_id": 1, "to_class_id": 1, "area_ha": 69.5, "percent": 69.5},
        {"from_class_id": 0, "to_class_id": 0, "area_ha": 20.0, "percent": 20.0},
    ],
    "class_areas_ha": {"from": {"1": 80.0, "0": 20.0}, "to": {"1": 69.5, "0": 20.0, "3": 10.5}},
}


def test_classes_match_contract() -> None:
    spec = yaml.safe_load((CONTRACTS / "classes.yaml").read_text())
    assert [c.model_dump() for c in CLASSES.values()] == spec["classes"]


@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        (None, "application/json"),
        ("", "application/json"),
        ("*/*", "application/json"),
        ("application/json", "application/json"),
        ("text/csv", "text/csv"),
        ("text/*", "text/csv"),
        ("text/csv;q=0.9, application/json;q=0.5", "text/csv"),
        ("text/csv, application/json", "application/json"),
        ("application/xml", None),
        ("text/csv;q=0", None),
    ],
)
def test_parse_accept(accept: str | None, expected: str | None) -> None:
    assert _parse_accept(accept) == expected


def test_s3_presign_is_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    store = S3ObjectStore(Settings(aws_endpoint_url="http://localhost:4566"))
    url = store.presign_get("peatwatch-data", "a/b.tif", 900)
    assert url.startswith("http://localhost:4566/peatwatch-data/a/b.tif?")
    assert "X-Amz-Expires=900" in url and "X-Amz-Signature=" in url


@pytest.fixture
def queue() -> InMemoryJobQueue:
    return InMemoryJobQueue()


@pytest.fixture
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
def client(
    session: Session,  # noqa: F811
    queue: InMemoryJobQueue,
    store: InMemoryObjectStore,
) -> Iterator[TestClient]:
    app = create_app(Settings(api_keys=hash_key(KEY)))
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_queue] = lambda: queue
    app.dependency_overrides[get_storage] = lambda: store
    yield TestClient(app)


@pytest.fixture
def site(session: Session) -> Site:  # noqa: F811
    return make_site(session, "glen-trool")


@pytest.fixture
def model(session: Session) -> Model:  # noqa: F811
    m = make_model(session, "unet", "1.0.0")
    m.is_default = True
    session.flush()
    return m


def add_window(
    session: Session, site: Site, season: str, model: Model | None
) -> tuple[Composite, Classification | None]:
    d = dt.datetime.strptime(season[1:9], "%Y%m%d").date()
    comp = Composite(
        site_id=site.id,
        year=d.year,
        season=season,
        date_start=d,
        date_end=d,
        cog_uri=f"s3://peatwatch-data/composites/{site.slug}/{d.year}/{season}.tif",
        pipeline_version="pipeline 0.1.0 (a1b2c3d)",
        scene_ids=["S2A_1"],
        cloud_stats={},
        parameters={},
        idempotency_key=sha(f"c{site.slug}{season}"),
    )
    session.add(comp)
    session.flush()
    if model is None:
        return comp, None
    cls = Classification(
        composite_id=comp.id,
        model_id=model.id,
        cog_uri=(
            f"s3://peatwatch-data/classifications/{site.slug}/{d.year}/{season}/"
            f"{model.name}-{model.version}.tif"
        ),
        class_areas_ha={"0": 20.0, "1": 80.0},
        idempotency_key=sha(f"k{site.slug}{season}{model.name}{model.version}"),
    )
    session.add(cls)
    session.flush()
    return comp, cls


def make_change(
    session: Session, site: Site, model: Model, a: tuple[Any, Any], b: tuple[Any, Any]
) -> Change:
    ch = Change(
        site_id=site.id,
        from_composite_id=a[0].id,
        to_composite_id=b[0].id,
        from_classification_id=a[1].id,
        to_classification_id=b[1].id,
        model_id=model.id,
        cog_uri=(
            f"s3://peatwatch-data/changes/{site.slug}/2025-{W1}__2025-{W2}/"
            f"{model.name}-{model.version}.tif"
        ),
        stats=STATS,
        idempotency_key=sha("change"),
    )
    session.add(ch)
    session.flush()
    session.refresh(ch)
    return ch


def post(client: TestClient, site: Site, body: Any = BODY) -> Any:
    return client.post(f"/sites/{site.id}/changes", json=body, headers=H)


# ---- classifications -------------------------------------------------------------------------


def test_list_classifications(
    client: TestClient,
    session: Session,
    site: Site,
    model: Model,  # noqa: F811
) -> None:
    add_window(session, site, W2, model)
    add_window(session, site, W1, model)
    add_window(session, site, "w20240101-20240101", None)  # composite only, no classification
    other = make_model(session, "rf", "2.0.0")
    add_window(session, site, "w20250501-20250501", other)
    r = client.get(f"/sites/{site.id}/classifications", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3 and body["limit"] == 50 and body["offset"] == 0
    assert [i["window"]["season"] for i in body["items"]] == [W1, W2, "w20250501-20250501"]
    first = body["items"][0]
    assert first["window"] == {
        "year": 2025,
        "season": W1,
        "start": "2025-03-19",
        "end": "2025-03-19",
    }
    assert first["model"]["name"] == "unet"
    assert first["class_areas"][1] == {
        "class": {"id": 1, "name": "bog", "label": "Bog (peatland)", "hex": "#0072B2"},
        "area_ha": 80.0,
        "percent": 80.0,
    }
    assert first["cog_uri"].startswith("s3://peatwatch-data/classifications/glen-trool/2025/")
    assert "X-Amz-Expires=900" in first["download_url"]
    assert first["tile_url"].startswith("http://localhost:8001/cog/tiles/WebMercatorQuad/{z}/")
    assert "url=s3%3A%2F%2Fpeatwatch-data%2Fclassifications" in first["tile_url"]
    assert set(first) == {
        "id", "cog_uri", "tile_url", "download_url", "model", "created_at",
        "composite_id", "window", "class_areas",
    }  # fmt: skip


def test_list_classifications_filters_and_paging(
    client: TestClient,
    session: Session,
    site: Site,
    model: Model,  # noqa: F811
) -> None:
    add_window(session, site, W1, model)
    add_window(session, site, W2, model)
    other = make_model(session, "rf", "2.0.0")
    add_window(session, site, "w20240501-20240501", other)
    url = f"/sites/{site.id}/classifications"
    assert client.get(url, params={"model": "rf"}, headers=H).json()["total"] == 1
    assert client.get(url, params={"model": "rf-2.0.0"}, headers=H).json()["total"] == 1
    assert client.get(url, params={"model": "unet-1.0.0"}, headers=H).json()["total"] == 2
    assert client.get(url, params={"year": 2024}, headers=H).json()["total"] == 1
    page = client.get(url, params={"limit": 1, "offset": 1}, headers=H).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert client.get(url, params={"limit": 0}, headers=H).status_code == 422
    assert client.get(url, params={"year": 1999}, headers=H).status_code == 422


def test_list_classifications_errors(client: TestClient, site: Site) -> None:
    assert client.get(f"/sites/{uuid.uuid4()}/classifications", headers=H).status_code == 404
    assert client.get(f"/sites/{site.id}/classifications").status_code == 401


def test_list_classifications_storage_unavailable(
    client: TestClient,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
    store: InMemoryObjectStore,
) -> None:
    add_window(session, site, W1, model)
    store.fail = True
    r = client.get(f"/sites/{site.id}/classifications", headers=H)
    assert r.status_code == 503 and r.json()["code"] == "storage_unavailable"


# ---- POST /sites/{id}/changes -----------------------------------------------------------------


def test_create_change_enqueues(
    client: TestClient,
    queue: InMemoryJobQueue,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    add_window(session, site, W1, model)
    add_window(session, site, W2, model)
    r = post(client, site)
    assert r.status_code == 202, r.text
    body = r.json()
    assert r.headers["Location"].endswith(f"/runs/{body['id']}")
    assert body["kind"] == "change" and body["status"] == "queued"
    assert body["params"] == {
        "from": {"year": 2025, "season": W1},
        "to": {"year": 2025, "season": W2},
        "model": "unet-1.0.0",
    }
    assert body["outputs"] == {"composite_ids": [], "classification_ids": [], "change_id": None}
    run = session.get(Run, uuid.UUID(body["id"]))
    assert run is not None and run.kind == "change"
    assert run.log_uri == f"s3://peatwatch-artifacts/runs/{run.id}/log.jsonl"

    assert len(queue.messages) == 1
    msg = queue.messages[0]
    VALIDATOR.validate(msg)
    assert msg["kind"] == "change" and msg["run_id"] == body["id"]
    assert msg["idempotency_key"] == run.idempotency_key
    assert [w["season"] for w in msg["windows"]] == [W1, W2]
    assert msg["windows"][0] == {
        "year": 2025,
        "season": W1,
        "start": "2025-03-19",
        "end": "2025-03-19",
    }
    assert msg["params"] == body["params"]
    assert msg["model"] == {"name": "unet", "version": "1.0.0"}


def test_create_change_default_model_and_idempotent(
    client: TestClient,
    queue: InMemoryJobQueue,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    add_window(session, site, W1, model)
    add_window(session, site, W2, model)
    body = {"from": BODY["from"], "to": BODY["to"]}
    first = post(client, site, body)
    again = post(client, site, BODY)
    assert first.status_code == 202 and again.status_code == 202
    assert first.json()["id"] == again.json()["id"]
    assert len(queue.messages) == 1


def test_create_change_existing_returns_200(
    client: TestClient,
    queue: InMemoryJobQueue,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    a = add_window(session, site, W1, model)
    b = add_window(session, site, W2, model)
    ch = make_change(session, site, model, a, b)
    r = post(client, site)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == str(ch.id) and body["site_id"] == str(site.id)
    assert body["from"]["season"] == W1 and body["to"]["season"] == W2
    assert body["from_classification_id"] == str(a[1].id)
    assert "X-Amz-Expires=900" in body["download_url"]
    assert not queue.messages


def test_create_change_classification_missing(
    client: TestClient,
    session: Session,
    site: Site,
    model: Model,  # noqa: F811
) -> None:
    add_window(session, site, W1, model)
    add_window(session, site, W2, None)  # composite exists, classification does not
    r = post(client, site)
    assert r.status_code == 422
    assert r.json()["code"] == "classification_missing"
    assert r.json()["detail"]["season"] == W2
    assert r.json()["detail"]["model"] == "unet-1.0.0"
    body = {"from": {"year": 2025, "season": "summer"}, "to": BODY["to"], "model": "unet-1.0.0"}
    r = post(client, site, body)  # no such composite at all
    assert r.status_code == 422 and r.json()["code"] == "classification_missing"


def test_create_change_order_and_validation(
    client: TestClient,
    session: Session,
    site: Site,
    model: Model,  # noqa: F811
) -> None:
    add_window(session, site, W1, model)
    add_window(session, site, W2, model)
    swapped = {"from": BODY["to"], "to": BODY["from"], "model": "unet-1.0.0"}
    r = post(client, site, swapped)
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    same = {"from": BODY["from"], "to": BODY["from"]}
    assert post(client, site, same).status_code == 422
    assert post(client, site, {"from": BODY["from"]}).status_code == 422
    bad_season = {"from": {"year": 2025, "season": "monsoon"}, "to": BODY["to"]}
    assert post(client, site, bad_season).status_code == 422
    assert post(client, site, {**BODY, "extra": 1}).status_code == 422
    assert post(client, site, {**BODY, "model": "nope-9.9"}).status_code == 422


def test_create_change_not_found_and_auth(client: TestClient, model: Model) -> None:
    r = client.post(f"/sites/{uuid.uuid4()}/changes", json=BODY, headers=H)
    assert r.status_code == 404
    assert client.post(f"/sites/{uuid.uuid4()}/changes", json=BODY).status_code == 401


def test_create_change_enqueue_failure(
    client: TestClient,
    queue: InMemoryJobQueue,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    add_window(session, site, W1, model)
    add_window(session, site, W2, model)
    queue.fail = True
    r = post(client, site)
    assert r.status_code == 503 and r.json()["code"] == "enqueue_failed"
    run = session.get(Run, uuid.UUID(r.json()["detail"]["run_id"]))
    assert run is not None and run.status == "failed" and run.error is not None
    queue.fail = False
    assert post(client, site).status_code == 202  # failed run does not block a retry


def test_create_change_rate_limited(
    session: Session,
    queue: InMemoryJobQueue,
    site: Site,
    model: Model,  # noqa: F811
) -> None:
    app = create_app(Settings(api_keys=hash_key(KEY), rate_limit_job_per_minute=1))
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_queue] = lambda: queue
    c = TestClient(app)
    assert post(c, site).status_code in (202, 422)
    r = post(c, site)
    assert r.status_code == 429 and "Retry-After" in r.headers


# ---- stats ------------------------------------------------------------------------------------


@pytest.fixture
def change(
    session: Session,
    site: Site,
    model: Model,  # noqa: F811
) -> Change:
    a = add_window(session, site, W1, model)
    b = add_window(session, site, W2, model)
    return make_change(session, site, model, a, b)


def test_stats_json_default(client: TestClient, change: Change) -> None:
    for headers in (H, {**H, "Accept": "*/*"}, {**H, "Accept": "application/json"}):
        r = client.get(f"/changes/{change.id}/stats", headers=headers)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["units"] == "hectares" and body["pixel_area_ha"] == 0.01
    assert body["total_ha"] == 100.0
    assert body["change"]["id"] == str(change.id)
    assert body["change"]["from"]["season"] == W1
    assert [(t["from_class"]["id"], t["to_class"]["id"]) for t in body["transitions"]] == [
        (0, 0),
        (1, 1),
        (1, 3),
    ]
    assert body["transitions"][2]["to_class"]["name"] == "felled_or_burnt_bare"
    assert body["transitions"][2]["area_ha"] == 10.5
    assert [a["class"]["id"] for a in body["from_class_areas"]] == [0, 1]
    assert body["to_class_areas"][2] == {
        "class": {
            "id": 3,
            "name": "felled_or_burnt_bare",
            "label": "Felled or burnt-bare",
            "hex": "#D55E00",
        },
        "area_ha": 10.5,
        "percent": 10.5,
    }


def test_stats_matches_fixture_shape(client: TestClient, change: Change) -> None:
    fixture = json.loads((CONTRACTS / "fixtures" / "changes.stats.200.json").read_text())
    body = client.get(f"/changes/{change.id}/stats", headers=H).json()
    assert body.keys() == fixture.keys()
    assert body["change"].keys() == fixture["change"].keys()
    assert body["transitions"][0].keys() == fixture["transitions"][0].keys()
    assert body["to_class_areas"][0].keys() == fixture["to_class_areas"][0].keys()


def test_stats_csv(client: TestClient, change: Change) -> None:
    r = client.get(f"/changes/{change.id}/stats", headers={**H, "Accept": "text/csv"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == (
        f'attachment; filename="glen-trool-2025-{W1}__2025-{W2}-unet-1.0.0.csv"'
    )
    lines = r.text.splitlines()
    assert lines == [
        "from_class_id,from_class,to_class_id,to_class,area_ha,percent",
        "0,other,0,other,20.00,20.00",
        "1,bog,1,bog,69.50,69.50",
        "1,bog,3,felled_or_burnt_bare,10.50,10.50",
    ]
    fixture = (CONTRACTS / "fixtures" / "changes.stats.200.csv").read_text().splitlines()
    assert lines[0] == fixture[0]


def test_stats_errors(client: TestClient, change: Change) -> None:
    r = client.get(f"/changes/{change.id}/stats", headers={**H, "Accept": "application/xml"})
    assert r.status_code == 406
    assert r.json() == {
        "code": "not_acceptable",
        "message": "Use Accept: application/json or text/csv.",
        "detail": {"accept": "application/xml"},
    }
    assert client.get(f"/changes/{uuid.uuid4()}/stats", headers=H).status_code == 404
    assert client.get("/changes/nope/stats", headers=H).status_code == 422
    assert client.get(f"/changes/{change.id}/stats").status_code == 401


def test_stats_storage_unavailable(
    client: TestClient, change: Change, store: InMemoryObjectStore
) -> None:
    store.fail = True
    r = client.get(f"/changes/{change.id}/stats", headers=H)
    assert r.status_code == 503 and r.json()["code"] == "storage_unavailable"
    # CSV needs no signed link
    assert (
        client.get(f"/changes/{change.id}/stats", headers={**H, "Accept": "text/csv"}).status_code
        == 200
    )
