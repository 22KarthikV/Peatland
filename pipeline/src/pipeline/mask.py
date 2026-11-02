"""Cloud, shadow and snow mask from the Sentinel-2 L2A scene classification (SCL).

SCL classes: 0 no data, 1 saturated/defective, 2 dark area pixels (topographic
shadow), 3 cloud shadow, 4 vegetation, 5 not vegetated (bare soil), 6 water,
7 unclassified, 8 cloud medium probability, 9 cloud high probability,
10 thin cirrus, 11 snow/ice.

Default exclusions: 0, 1, 3, 8, 9, 10, 11. These either carry no usable
reflectance or are contaminated by cloud, shadow or snow. Kept: 4, 5, 6 (the
surface classes we want) and 2 (dark area pixels: common over wet, dark peat,
so dropping them would bias the composite away from the wettest ground).

Class 7 (unclassified) is NOT excluded by default but is a parameter: Sen2Cor
puts ambiguous pixels there, often thin or sub-pixel cloud, yet on bog and
heath it also catches genuine surface. Callers can add it to ``exclude`` when
a stricter mask is wanted.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import ndimage  # type: ignore[import-untyped]  # no scipy-stubs dependency yet

SCL_NO_DATA = 0
SCL_SATURATED = 1
SCL_DARK_AREA = 2
SCL_CLOUD_SHADOW = 3
SCL_VEGETATION = 4
SCL_BARE_SOIL = 5
SCL_WATER = 6
SCL_UNCLASSIFIED = 7
SCL_CLOUD_MEDIUM = 8
SCL_CLOUD_HIGH = 9
SCL_THIN_CIRRUS = 10
SCL_SNOW = 11

DEFAULT_EXCLUDE: frozenset[int] = frozenset(
    {
        SCL_NO_DATA,
        SCL_SATURATED,
        SCL_CLOUD_SHADOW,
        SCL_CLOUD_MEDIUM,
        SCL_CLOUD_HIGH,
        SCL_THIN_CIRRUS,
        SCL_SNOW,
    }
)

# Classes grown by the optional dilation (cloud edges and their shadows).
CLOUD_CLASSES: frozenset[int] = frozenset(
    {SCL_CLOUD_SHADOW, SCL_CLOUD_MEDIUM, SCL_CLOUD_HIGH, SCL_THIN_CIRRUS}
)


def valid_mask(
    scl: ArrayLike,
    exclude: Iterable[int] = DEFAULT_EXCLUDE,
    dilate_px: int = 0,
) -> NDArray[np.bool_]:
    """Return a boolean array, True where the SCL pixel is usable.

    ``exclude`` lists SCL values to mask out. ``dilate_px`` (default 0, off)
    grows the cloud/shadow/cirrus pixels (those in ``exclude`` that are also
    cloud classes) by that many pixels (8-connected) and marks them invalid,
    to catch hazy cloud edges. Values outside 0-11 are treated as invalid.
    """
    if dilate_px < 0:
        raise ValueError("dilate_px must be >= 0")
    arr = np.asarray(scl)
    if arr.ndim != 2:
        raise ValueError("scl must be a 2-D array")
    excluded = {int(c) for c in exclude}
    invalid = np.isin(arr, sorted(excluded))
    invalid |= (arr < 0) | (arr > SCL_SNOW)
    if dilate_px > 0:
        cloud = np.isin(arr, sorted(excluded & CLOUD_CLASSES))
        struct = ndimage.generate_binary_structure(2, 2)
        invalid |= ndimage.binary_dilation(cloud, structure=struct, iterations=dilate_px)
    valid: NDArray[np.bool_] = ~invalid
    return valid
