"""Model-agnostic evaluation harness on the spatial-split test blocks.

Predictor interface (the only thing a model must provide)::

    predictor(patch: ml.dataset.Patch) -> np.ndarray  # (H, W) integer class ids, 255 = nodata

``patch`` comes from the ``PatchDataset`` passed to ``evaluate``, so the dataset's
normalisation / NaN policy is whatever that model needs. Pixels count only where the truth
label is a class id, the patch's ``valid`` mask is True and the prediction is not 255
(``segmentation_metrics`` already ignores nodata in either array). Patches are those of
``split_result.indices(split)`` (default ``"test"``); the dataset must carry labels.

``write_metrics_json`` serialises with NaN -> null (strict JSON).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from ml.dataset import Patch, PatchDataset
from ml.labels import load_classes
from ml.metrics import LABEL_NODATA, SegmentationMetrics, segmentation_metrics
from ml.split import Split, SplitResult

Predictor = Callable[[Patch], npt.NDArray[np.integer[Any]]]


def _num(x: float) -> float | None:
    return None if math.isnan(x) else float(x)


def metrics_to_dict(m: SegmentationMetrics) -> dict[str, Any]:
    """JSON-ready dict of ``m`` with NaN replaced by ``None``; includes class names."""
    names = [n for n, _ in sorted(load_classes()[0].items(), key=lambda kv: kv[1])]
    return {
        "classes": names,
        "confusion": m.confusion.tolist(),
        "iou": [_num(float(v)) for v in m.iou],
        "f1": [_num(float(v)) for v in m.f1],
        "mean_iou": _num(m.mean_iou),
        "mean_f1": _num(m.mean_f1),
    }


def evaluate(
    predictor: Predictor,
    ds: PatchDataset,
    split_result: SplitResult,
    *,
    split: Split = "test",
) -> SegmentationMetrics:
    """Run ``predictor`` over the ``split`` patches of ``ds`` and return pooled metrics."""
    trues: list[npt.NDArray[np.uint8]] = []
    preds: list[npt.NDArray[np.int64]] = []
    for i in split_result.indices(split):
        patch = ds[i]
        if patch.label is None:
            raise ValueError("dataset has no labels")
        raw = np.asarray(predictor(patch))
        if raw.shape != patch.label.shape:
            raise ValueError(f"prediction shape {raw.shape} != label {patch.label.shape}")
        if not np.issubdtype(raw.dtype, np.integer):
            raise TypeError(f"prediction must be an integer array, got {raw.dtype}")
        pred = raw.astype(np.int64)
        pred[~patch.valid] = LABEL_NODATA
        trues.append(patch.label.ravel())
        preds.append(pred.ravel())
    if not trues:
        empty = np.empty(0, dtype=np.uint8)
        return segmentation_metrics(empty, empty)
    return segmentation_metrics(np.concatenate(trues), np.concatenate(preds))


def write_metrics_json(
    path: Path | str, metrics: SegmentationMetrics, extra: Mapping[str, Any] | None = None
) -> Path:
    """Write ``metrics_to_dict(metrics)`` (plus ``extra`` top-level keys) as strict JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {**(extra or {}), **metrics_to_dict(metrics)}
    out.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", "utf-8")
    return out
