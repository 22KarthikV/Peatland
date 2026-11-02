"""Per-class segmentation metrics over uint8 class-id arrays.

Class ids follow ``contracts/classes.yaml`` (0..3 for the current list). Nodata (255) is
ignored: a pixel contributes only when *both* truth and prediction are valid class ids.

Definitions (``tp``, ``fp``, ``fn`` are per class, pixel counts):

* ``iou = tp / (tp + fp + fn)``
* ``f1 = 2 tp / (2 tp + fp + fn)``
* ``mean_iou`` / ``mean_f1`` average over classes that *occur* (in truth or prediction).

A class absent from both truth and prediction has an undefined score: its entry is NaN and
it is excluded from the mean (``np.nanmean``). A class present in truth but never predicted
scores 0 and is included. If no class occurs at all, the means are NaN.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

LABEL_NODATA = 255
NUM_CLASSES = 4


@dataclass(frozen=True)
class SegmentationMetrics:
    """Metrics for one truth/prediction pair.

    ``confusion[t, p]`` counts valid pixels with truth ``t`` and prediction ``p``.
    ``iou`` and ``f1`` have one entry per class id (NaN where the class does not occur).
    """

    confusion: npt.NDArray[np.int64]
    iou: npt.NDArray[np.float64]
    f1: npt.NDArray[np.float64]
    mean_iou: float
    mean_f1: float


def _valid_pairs(
    y_true: npt.ArrayLike, y_pred: npt.ArrayLike, nodata: int
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    t = np.asarray(y_true)
    p = np.asarray(y_pred)
    for name, arr in (("y_true", t), ("y_pred", p)):
        if not np.issubdtype(arr.dtype, np.integer):
            raise TypeError(f"{name} must be an integer array, got {arr.dtype}")
    if t.shape != p.shape:
        raise ValueError(f"shape mismatch: y_true {t.shape} vs y_pred {p.shape}")
    keep = (t != nodata) & (p != nodata)
    return t[keep].astype(np.int64), p[keep].astype(np.int64)


def confusion_matrix(
    y_true: npt.ArrayLike,
    y_pred: npt.ArrayLike,
    *,
    num_classes: int = NUM_CLASSES,
    nodata: int = LABEL_NODATA,
) -> npt.NDArray[np.int64]:
    """Return a ``(num_classes, num_classes)`` count matrix; rows are truth, columns prediction.

    Pixels where either array equals ``nodata`` are dropped. Any other value outside
    ``0..num_classes-1`` raises ``ValueError``.
    """
    if num_classes <= 0:
        raise ValueError("num_classes must be positive")
    t, p = _valid_pairs(y_true, y_pred, nodata)
    if t.size:
        lo = min(int(t.min()), int(p.min()))
        hi = max(int(t.max()), int(p.max()))
        if lo < 0 or hi >= num_classes:
            raise ValueError(f"class ids must be in 0..{num_classes - 1} (or nodata {nodata})")
    flat = t * num_classes + p
    counts = np.bincount(flat, minlength=num_classes * num_classes)
    return counts.reshape(num_classes, num_classes).astype(np.int64)


def _safe_ratio(
    num: npt.NDArray[np.float64], den: npt.NDArray[np.float64]
) -> npt.NDArray[np.float64]:
    out = np.full(num.shape, np.nan, dtype=np.float64)
    np.divide(num, den, out=out, where=den > 0)
    return out


def _nanmean(values: npt.NDArray[np.float64]) -> float:
    if np.isnan(values).all():
        return float("nan")
    return float(np.nanmean(values))


def segmentation_metrics(
    y_true: npt.ArrayLike,
    y_pred: npt.ArrayLike,
    *,
    num_classes: int = NUM_CLASSES,
    nodata: int = LABEL_NODATA,
) -> SegmentationMetrics:
    """Per-class IoU and F1, their means, and the confusion matrix (nodata ignored)."""
    cm = confusion_matrix(y_true, y_pred, num_classes=num_classes, nodata=nodata)
    tp = np.diag(cm).astype(np.float64)
    fp = cm.sum(axis=0).astype(np.float64) - tp
    fn = cm.sum(axis=1).astype(np.float64) - tp
    iou = _safe_ratio(tp, tp + fp + fn)
    f1 = _safe_ratio(2.0 * tp, 2.0 * tp + fp + fn)
    return SegmentationMetrics(
        confusion=cm,
        iou=iou,
        f1=f1,
        mean_iou=_nanmean(iou),
        mean_f1=_nanmean(f1),
    )
