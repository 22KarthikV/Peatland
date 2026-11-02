"""Structured JSON-lines run logger (written to runs/<run_id>/log.jsonl)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class RunLogger:
    """Append-only JSONL logger; each line carries run_id, timestamp and event."""

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")

    def run_start(
        self, parameters: dict[str, Any], versions: dict[str, str], scene_ids: list[str]
    ) -> None:
        """Record the full resolved parameters, versions and sorted scene ids."""
        self.event(
            "run_start",
            parameters=parameters,
            versions=versions,
            scene_ids=sorted(scene_ids),
        )
