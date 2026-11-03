"""Worker tests with the in-memory queue and mock steps (DB tests skip without PostGIS)."""

import json
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from api.db.models import Model, Run, Site
from api.queue import InMemoryJobQueue
from api.worker import StepContext, StepError, Worker, load_validator
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_db import conn, db_url, engine, make_model, make_run, make_site, session  # noqa: F401

FIXTURE = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "queue.pipeline.json"


class Steps:
    """Records calls; `pipeline_error` / `inference_error` are raised when set."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.contexts: list[StepContext] = []
        self.pipeline_error: Exception | None = None
        self.inference_error: Exception | None = None
        self.on_pipeline: Callable[[], None] | None = None

    def pipeline(self, ctx: StepContext) -> Mapping[str, Any]:
        self.calls.append("pipeline")
        self.contexts.append(ctx)
        if self.on_pipeline:
            self.on_pipeline()
        if self.pipeline_error:
            raise self.pipeline_error
        return {"composite_ids": ["c1"]}

    def inference(self, ctx: StepContext) -> Mapping[str, Any]:
        self.calls.append("inference")
        self.contexts.append(ctx)
        if self.inference_error:
            raise self.inference_error
        return {"classification_ids": ["k1"]}


@pytest.fixture
def steps() -> Steps:
    return Steps()


@pytest.fixture
def queue() -> InMemoryJobQueue:
    return InMemoryJobQueue()


@pytest.fixture
def site(session: Session) -> Site:  # noqa: F811
    return make_site(session)


@pytest.fixture
def model(session: Session) -> Model:  # noqa: F811
    return make_model(session, "unet", "1.0.0")


@pytest.fixture
def worker(session: Session, queue: InMemoryJobQueue, steps: Steps) -> Worker:  # noqa: F811
    @contextmanager
    def factory() -> Iterator[Session]:
        yield session

    return Worker(
        queue,
        factory,
        steps.pipeline,
        steps.inference,
        validator=load_validator(),
        heartbeat_interval=3600,
        stale_after=300,
        wait_seconds=0,
    )


def body_for(run: Run, site: Site, **over: Any) -> dict[str, Any]:
    msg = json.loads(FIXTURE.read_text())
    msg.update(
        run_id=str(run.id),
        site={"id": str(site.id), "slug": site.slug},
        idempotency_key=run.idempotency_key,
    )
    msg.update(over)
    return msg


def row(session: Session, run: Run) -> Any:  # noqa: F811
    return session.execute(
        text(
            "SELECT status, error, outputs, progress, started_at, finished_at, heartbeat_at "
            "FROM runs WHERE id = :id"
        ),
        {"id": run.id},
    ).one()


def new_run(session: Session, site: Site, model: Model, **kw: Any) -> Run:  # noqa: F811
    return make_run(session, site, model, uuid.uuid4().hex + "0" * 32, **kw)


def test_success(
    worker: Worker,
    queue: InMemoryJobQueue,
    steps: Steps,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)
    queue.send(body_for(run, site))
    assert worker.poll_once() == 1
    r = row(session, run)
    assert r.status == "succeeded" and r.error is None
    assert r.started_at is not None and r.finished_at is not None and r.heartbeat_at is not None
    assert r.outputs == {"composite_ids": ["c1"], "classification_ids": ["k1"]}
    assert r.progress["step"] == "inference"
    assert steps.calls == ["pipeline", "inference"]
    assert steps.contexts[1].outputs == {"composite_ids": ["c1"]}
    assert len(queue.deleted) == 1 and not queue.in_flight


def test_handled_failure_marks_failed_and_deletes(
    worker: Worker,
    queue: InMemoryJobQueue,
    steps: Steps,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)
    steps.pipeline_error = StepError("no_usable_scenes", "No scenes.", {"n": 0})
    queue.send(body_for(run, site))
    worker.poll_once()
    r = row(session, run)
    assert r.status == "failed" and r.finished_at is not None
    assert r.error == {"code": "no_usable_scenes", "message": "No scenes.", "detail": {"n": 0}}
    assert steps.calls == ["pipeline"]
    assert len(queue.deleted) == 1


def test_crash_leaves_message_then_takeover(
    worker: Worker,
    queue: InMemoryJobQueue,
    steps: Steps,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)
    steps.inference_error = RuntimeError("boom")
    queue.send(body_for(run, site))
    worker.poll_once()
    assert row(session, run).status == "running"  # not failed, not deleted
    assert queue.deleted == [] and len(queue.in_flight) == 1

    # Message reappears; heartbeat is still fresh, so another worker holds it: ack and drop.
    queue.expire_visibility()
    steps.calls.clear()
    worker.poll_once()
    assert steps.calls == [] and len(queue.deleted) == 1


def test_stale_running_run_is_taken_over(
    worker: Worker,
    queue: InMemoryJobQueue,
    steps: Steps,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)
    session.execute(
        text(
            "UPDATE runs SET status='running', started_at=now() - interval '1 hour', "
            "heartbeat_at=now() - interval '1 hour' WHERE id=:id"
        ),
        {"id": run.id},
    )
    queue.send(body_for(run, site))
    worker.poll_once()
    assert row(session, run).status == "succeeded"
    assert steps.calls == ["pipeline", "inference"]


def test_repeated_crash_fails_run_on_last_receive(
    worker: Worker,
    queue: InMemoryJobQueue,
    steps: Steps,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)
    steps.pipeline_error = RuntimeError("boom")
    queue.send(body_for(run, site))
    for _ in range(3):
        worker.poll_once()
        session.execute(
            text("UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id = :id"),
            {"id": run.id},
        )
        queue.expire_visibility()
    r = row(session, run)
    assert r.status == "failed" and r.error["code"] == "step_failed"
    assert queue.deleted == []
    queue.expire_visibility()
    assert worker.poll_once() == 0 and len(queue.dlq) == 1  # fourth receive goes to the DLQ


def test_terminal_or_missing_claim_deletes_message(
    worker: Worker,
    queue: InMemoryJobQueue,
    steps: Steps,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)
    session.execute(
        text("UPDATE runs SET status='cancelled', finished_at=now() WHERE id=:id"), {"id": run.id}
    )
    queue.send(body_for(run, site))
    worker.poll_once()
    assert steps.calls == [] and len(queue.deleted) == 1
    assert row(session, run).status == "cancelled"


def test_cancel_between_steps_stops(
    worker: Worker,
    queue: InMemoryJobQueue,
    steps: Steps,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)

    def cancel() -> None:
        session.execute(
            text("UPDATE runs SET status='cancelled', finished_at=now() WHERE id=:id"),
            {"id": run.id},
        )

    steps.on_pipeline = cancel
    queue.send(body_for(run, site))
    worker.poll_once()
    assert steps.calls == ["pipeline"]
    assert row(session, run).status == "cancelled" and len(queue.deleted) == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(contract_version=2),
        lambda m: m.pop("windows"),
        lambda m: m.update(extra=1),
        lambda m: m.update(kind="change", windows=m["windows"][:1]),  # change needs two
    ],
)
def test_invalid_message_is_poison(
    worker: Worker,
    queue: InMemoryJobQueue,
    steps: Steps,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
    mutate: Callable[[dict[str, Any]], Any],
) -> None:
    run = new_run(session, site, model)
    msg = body_for(run, site)
    mutate(msg)
    queue.send(msg)
    worker.poll_once()
    r = row(session, run)
    assert r.status == "failed" and r.error["code"] == "step_failed"
    assert steps.calls == [] and queue.deleted == []  # not deleted: heads for the DLQ


def test_unparseable_body_and_unknown_run(
    worker: Worker,
    queue: InMemoryJobQueue,
    steps: Steps,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    queue.send_raw("not json")
    queue.send_raw(json.dumps({"run_id": str(uuid.uuid4())}))
    queue.send(body_for(new_run(session, site, model), site, run_id=str(uuid.uuid4())))
    for _ in range(4):
        while worker.poll_once():
            pass
        queue.expire_visibility()
    assert steps.calls == [] and queue.deleted == []
    assert len(queue.dlq) == 3


def test_site_or_model_mismatch_is_poison(
    worker: Worker,
    queue: InMemoryJobQueue,
    steps: Steps,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)
    other = make_site(session)
    queue.send(body_for(run, other))
    worker.poll_once()
    assert row(session, run).status == "failed"
    run2 = new_run(session, site, model)
    queue.send(body_for(run2, site, model={"name": "rf", "version": "9"}))
    worker.poll_once()
    assert row(session, run2).status == "failed"
    assert steps.calls == [] and queue.deleted == []


def test_heartbeat_updates_row_and_visibility(
    worker: Worker,
    queue: InMemoryJobQueue,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)
    session.execute(
        text(
            "UPDATE runs SET status='running', started_at=now() - interval '1 hour', "
            "heartbeat_at=now() - interval '1 hour' WHERE id=:id"
        ),
        {"id": run.id},
    )
    queue.send(body_for(run, site))
    (msg,) = queue.receive(0)
    worker.beat(run.id, msg)
    stale = session.execute(
        text("SELECT heartbeat_at < now() - interval '1 minute' FROM runs WHERE id=:id"),
        {"id": run.id},
    ).scalar()
    assert stale is False
    assert queue.visibility_extensions == [(msg.receipt, 900)]


def test_heartbeat_thread_runs_during_steps(
    queue: InMemoryJobQueue,
    steps: Steps,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    beats = threading.Event()

    class Spy(Worker):
        def beat(self, run_id: uuid.UUID, message: Any) -> None:  # no DB access off-thread
            self.consumer.extend_visibility(message, 900)
            beats.set()

    @contextmanager
    def factory() -> Iterator[Session]:
        yield session

    w = Spy(
        queue,
        factory,
        steps.pipeline,
        steps.inference,
        validator=load_validator(),
        heartbeat_interval=0.01,
        wait_seconds=0,
    )
    steps.on_pipeline = lambda: beats.wait(5)
    run = new_run(session, site, model)
    queue.send(body_for(run, site))
    w.poll_once()
    assert beats.is_set() and queue.visibility_extensions
    assert row(session, run).status == "succeeded"


def test_step_error_is_one_class_when_worker_runs_as_main() -> None:
    """`python -m api.worker` makes the worker module `__main__`; StepError must not live there."""
    from api import steps, worker

    assert worker.StepError is steps.StepError
    assert worker.StepError.__module__ == "api.step_types"
