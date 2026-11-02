"""Overlap-blended sliding-window inference over a full composite GeoTIFF.

A model implements ``ProbaModel.predict_proba(window: (C, h, w) float32) -> (K, h, w)``
class probabilities; pixels it cannot classify are NaN in every channel and become nodata
(255) in the output. Overlapping windows are blended with a separable Hann weight (strictly
positive, so image borders are covered) and the argmax of the blended probabilities is the
class id (channel index == class id). Windows are streamed: only a ``size``-row band of
accumulators is held, so memory is O(K * size * width), independent of raster height.
``HardLabelAdapter`` turns a hard-label function (e.g. ``classify_array`` of the NDVI
baseline) into one-hot probabilities.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt
import rasterio
from rasterio.windows import Window

from ml.baseline_ndvi import NdviRules, classify_array
from ml.cog import ClassificationCogWriter
from ml.labels import load_classes

_CLASSES, NODATA = load_classes()
N_CLASSES = max(_CLASSES.values()) + 1


class ProbaModel(Protocol):
    """Anything that maps a (C, h, w) window to (K, h, w) class probabilities."""

    def predict_proba(self, window: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]: ...


class HardLabelAdapter:
    """One-hot ``ProbaModel`` from ``labeller((C, h, w)) -> (h, w) uint8`` (255 -> NaN)."""

    def __init__(
        self,
        labeller: Callable[[npt.NDArray[np.float32]], npt.NDArray[np.uint8]],
        n_classes: int = N_CLASSES,
    ) -> None:
        self.labeller = labeller
        self.n_classes = n_classes

    def predict_proba(self, window: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        labels = self.labeller(window)
        out: npt.NDArray[np.float32] = (
            labels[None] == np.arange(self.n_classes)[:, None, None]
        ).astype(np.float32)
        out[:, labels >= self.n_classes] = np.nan
        return out


def ndvi_baseline_model(rules: NdviRules | None = None) -> HardLabelAdapter:
    """The NDVI rule baseline as a ``ProbaModel``."""
    return HardLabelAdapter(lambda a: classify_array(a, rules))


def hann_weights(h: int, w: int) -> npt.NDArray[np.float32]:
    """Separable, strictly positive Hann window of shape (h, w)."""

    def _w(n: int) -> npt.NDArray[np.float64]:
        return 0.5 * (1.0 - np.cos(2.0 * np.pi * (np.arange(n) + 0.5) / n))

    return np.outer(_w(h), _w(w)).astype(np.float32)


def _starts(total: int, size: int, stride: int) -> list[int]:
    if total <= size:
        return [0]
    s = list(range(0, total - size, stride))
    s.append(total - size)
    return s


def _finalize(acc: npt.NDArray[np.float32], rows: int) -> npt.NDArray[np.uint8]:
    """Argmax of the first ``rows`` accumulator rows; zero weight -> nodata."""
    probs, wsum = acc[:-1, :rows], acc[-1, :rows]
    out = np.argmax(probs, axis=0).astype(np.uint8)
    out[wsum <= 0] = NODATA
    return out


def predict_cog(
    src_path: Path | str,
    dst_path: Path | str,
    model: ProbaModel,
    *,
    window_size: int = 512,
    overlap: int = 128,
    n_classes: int = N_CLASSES,
) -> Path:
    """Classify the composite at ``src_path`` and write a classification COG to ``dst_path``."""
    if not 0 <= overlap < window_size:
        raise ValueError("need 0 <= overlap < window_size")
    stride = window_size - overlap
    dst_path = Path(dst_path)
    with rasterio.open(src_path) as src:
        height, width = src.height, src.width
        wh, ww = min(window_size, height), min(window_size, width)
        weights = hann_weights(wh, ww)
        ys, xs = _starts(height, wh, stride), _starts(width, ww, stride)
        # acc rows cover [base, base + wh); channels 0..K-1 weighted probs, K = weight sum.
        acc = np.zeros((n_classes + 1, wh, width), dtype=np.float32)
        base = 0
        with ClassificationCogWriter(
            dst_path, width=width, height=height, transform=src.transform, crs=src.crs
        ) as out:
            for i, y in enumerate(ys):
                for x in xs:
                    arr = src.read(window=Window(x, y, ww, wh)).astype(np.float32)
                    p = np.asarray(model.predict_proba(arr), dtype=np.float32)
                    if p.shape != (n_classes, wh, ww):
                        raise ValueError(f"predict_proba returned {p.shape}")
                    valid = np.isfinite(p).all(axis=0)
                    w = np.where(valid, weights, 0.0).astype(np.float32)
                    r0 = y - base
                    acc[:n_classes, r0 : r0 + wh, x : x + ww] += np.where(valid, p, 0.0) * w
                    acc[n_classes, r0 : r0 + wh, x : x + ww] += w
                nxt = ys[i + 1] if i + 1 < len(ys) else height
                done = nxt - base  # rows [base, nxt) are complete
                out.write(Window(0, base, width, done), _finalize(acc, done))
                if nxt < height:
                    acc[:, : wh - done] = acc[:, done:]
                    acc[:, wh - done :] = 0
                    base = nxt
    return dst_path


def predict_cog_ndvi(src_path: Path | str, dst_path: Path | str, **kw: int) -> Path:
    """Convenience: run the NDVI baseline over ``src_path``."""
    return predict_cog(src_path, dst_path, ndvi_baseline_model(), **kw)
