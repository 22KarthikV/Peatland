"""Spectral indices and terrain features (pure float32 numpy, NaN-safe).

Formulas follow contracts/bands.yaml. All functions return float32 arrays,
NaN where an input is NaN or the denominator is 0, and never emit numpy
warnings.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

F32 = NDArray[np.float32]

# Slope floor (degrees) so tan(slope) never reaches 0 on flat peat.
MIN_SLOPE_DEG = 0.1


def _f32(a: ArrayLike) -> F32:
    return np.asarray(a, dtype=np.float32)


def normalized_difference(a: ArrayLike, b: ArrayLike) -> F32:
    """(a - b) / (a + b); NaN where a + b == 0 or any input is NaN."""
    x, y = _f32(a), _f32(b)
    num = x - y
    den = x + y
    out = np.full(np.broadcast(x, y).shape, np.nan, dtype=np.float32)
    ok = (den != 0) & np.isfinite(den) & np.isfinite(num)
    np.divide(num, den, out=out, where=ok)
    return out


def ndvi(b08: ArrayLike, b04: ArrayLike) -> F32:
    """(B08 - B04) / (B08 + B04)."""
    return normalized_difference(b08, b04)


def ndwi(b03: ArrayLike, b08: ArrayLike) -> F32:
    """(B03 - B08) / (B03 + B08)."""
    return normalized_difference(b03, b08)


def ndmi(b08: ArrayLike, b11: ArrayLike) -> F32:
    """(B08 - B11) / (B08 + B11)."""
    return normalized_difference(b08, b11)


def slope_degrees(elevation: ArrayLike, cell_size: float) -> F32:
    """Slope in degrees (0-90) from central differences (np.gradient).

    Edges use one-sided differences. NaN neighbours propagate to NaN.
    """
    if cell_size <= 0:
        raise ValueError("cell_size must be > 0")
    z = _f32(elevation)
    if z.ndim != 2:
        raise ValueError("elevation must be 2-D")
    if min(z.shape) < 2:
        return np.full(z.shape, np.nan, dtype=np.float32)
    dzdy, dzdx = np.gradient(z.astype(np.float64), cell_size)
    deg: F32 = np.degrees(np.arctan(np.hypot(dzdx, dzdy))).astype(np.float32)
    return deg


def twi_proxy(elevation: ArrayLike, cell_size: float) -> F32:
    """Topographic wetness proxy, ln(A / tan(slope)), without flow accumulation.

    A (specific catchment area proxy, m) = cell_size * (1 + d), where d is the
    local depression depth in metres: max(0, mean of the 8 neighbours - z).
    Slope is floored at MIN_SLOPE_DEG so flat cells stay finite.

    Limits: no true upslope routing, so large valley-bottom catchments are
    underestimated; values only rank local wetness (hollows and flats high,
    steep convex slopes low) and are not comparable to flow-routed TWI.
    Raster edges replicate the border; NaN anywhere in a 3x3 window gives NaN.
    """
    z = _f32(elevation)
    slope = slope_degrees(z, cell_size)
    pad = np.pad(z.astype(np.float64), 1, mode="edge")
    h, w = z.shape
    total = np.zeros((h, w), dtype=np.float64)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy == 1 and dx == 1:
                continue
            total += pad[dy : dy + h, dx : dx + w]
    depth = np.maximum(total / 8.0 - z, 0.0)
    area = cell_size * (1.0 + depth)
    tan_s = np.tan(np.radians(np.maximum(slope, MIN_SLOPE_DEG)))
    out = np.log(area / tan_s).astype(np.float32)
    out[~np.isfinite(slope)] = np.nan
    return out
