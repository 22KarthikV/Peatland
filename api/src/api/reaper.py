"""Stale-run reaper (contracts/queue.md item 3, db-schema.md section 4).

A run left in `running` whose worker stopped heartbeating is moved to `failed` with error
`worker_lost`, so the status screen never hangs. One compare-and-set UPDATE does it: under READ
COMMITTED a concurrent heartbeat makes the UPDATE re-check the row, which then no longer
qualifies, so a live worker's run is never failed. Terminal and `queued` runs are never touched.
"""

import json
import logging
import uuid

from sqlalchemy import Connection, Engine, text

logger = logging.getLogger(__name__)

WORKER_LOST = "worker_lost"

_REAP_SQL = text(
    "UPDATE runs SET status = 'failed', finished_at = now(), error = CAST(:e AS jsonb) "
    "WHERE status = 'running' "
    "AND COALESCE(heartbeat_at, started_at, created_at) "
    "< now() - make_interval(secs => :stale) "
    "RETURNING id"
)


def _error(stale_after_s: float) -> dict[str, object]:
    minutes = max(1, round(stale_after_s / 60))
    return {
        "code": WORKER_LOST,
        "message": (
            "The worker processing this run stopped responding "
            f"(no heartbeat for more than {minutes} min). Submit the request again to retry."
        ),
        "detail": {"stale_after_s": stale_after_s},
    }


def reap_stale_runs(engine: Engine | Connection, stale_after_s: float) -> list[uuid.UUID]:
    """Fail `running` runs whose last heartbeat (else `started_at`) is older than the timeout.

    Given an Engine it commits its own transaction; given a Connection it runs inside the
    caller's transaction and leaves the commit to the caller. Returns the failed run ids.
    """
    params = {"e": json.dumps(_error(stale_after_s)), "stale": stale_after_s}
    if isinstance(engine, Engine):
        with engine.begin() as conn:
            rows = conn.execute(_REAP_SQL, params).all()
    else:
        rows = engine.execute(_REAP_SQL, params).all()
    ids = [row.id for row in rows]
    for run_id in ids:
        logger.warning("run %s failed as worker_lost (stale heartbeat)", run_id)
    return ids
