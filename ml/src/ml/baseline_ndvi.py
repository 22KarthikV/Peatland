"""Rule-based NDVI/NDWI/NDMI/elevation baseline classifier.

Works on the raw (un-normalised) 18-band composite of ``contracts/bands.yaml``: the 12
Sentinel-2 bands, then ``ndvi``, ``ndwi``, ``ndmi``, ``elevation``, ``slope``, ``twi``.
Build the ``PatchDataset`` with ``normalize=None`` and ``nan_policy="keep"``.

Rules, applied in order (thresholds are fields of ``NdviRules``):

1. Pixel is nodata (255) if ndvi, ndwi, ndmi or elevation is not finite, or the patch marks
   it invalid.
2. ``ndwi > water_ndwi_min``: open water, class ``other`` (0).
3. ``ndvi < bare_ndvi_max``: sparse or no vegetation, ``felled_or_burnt_bare`` (3).
4. ``ndvi >= woodland_ndvi_min``: dense canopy, ``woodland`` (2).
5. Mid NDVI, wet (``ndmi >= bog_ndmi_min``) and ``elevation <= bog_max_elevation_m``:
   ``bog`` (1).
6. Everything else: ``other`` (0).

The defaults are plausible starting values for upland Scotland, not tuned; tune them on the
train split if needed. Class ids are read from ``contracts/classes.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from ml.dataset import Patch
from ml.labels import load_classes

# Band indices in the 18-band composite (contracts/bands.yaml).
NDVI_BAND = 12
NDWI_BAND = 13
NDMI_BAND = 14
ELEVATION_BAND = 15

_CLASSES, NODATA = load_classes()
OTHER = _CLASSES["other"]
BOG = _CLASSES["bog"]
WOODLAND = _CLASSES["woodland"]
BARE = _CLASSES["felled_or_burnt_bare"]


@dataclass(frozen=True)
class NdviRules:
    """Thresholds for ``classify_array`` (see module docstring)."""

    water_ndwi_min: float = 0.3
    bare_ndvi_max: float = 0.25
    woodland_ndvi_min: float = 0.6
    bog_ndmi_min: float = 0.0
    bog_max_elevation_m: float = 600.0


def classify_array(
    array: npt.NDArray[np.float32], rules: NdviRules | None = None
) -> npt.NDArray[np.uint8]:
    """Classify a (C>=16, H, W) raw composite array into (H, W) uint8 class ids."""
    r = rules or NdviRules()
    if array.ndim != 3 or array.shape[0] <= ELEVATION_BAND:
        raise ValueError(f"expected (C>={ELEVATION_BAND + 1}, H, W) array, got {array.shape}")
    ndvi = array[NDVI_BAND]
    ndwi = array[NDWI_BAND]
    ndmi = array[NDMI_BAND]
    elev = array[ELEVATION_BAND]
    finite = np.isfinite(ndvi) & np.isfinite(ndwi) & np.isfinite(ndmi) & np.isfinite(elev)

    out = np.full(ndvi.shape, OTHER, dtype=np.uint8)
    out[(ndmi >= r.bog_ndmi_min) & (elev <= r.bog_max_elevation_m)] = BOG
    out[ndvi >= r.woodland_ndvi_min] = WOODLAND
    out[ndvi < r.bare_ndvi_max] = BARE
    out[ndwi > r.water_ndwi_min] = OTHER
    out[~finite] = NODATA
    return out


class NdviBaseline:
    """Predictor (``Patch -> (H, W) uint8``) wrapping ``classify_array``."""

    def __init__(self, rules: NdviRules | None = None) -> None:
        self.rules = rules or NdviRules()

    def __call__(self, patch: Patch) -> npt.NDArray[np.uint8]:
        pred = classify_array(patch.array, self.rules)
        pred[~patch.valid] = NODATA
        return pred
