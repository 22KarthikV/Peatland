"""Job queue access behind a small interface (contracts/queue.md).

`SqsJobQueue` is the real boto3 implementation; `InMemoryJobQueue` is the test fake. Routes get
the queue through the `get_queue` dependency, which tests override.
"""

import json
from functools import cached_property
from typing import Annotated, Any, Protocol

from fastapi import Depends, Request

from api.settings import Settings


class JobQueue(Protocol):
    def send(self, body: dict[str, Any]) -> None:
        """Send one job message; raise on failure."""
        ...


def encode(body: dict[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class InMemoryJobQueue:
    """Test double: records bodies; set `fail` to simulate a send error."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.fail = False

    def send(self, body: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("simulated SQS failure")
        self.messages.append(json.loads(encode(body)))


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


def get_queue(request: Request) -> JobQueue:
    queue: JobQueue | None = getattr(request.app.state, "job_queue", None)
    if queue is None:
        queue = SqsJobQueue(request.app.state.settings)
        request.app.state.job_queue = queue
    return queue


QueueDep = Annotated[JobQueue, Depends(get_queue)]
