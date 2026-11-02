"""Runs endpoints with the in-memory queue fake (DB tests skip if PostGIS is unreachable)."""

import datetime as dt
import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from api.auth import hash_key
from api.db.models import Model, Run, Site
from api.db.session import get_session
from api.main import create_app
from api.queue import InMemoryJobQueue, SqsJobQueue, get_queue
from api.routes.runs import season_for
from api.settings import Settings
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session
from test_db import conn, db_url, engine, make_model, make_site, session  # noqa: F401

KEY = "runs-test-key"
H = {"X-API-Key": KEY}
CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
VALIDATOR = Draft202012Validator(
    json.loads((CONTRACTS / "queue.schema.json").read_text()),
    format_checker=Draft202012Validator.FORMAT_CHECKER,
)
BODY: dict[str, Any] = {
    "windows": [
        {"start": "2025-04-08", "end": "2025-04-08"},
        {"start": "2025-03-19", "end": "2025-03-19"},
    ],
    "model": "unet-1.0.0",
    "max_cloud_cover_pct": 60,
}


def test_season_for() -> None:
    d = dt.date
    assert season_for(d(2025, 6, 1), d(2025, 8, 31)) == (2025, "summer")
    assert season_for(d(2025, 3, 1), d(2025, 5, 31)) == (2025, "spring")
    assert season_for(d(2025, 9, 1), d(2025, 11, 30)) == (2025, "autumn")
    assert season_for(d(2024, 12, 1), d(2025, 2, 28)) == (2024, "winter")
    assert season_for(d(2023, 12, 1), d(2024, 2, 29)) == (2023, "winter")
    assert season_for(d(2025, 1, 1), d(2025, 12, 31)) == (2025, "annual")
    assert season_for(d(2025, 3, 19), d(2025, 3, 19)) == (2025, "w20250319-20250319")


@pytest.fixture
def queue() -> InMemoryJobQueue:
    return InMemoryJobQueue()


@pytest.fixture
def client(session: Session, queue: InMemoryJobQueue) -> Iterator[TestClient]:  # noqa: F811
    app = create_app(Settings(api_keys=hash_key(KEY)))
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_queue] = lambda: queue
    yield TestClient(app)


@pytest.fixture
def site(session: Session) -> Site:  # noqa: F811
    return make_site(session)


@pytest.fixture
def model(session: Session) -> Model:  # noqa: F811
    m = make_model(session, "unet", "1.0.0")
    m.is_default = True
    session.flush()
    return m


def post(client: TestClient, site: Site, body: Any = BODY) -> Any:
    return client.post(f"/sites/{site.id}/runs", json=body, headers=H)


def test_create_run_enqueues(
    client: TestClient,
    queue: InMemoryJobQueue,
    site: Site,
    model: Model,
    session: Session,  # noqa: F811
) -> None:
    r = post(client, site)
    assert r.status_code == 202, r.text
    body = r.json()
    assert r.headers["Location"].endswith(f"/runs/{body['id']}")
    assert body["status"] == "queued"
    assert body["kind"] == "pipeline"
    assert body["site_id"] == str(site.id)
    assert body["model"] == {"id": str(model.id), "name": "unet", "version": "1.0.0"}
    assert body["params"] == {
        "windows": [
            {"start": "2025-03-19", "end": "2025-03-19"},
            {"start": "2025-04-08", "end": "2025-04-08"},
        ],
        "model": "unet-1.0.0",
        "max_cloud_cover_pct": 60,
    }
    assert body["progress"] == {} and body["error"] is None and body["started_at"] is None

    run = session.get(Run, uuid.UUID(body["id"]))
    assert run is not None and run.status == "queued"
    assert run.log_uri == f"s3://peatwatch-artifacts/runs/{run.id}/log.jsonl"

    assert len(queue.messages) == 1
    msg = queue.messages[0]
    VALIDATOR.validate(msg)
    assert msg["run_id"] == body["id"]
    assert msg["idempotency_key"] == run.idempotency_key
    assert msg["site"] == {"id": str(site.id), "slug": site.slug}
    assert msg["windows"][0] == {
        "year": 2025,
        "season": "w20250319-20250319",
        "start": "2025-03-19",
        "end": "2025-03-19",
    }
    assert msg["params"] == body["params"]


def test_get_run(client: TestClient, site: Site, model: Model) -> None:
    run_id = post(client, site).json()["id"]
    r = client.get(f"/runs/{run_id}", headers=H)
    assert r.status_code == 200
    assert r.json()["id"] == run_id


def test_get_run_not_found_and_invalid(client: TestClient) -> None:
    r = client.get(f"/runs/{uuid.uuid4()}", headers=H)
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert client.get("/runs/nope", headers=H).status_code == 422


def test_idempotent_while_active(
    client: TestClient,
    queue: InMemoryJobQueue,
    site: Site,
    model: Model,
    session: Session,  # noqa: F811
) -> None:
    first = post(client, site)
    # Same windows in another order, default model and cloud threshold spelled differently.
    second = post(
        client,
        site,
        {"windows": list(reversed(BODY["windows"])), "max_cloud_cover_pct": 60.0},
    )
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(queue.messages) == 1

    # Different parameters give a new run.
    third = post(client, site, {**BODY, "max_cloud_cover_pct": 30})
    assert third.status_code == 202
    assert third.json()["id"] != first.json()["id"]
    assert len(queue.messages) == 2

    # Once terminal, the same request creates a new run.
    run = session.get(Run, uuid.UUID(first.json()["id"]))
    assert run is not None
    run.status = "failed"
    run.finished_at = dt.datetime.now(dt.UTC)
    run.error = {"code": "step_failed", "message": "x", "detail": None}
    session.flush()
    again = post(client, site)
    assert again.status_code == 202
    assert again.json()["id"] != first.json()["id"]
    assert len(queue.messages) == 3


def test_default_model_when_omitted(client: TestClient, site: Site, model: Model) -> None:
    r = post(client, site, {"windows": BODY["windows"]})
    assert r.status_code == 202
    assert r.json()["params"]["model"] == "unet-1.0.0"
    assert r.json()["params"]["max_cloud_cover_pct"] == 60


def test_enqueue_failure_marks_run_failed(
    client: TestClient,
    queue: InMemoryJobQueue,
    site: Site,
    model: Model,
    session: Session,  # noqa: F811
) -> None:
    queue.fail = True
    r = post(client, site)
    assert r.status_code == 503
    assert r.json()["code"] == "enqueue_failed"
    run = session.get(Run, uuid.UUID(r.json()["detail"]["run_id"]))
    assert run is not None
    assert run.status == "failed" and run.error is not None
    assert run.error["code"] == "enqueue_failed" and run.finished_at is not None
    # A failed run is terminal, so a retry creates a fresh one.
    queue.fail = False
    assert post(client, site).status_code == 202


def test_errors(client: TestClient, site: Site, model: Model) -> None:
    missing = client.post(f"/sites/{uuid.uuid4()}/runs", json=BODY, headers=H)
    assert missing.status_code == 404
    unknown_model = post(client, site, {**BODY, "model": "nope-9.9"})
    assert unknown_model.status_code == 422
    assert unknown_model.json()["code"] == "validation_error"
    backwards = post(client, site, {"windows": [{"start": "2025-04-08", "end": "2025-04-01"}]})
    assert backwards.status_code == 422
    assert post(client, site, {"windows": []}).status_code == 422
    assert post(client, site, {**BODY, "extra": 1}).status_code == 422
    assert post(client, site, {**BODY, "max_cloud_cover_pct": 101}).status_code == 422
    too_old = post(client, site, {"windows": [{"start": "2010-01-01", "end": "2010-01-02"}]})
    assert too_old.status_code == 422
    assert client.post(f"/sites/{site.id}/runs", json=BODY).status_code == 401


def test_no_default_model(client: TestClient, site: Site) -> None:
    r = post(client, site, {"windows": BODY["windows"]})
    assert r.status_code == 422


def test_rate_limited_at_ten_per_minute(
    client: TestClient, queue: InMemoryJobQueue, site: Site, model: Model
) -> None:
    for i in range(10):
        r = post(client, site, {**BODY, "max_cloud_cover_pct": i})
        assert r.status_code == 202
    limited = post(client, site, {**BODY, "max_cloud_cover_pct": 99})
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


@pytest.mark.localstack
@pytest.mark.skipif(os.environ.get("RUN_LOCALSTACK") != "1", reason="needs LocalStack")
def test_localstack_send_message(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", os.environ.get("AWS_ACCESS_KEY_ID", "test"))
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", os.environ.get("AWS_SECRET_ACCESS_KEY", "test"))
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
    settings = Settings(aws_endpoint_url=endpoint)
    sqs = boto3.client("sqs", region_name=settings.aws_region, endpoint_url=endpoint)
    url = sqs.get_queue_url(QueueName="peatwatch-jobs")["QueueUrl"]
    sqs.purge_queue(QueueUrl=url)

    msg = json.loads((CONTRACTS / "fixtures" / "queue.pipeline.json").read_text())
    msg["run_id"] = str(uuid.uuid4())
    SqsJobQueue(settings).send(msg)

    got = sqs.receive_message(QueueUrl=url, WaitTimeSeconds=5, MaxNumberOfMessages=1)
    body = json.loads(got["Messages"][0]["Body"])
    assert body == msg
    VALIDATOR.validate(body)
    sqs.delete_message(QueueUrl=url, ReceiptHandle=got["Messages"][0]["ReceiptHandle"])
