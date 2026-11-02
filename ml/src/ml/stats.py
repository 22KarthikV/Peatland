"""Transition-matrix statistics (JSON per ``ChangeStats`` in openapi.yaml, and CSV)."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import yaml

from ml.labels import CLASSES_PATH

PIXEL_AREA_HA = 0.01  # 10 m x 10 m


def load_classes(path: Path | str = CLASSES_PATH) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text("utf-8"))
    return [
        {"id": int(c["id"]), "name": c["name"], "label": c["label"], "hex": c["hex"]}
        for c in data["classes"]
    ]


def _pct(count: int, total: int) -> float:
    return round(100.0 * count / total, 2) if total else 0.0


def build_stats(
    matrix: npt.NDArray[np.int64],
    change: dict[str, Any] | None = None,
    classes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """ChangeStats dict from a ``[from, to]`` pixel-count matrix (nodata excluded).

    ``change`` is the API ``Change`` object (caller supplies it; the ml module only knows pixels).
    """
    classes = classes if classes is not None else load_classes()
    total = int(matrix.sum())
    transitions = [
        {
            "from_class": dict(f),
            "to_class": dict(t),
            "area_ha": round(int(matrix[f["id"], t["id"]]) * PIXEL_AREA_HA, 2),
            "percent": _pct(int(matrix[f["id"], t["id"]]), total),
        }
        for f in classes
        for t in classes
        if matrix[f["id"], t["id"]] > 0
    ]

    def areas(counts: npt.NDArray[np.int64]) -> list[dict[str, Any]]:
        return [
            {
                "class": dict(c),
                "area_ha": round(int(counts[c["id"]]) * PIXEL_AREA_HA, 2),
                "percent": _pct(int(counts[c["id"]]), total),
            }
            for c in classes
        ]

    return {
        "change": change if change is not None else {},
        "units": "hectares",
        "pixel_area_ha": PIXEL_AREA_HA,
        "total_ha": round(total * PIXEL_AREA_HA, 2),
        "transitions": transitions,
        "from_class_areas": areas(matrix.sum(axis=1)),
        "to_class_areas": areas(matrix.sum(axis=0)),
    }


def stats_csv(stats: dict[str, Any]) -> str:
    """CSV matching contracts/fixtures/changes.stats.200.csv."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["from_class_id", "from_class", "to_class_id", "to_class", "area_ha", "percent"])
    for t in stats["transitions"]:
        f, to = t["from_class"], t["to_class"]
        w.writerow(
            [f["id"], f["name"], to["id"], to["name"], f"{t['area_ha']:.2f}", f"{t['percent']:.2f}"]
        )
    return buf.getvalue()
