"""Step types shared by the worker and the step implementations.

They live outside `api.worker` on purpose: the worker runs as `python -m api.worker`, so that
module is `__main__` and a class defined there is a different class from `api.worker.StepError`.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


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
