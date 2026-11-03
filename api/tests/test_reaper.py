"""Stale-run reaper tests (DB tests skip without PostGIS)."""

# ruff: noqa: F811  (pytest fixtures are imported and re-declared as test arguments)

import uuid
from typing import Any

from api.db.models import Model, Run, Site
from api.queue import InMemoryJobQueue
from api.reaper import reap_stale_runs
from api.worker import Worker
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session
from test_db import conn, db_url, engine, make_model, make_run, make_site, session  # noqa: F401
from test_worker import Steps, model, new_run, queue, row, site, steps, worker  # noqa: F401


def set_state(session: Session, run: Run, **cols: str | None) -> None:  # noqa: F811
    """Set raw SQL expressions for the given columns (values are trusted test literals)."""
    sets = ", ".join(f"{k} = {v}" for k, v in cols.items())
    session.execute(text(f"UPDATE runs SET {sets} WHERE id = :id"), {"id": run.id})  # noqa: S608


def make_running(
    session: Session, run: Run, heartbeat: str | None, started: str = "1 hour"
) -> None:  # noqa: F811
    hb = f"now() - interval '{heartbeat}'" if heartbeat else "NULL"
    set_state(
        session,
        run,
        status="'running'",
        started_at=f"now() - interval '{started}'",
        heartbeat_at=hb,
    )


def test_stale_running_run_fails_with_worker_lost(
    conn: Connection,  # noqa: F811
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)
    make_running(session, run, "20 minutes")
    ids = reap_stale_runs(conn, 900)
    assert ids == [run.id]
    r = row(session, run)
    assert r.status == "failed"
    assert r.error["code"] == "worker_lost"
    assert "stopped responding" in r.error["message"]
    assert r.finished_at is not None


def test_fresh_heartbeat_is_untouched(
    conn: Connection,  # noqa: F811
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)
    make_running(session, run, "1 minute")
    assert reap_stale_runs(conn, 900) == []
    r = row(session, run)
    assert r.status == "running" and r.error is None and r.finished_at is None


def test_queued_and_terminal_runs_are_untouched(
    conn: Connection,  # noqa: F811
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    queued = new_run(session, site, model)
    set_state(session, queued, created_at="now() - interval '2 days'")
    done: dict[str, Run] = {}
    for status in ("succeeded", "failed", "cancelled"):
        run = new_run(session, site, model)
        make_running(session, run, "2 hours", started="2 hours")  # state machine: via running
        extra: dict[str, str | None] = {
            "status": f"'{status}'",
            "finished_at": "now() - interval '1 hour'",
        }
        if status == "failed":
            extra["error"] = '\'{"code": "step_failed", "message": "x"}\'::jsonb'
        set_state(session, run, **extra)
        done[status] = run
    before = {s: row(session, r) for s, r in done.items()}
    assert reap_stale_runs(conn, 900) == []
    assert row(session, queued).status == "queued"
    for status, run in done.items():
        after = row(session, run)
        assert after.status == status
        assert after.error == before[status].error
        assert after.finished_at == before[status].finished_at


def test_null_heartbeat_uses_started_at(
    conn: Connection,  # noqa: F811
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    old = new_run(session, site, model)
    make_running(session, old, None, started="1 hour")
    recent = new_run(session, site, model)
    make_running(session, recent, None, started="1 minute")
    assert reap_stale_runs(conn, 900) == [old.id]
    assert row(session, old).status == "failed"
    assert row(session, recent).status == "running"


def test_engine_form_commits_its_own_transaction(engine: Any, session: Session) -> None:  # noqa: F811
    # With nothing stale it is a no-op that must still run on a real Engine.
    assert reap_stale_runs(engine, 10**9) == []


def test_worker_loop_invokes_reaper(
    worker: Worker,
    queue: InMemoryJobQueue,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)
    make_running(session, run, "20 minutes")
    worker.reap_interval = 0
    worker.reap_stale_after = 900
    assert worker.poll_once() == 0
    assert row(session, run).status == "failed"
    assert row(session, run).error["code"] == "worker_lost"


def test_worker_reaper_is_throttled_and_disabled_by_default(
    worker: Worker,
    session: Session,  # noqa: F811
    site: Site,
    model: Model,
) -> None:
    run = new_run(session, site, model)
    make_running(session, run, "20 minutes")
    assert worker.reap_interval is None
    worker.poll_once()
    assert row(session, run).status == "running"  # disabled

    worker.reap_interval = 3600
    worker._last_reap = None
    assert worker.reap_if_due() == [run.id]
    run2 = new_run(session, site, model)
    make_running(session, run2, "20 minutes")
    assert worker.reap_if_due() == []  # throttled: ran moments ago
    assert row(session, run2).status == "running"


def test_worker_reaper_failure_does_not_crash_the_loop(worker: Worker, monkeypatch: Any) -> None:
    def boom(*_: Any, **__: Any) -> list[uuid.UUID]:
        raise RuntimeError("db down")

    monkeypatch.setattr("api.worker.reap_stale_runs", boom)
    worker.reap_interval = 0
    assert worker.poll_once() == 0
