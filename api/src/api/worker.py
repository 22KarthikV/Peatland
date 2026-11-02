"""Queue worker (contracts/queue.md, db-schema.md section 4).

Receive -> validate -> claim (compare-and-set) -> pipeline step -> inference step -> finish.
The steps are injected callables; the real ones are wired after CP2. Run with
`python -m api.worker`.
"""

import contextlib
import json
import logging
import os
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.queue import JobConsumer, QueueMessage
from api.settings import Settings

logger = logging.getLogger(__name__)

SCHEMA_ENV = "QUEUE_SCHEMA_PATH"
DEFAULT_SCHEMA = Path(__file__).resolve().parents[3] / "contracts" / "queue.schema.json"
MAX_RECEIVE_COUNT = 3
VISIBILITY_SECONDS = 900


class StepError(Exception):
    """A handled failure: the run goes to `failed` with this error and the message is deleted."""

    def __init__(self, code: str, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


@dataclass(frozen=True)
class StepContext:
    run_id: uuid.UUID
    site_id: uuid.UUID
    message: Mapping[str, Any]
    outputs: Mapping[str, Any]  # outputs of earlier steps


Step = Callable[[StepContext], Mapping[str, Any]]
SessionFactory = Callable[[], AbstractContextManager[Session]]


def _unwired(name: str) -> Step:
    def step(ctx: StepContext) -> Mapping[str, Any]:
        raise StepError("step_failed", f"The {name} step is not wired into this worker.")

    return step


def load_validator() -> Draft202012Validator:
    path = Path(os.environ.get(SCHEMA_ENV) or DEFAULT_SCHEMA)
    return Draft202012Validator(
        json.loads(path.read_text()), format_checker=Draft202012Validator.FORMAT_CHECKER
    )


class Worker:
    def __init__(
        self,
        consumer: JobConsumer,
        session_factory: SessionFactory,
        pipeline_step: Step,
        inference_step: Step,
        *,
        validator: Draft202012Validator | None = None,
        heartbeat_interval: float = 60.0,
        stale_after: float = 300.0,
        wait_seconds: int = 20,
    ) -> None:
        self.consumer = consumer
        self.session_factory = session_factory
        self.steps: list[tuple[str, Step]] = [
            ("pipeline", pipeline_step),
            ("inference", inference_step),
        ]
        self.validator = validator or load_validator()
        self.heartbeat_interval = heartbeat_interval
        self.stale_after = stale_after
        self.wait_seconds = wait_seconds

    # ---- loop ------------------------------------------------------------------------------

    def poll_once(self) -> int:
        """Receive one batch and process it; returns the number of messages received."""
        messages = self.consumer.receive(self.wait_seconds, 1)
        for message in messages:
            try:
                self.handle(message)
            except Exception:
                # Crash path: leave the message undeleted so it reappears (queue.md item 3).
                logger.exception("unhandled error processing message %s", message.receipt)
        return len(messages)

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception("receive failed")
                stop.wait(5)

    # ---- one message -----------------------------------------------------------------------

    def handle(self, message: QueueMessage) -> None:
        body, problem = self._parse(message.body)
        if body is None:
            self._poison(message, problem or "invalid message")
            return
        run_id = uuid.UUID(body["run_id"])
        site_id = uuid.UUID(body["site"]["id"])

        with self.session_factory() as session:
            problem = self._check_references(session, body, run_id, site_id)
            if problem:
                self._poison(message, problem, run_id)
                return
            if not self._claim(session, run_id):
                self.consumer.delete(message)  # another worker has it, or it is terminal
                return

        stop = threading.Event()
        beat = threading.Thread(target=self._heartbeat_loop, args=(stop, run_id, message))
        beat.daemon = True
        beat.start()
        try:
            self._execute(message, body, run_id, site_id)
        finally:
            stop.set()
            beat.join(timeout=5)

    def _parse(self, raw: str) -> tuple[dict[str, Any] | None, str | None]:
        try:
            body = json.loads(raw)
        except ValueError as exc:
            return None, f"body is not JSON: {exc}"
        if not isinstance(body, dict):
            return None, "body is not a JSON object"
        errors = sorted(self.validator.iter_errors(body), key=lambda e: list(e.path))
        if errors:
            return None, f"schema violation: {errors[0].message}"
        return body, None

    def _poison(self, message: QueueMessage, problem: str, run_id: uuid.UUID | None = None) -> None:
        """Do not delete: the message reaches the DLQ after 3 receives. Fail the run if known."""
        logger.error("poison message %s: %s", message.receipt, problem)
        if run_id is None:
            run_id = _readable_run_id(message.body)
        if run_id is not None:
            self._fail(run_id, "step_failed", f"Invalid job message: {problem}", None)

    def _check_references(
        self, session: Session, body: dict[str, Any], run_id: uuid.UUID, site_id: uuid.UUID
    ) -> str | None:
        row = session.execute(
            text(
                "SELECT r.site_id, s.slug, m.name, m.version FROM runs r "
                "JOIN sites s ON s.id = r.site_id JOIN models m ON m.id = r.model_id "
                "WHERE r.id = :id"
            ),
            {"id": run_id},
        ).first()
        if row is None:
            return "unknown run"
        if row.site_id != site_id or row.slug != body["site"]["slug"]:
            return "site does not match the run"
        if (row.name, row.version) != (body["model"]["name"], body["model"]["version"]):
            return "model does not match the run"
        return None

    def _claim(self, session: Session, run_id: uuid.UUID) -> bool:
        claimed = session.execute(
            text(
                "UPDATE runs SET status = 'running', started_at = now(), heartbeat_at = now() "
                "WHERE id = :id AND status = 'queued' RETURNING id"
            ),
            {"id": run_id},
        ).first()
        if claimed is None:
            # Takeover after a crash: running with a stale heartbeat (queue.md item 3).
            claimed = session.execute(
                text(
                    "UPDATE runs SET heartbeat_at = now() WHERE id = :id AND status = 'running' "
                    "AND (heartbeat_at IS NULL "
                    "OR heartbeat_at < now() - make_interval(secs => :stale)) RETURNING id"
                ),
                {"id": run_id, "stale": self.stale_after},
            ).first()
        session.commit()
        return claimed is not None

    def _execute(
        self, message: QueueMessage, body: dict[str, Any], run_id: uuid.UUID, site_id: uuid.UUID
    ) -> None:
        outputs: dict[str, Any] = {}
        try:
            for index, (name, step) in enumerate(self.steps):
                if self._status(run_id) != "running":  # cancelled between steps
                    self.consumer.delete(message)
                    return
                self._progress(run_id, name, index, len(self.steps))
                result = step(StepContext(run_id, site_id, body, dict(outputs)))
                outputs.update(result)
        except StepError as exc:
            self._fail(run_id, exc.code, exc.message, exc.detail)
            self.consumer.delete(message)
            return
        except Exception as exc:
            if message.receive_count >= MAX_RECEIVE_COUNT:
                self._fail(run_id, "step_failed", f"Step crashed: {exc}", None)
            raise
        self._succeed(run_id, outputs)
        self.consumer.delete(message)

    # ---- heartbeat -------------------------------------------------------------------------

    def _heartbeat_loop(self, stop: threading.Event, run_id: uuid.UUID, msg: QueueMessage) -> None:
        while not stop.wait(self.heartbeat_interval):
            try:
                self.beat(run_id, msg)
            except Exception:
                logger.exception("heartbeat failed for run %s", run_id)

    def beat(self, run_id: uuid.UUID, message: QueueMessage) -> None:
        with self.session_factory() as session:
            session.execute(
                text("UPDATE runs SET heartbeat_at = now() WHERE id = :id AND status = 'running'"),
                {"id": run_id},
            )
            session.commit()
        self.consumer.extend_visibility(message, VISIBILITY_SECONDS)

    # ---- run updates -----------------------------------------------------------------------

    def _status(self, run_id: uuid.UUID) -> str | None:
        with self.session_factory() as session:
            value = session.execute(
                text("SELECT status FROM runs WHERE id = :id"), {"id": run_id}
            ).scalar()
            session.commit()
            return None if value is None else str(value)

    def _progress(self, run_id: uuid.UUID, step: str, done: int, total: int) -> None:
        with self.session_factory() as session:
            session.execute(
                text("UPDATE runs SET progress = CAST(:p AS jsonb) WHERE id = :id"),
                {"id": run_id, "p": json.dumps({"step": step, "done": done, "total": total})},
            )
            session.commit()

    def _succeed(self, run_id: uuid.UUID, outputs: Mapping[str, Any]) -> None:
        with self.session_factory() as session:
            session.execute(
                text(
                    "UPDATE runs SET status = 'succeeded', finished_at = now(), "
                    "outputs = CAST(:o AS jsonb) WHERE id = :id AND status = 'running'"
                ),
                {"id": run_id, "o": json.dumps(dict(outputs))},
            )
            session.commit()

    def _fail(self, run_id: uuid.UUID, code: str, message: str, detail: Any) -> None:
        error = {"code": code, "message": message, "detail": detail}
        with self.session_factory() as session:
            session.execute(
                text(
                    "UPDATE runs SET status = 'failed', finished_at = now(), "
                    "error = CAST(:e AS jsonb) "
                    "WHERE id = :id AND status IN ('queued', 'running')"
                ),
                {"id": run_id, "e": json.dumps(error)},
            )
            session.commit()


def _readable_run_id(raw: str) -> uuid.UUID | None:
    with contextlib.suppress(ValueError, TypeError, AttributeError):
        return uuid.UUID(str(json.loads(raw)["run_id"]))
    return None


def main() -> None:
    import signal

    from api.db.session import make_session_factory
    from api.queue import SqsJobQueue

    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    factory = make_session_factory()
    worker = Worker(
        SqsJobQueue(settings),
        lambda: factory(),
        _unwired("pipeline"),
        _unwired("inference"),
    )
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    worker.run_forever(stop)


if __name__ == "__main__":
    main()
