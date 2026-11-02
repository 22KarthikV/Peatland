"""Job queue access behind a small interface (contracts/queue.md).

`SqsJobQueue` is the real boto3 implementation; `InMemoryJobQueue` is the test fake. Routes get
the queue through the `get_queue` dependency, which tests override.
"""

import json
from dataclasses import dataclass
from functools import cached_property
from typing import Annotated, Any, Protocol

from fastapi import Depends, Request

from api.settings import Settings


class JobQueue(Protocol):
    def send(self, body: dict[str, Any]) -> None:
        """Send one job message; raise on failure."""
        ...


@dataclass(frozen=True)
class QueueMessage:
    """One received message: opaque receipt handle, raw body, and SQS ApproximateReceiveCount."""

    receipt: str
    body: str
    receive_count: int


class JobConsumer(Protocol):
    def receive(self, wait_seconds: int, max_messages: int = 1) -> list[QueueMessage]: ...

    def delete(self, message: QueueMessage) -> None: ...

    def extend_visibility(self, message: QueueMessage, seconds: int) -> None: ...


def encode(body: dict[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class InMemoryJobQueue:
    """Test double: records bodies; set `fail` to simulate a send error."""

    MAX_RECEIVE_COUNT = 3  # redrive policy (contracts/queue.md)

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.fail = False
        self.pending: list[QueueMessage] = []
        self.in_flight: dict[str, QueueMessage] = {}
        self.dlq: list[QueueMessage] = []
        self.deleted: list[str] = []
        self.visibility_extensions: list[tuple[str, int]] = []
        self._counter = 0

    def _enqueue(self, body: str, receive_count: int = 0) -> None:
        self._counter += 1
        self.pending.append(QueueMessage(f"rh-{self._counter}", body, receive_count))

    def send_raw(self, body: str) -> None:
        """Test helper: put an arbitrary (possibly invalid) body on the queue."""
        self._enqueue(body)

    def expire_visibility(self) -> None:
        """Test helper: in-flight messages become visible again (a crash or timeout)."""
        self.pending.extend(self.in_flight.values())
        self.in_flight.clear()

    def receive(self, wait_seconds: int, max_messages: int = 1) -> list[QueueMessage]:
        out: list[QueueMessage] = []
        while self.pending and len(out) < max_messages:
            m = self.pending.pop(0)
            if m.receive_count >= self.MAX_RECEIVE_COUNT:
                self.dlq.append(m)
                continue
            got = QueueMessage(m.receipt, m.body, m.receive_count + 1)
            self.in_flight[got.receipt] = got
            out.append(got)
        return out

    def delete(self, message: QueueMessage) -> None:
        self.in_flight.pop(message.receipt, None)
        self.deleted.append(message.receipt)

    def extend_visibility(self, message: QueueMessage, seconds: int) -> None:
        self.visibility_extensions.append((message.receipt, seconds))

    def send(self, body: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("simulated SQS failure")
        self.messages.append(json.loads(encode(body)))
        self._enqueue(encode(body))


class SqsJobQueue:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @cached_property
    def _client(self) -> Any:
        import boto3

        return boto3.client(
            "sqs",
            region_name=self._settings.aws_region,
            endpoint_url=self._settings.aws_endpoint_url or None,
        )

    @cached_property
    def _url(self) -> str:
        return str(self._client.get_queue_url(QueueName=self._settings.queue_name)["QueueUrl"])

    def send(self, body: dict[str, Any]) -> None:
        self._client.send_message(QueueUrl=self._url, MessageBody=encode(body))

    def receive(self, wait_seconds: int, max_messages: int = 1) -> list[QueueMessage]:
        resp = self._client.receive_message(
            QueueUrl=self._url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
            AttributeNames=["ApproximateReceiveCount"],
        )
        return [
            QueueMessage(
                m["ReceiptHandle"],
                m["Body"],
                int(m.get("Attributes", {}).get("ApproximateReceiveCount", "1")),
            )
            for m in resp.get("Messages", [])
        ]

    def delete(self, message: QueueMessage) -> None:
        self._client.delete_message(QueueUrl=self._url, ReceiptHandle=message.receipt)

    def extend_visibility(self, message: QueueMessage, seconds: int) -> None:
        self._client.change_message_visibility(
            QueueUrl=self._url, ReceiptHandle=message.receipt, VisibilityTimeout=seconds
        )


def get_queue(request: Request) -> JobQueue:
    queue: JobQueue | None = getattr(request.app.state, "job_queue", None)
    if queue is None:
        queue = SqsJobQueue(request.app.state.settings)
        request.app.state.job_queue = queue
    return queue


QueueDep = Annotated[JobQueue, Depends(get_queue)]
