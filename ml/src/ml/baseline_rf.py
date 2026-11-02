"""Per-pixel random-forest baseline on the raw 18-band composite.

Inputs are the same raw (un-normalised) features as ``ml.baseline_ndvi``: build the
``PatchDataset`` with ``normalize=None`` and ``nan_policy="keep"``. Training draws a seeded,
class-balanced pixel sample from the patches of a split (default ``train``), keeping only
pixels that are labelled with a class id (not 255), marked valid by the patch, and finite in
every band. Pixels with any non-finite band are nodata at prediction time.

``RandomForestBaseline`` is both an ``ml.evaluate`` predictor (``Patch -> (H, W) uint8``) and
an ``ml.infer.ProbaModel`` (``predict_proba((C, h, w)) -> (K, h, w)``, NaN where unclassifiable)
so it can run through ``predict_cog``. ``save`` / ``load_model`` round-trip via joblib.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import numpy.typing as npt
from sklearn.ensemble import RandomForestClassifier

from ml.dataset import Patch, PatchDataset
from ml.labels import load_classes
from ml.split import Split, SplitResult

_CLASSES, NODATA = load_classes()
N_CLASSES = max(_CLASSES.values()) + 1
DEFAULT_MAX_SAMPLES = 200_000


def _flatten(array: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    """(C, H, W) -> (H*W, C)."""
    return np.ascontiguousarray(array.reshape(array.shape[0], -1).T)


def sample_pixels(
    patches: Iterable[Patch],
    *,
    max_samples: int = DEFAULT_MAX_SAMPLES,
    seed: int = 0,
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.uint8]]:
    """Class-balanced, seeded pixel sample ``(X (n, C), y (n,))`` from labelled patches.

    Each class gets at most ``max_samples // n_present_classes`` pixels (fewer if the class is
    rarer); the result never exceeds ``max_samples``. Deterministic for a given seed.
    """
    xs: list[npt.NDArray[np.float32]] = []
    ys: list[npt.NDArray[np.uint8]] = []
    for p in patches:
        if p.label is None:
            raise ValueError("patch has no labels")
        x = _flatten(p.array)
        y = p.label.ravel()
        ok = p.valid.ravel() & (y < N_CLASSES) & np.isfinite(x).all(axis=1)
        if ok.any():
            xs.append(x[ok])
            ys.append(y[ok])
    if not xs:
        raise ValueError("no labelled valid pixels to sample")
    x_all, y_all = np.concatenate(xs), np.concatenate(ys)
    rng = np.random.default_rng(seed)
    present = [c for c in range(N_CLASSES) if (y_all == c).any()]
    per_class = max(1, max_samples // len(present))
    picked: list[npt.NDArray[np.intp]] = []
    for c in present:
        idx = np.flatnonzero(y_all == c)
        if len(idx) > per_class:
            idx = np.sort(rng.choice(idx, size=per_class, replace=False))
        picked.append(idx)
    sel = np.concatenate(picked)
    return x_all[sel], y_all[sel]


class RandomForestBaseline:
    """Wrapper around a fitted ``RandomForestClassifier``; predictor and ``ProbaModel``."""

    def __init__(self, model: RandomForestClassifier) -> None:
        self.model = model

    def _proba_flat(self, x: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        """(n, C) -> (n, N_CLASSES) probabilities; NaN rows where any feature is non-finite."""
        out = np.full((x.shape[0], N_CLASSES), np.nan, dtype=np.float32)
        ok = np.flatnonzero(np.isfinite(x).all(axis=1))
        if len(ok):
            cls = np.asarray(self.model.classes_, dtype=np.intp)
            block = np.zeros((len(ok), N_CLASSES), dtype=np.float32)
            block[:, cls] = self.model.predict_proba(x[ok])
            out[ok] = block
        return out

    def predict_proba(self, window: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        """``ProbaModel`` interface: (C, h, w) -> (K, h, w), NaN in every channel if unusable."""
        _, h, w = window.shape
        flat = self._proba_flat(_flatten(np.asarray(window, dtype=np.float32)))
        return np.ascontiguousarray(flat.T.reshape(N_CLASSES, h, w))

    def __call__(self, patch: Patch) -> npt.NDArray[np.uint8]:
        """``ml.evaluate`` predictor: (H, W) uint8 class ids, 255 where unclassifiable."""
        proba = self.predict_proba(patch.array)
        out = np.argmax(np.nan_to_num(proba, nan=-1.0), axis=0).astype(np.uint8)
        out[np.isnan(proba).any(axis=0)] = NODATA
        out[~patch.valid] = NODATA
        return out

    def save(self, path: Path | str) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.model, out)
        return out


def train_rf(
    ds: PatchDataset,
    split_result: SplitResult,
    *,
    split: Split = "train",
    max_samples: int = DEFAULT_MAX_SAMPLES,
    seed: int = 0,
    n_estimators: int = 100,
    max_depth: int | None = None,
    min_samples_leaf: int = 1,
    n_jobs: int = 1,
    **rf_kwargs: Any,
) -> RandomForestBaseline:
    """Fit a random forest on a class-balanced pixel sample of the ``split`` patches."""
    if ds.normalize is not None or ds.nan_policy != "keep":
        raise ValueError("RF baseline needs a dataset with normalize=None and nan_policy='keep'")
    if ds.labels is None:
        raise ValueError("dataset has no labels")
    x, y = sample_pixels(
        (ds[i] for i in split_result.indices(split)), max_samples=max_samples, seed=seed
    )
    model = RandomForestClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth,
        min_samples_leaf=min_samples_leaf,
        n_jobs=n_jobs,
        random_state=seed,
        **rf_kwargs,
    )
    model.fit(x, y)
    return RandomForestBaseline(model)


def load_model(path: Path | str) -> RandomForestBaseline:
    """Load a model saved with ``RandomForestBaseline.save``."""
    model = joblib.load(path)
    if not isinstance(model, RandomForestClassifier):
        raise TypeError(f"{path} does not contain a RandomForestClassifier")
    return RandomForestBaseline(model)
