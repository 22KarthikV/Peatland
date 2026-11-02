"""Tile a composite COG into fixed-size patches.

Composite layout (contracts/cog-layout.md): 18-band float32, EPSG:32630, 10 m, nodata NaN.

NaN handling
------------
Cloud-masked or outside-footprint pixels are NaN in the file. ``PatchDataset`` offers:

* ``nan_policy="keep"``: arrays are returned as read (NaN preserved).
* ``nan_policy="zero"``: NaN (and +-inf) replaced by ``fill_value`` (default 0.0) via
  ``nan_to_num``, applied *after* normalisation so 0.0 means "band mean" for standardised data.

Independently, every patch carries ``valid`` (H, W bool): True where all bands in
``valid_bands`` (default: all) are finite in the *raw* data and the pixel is not padding.
It is computed before normalisation and fill, so it is identical for both policies.

Edge handling (``edge``), for patches that would cross the raster edge
----------------------------------------------------------------------
* ``"drop"``: skip them (only full patches).
* ``"pad"``: keep them, padded to full size with NaN (then ``fill_value`` under ``zero``);
  padded pixels are invalid and labels pad with ``label_nodata``.
* ``"shift"``: slide the last row/column of patches back so they end on the raster edge
  (overlapping the previous patch). If the raster is smaller than one patch on an axis,
  that axis falls back to ``"pad"``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import numpy.typing as npt
import rasterio
from rasterio.transform import Affine
from rasterio.windows import Window
from rasterio.windows import transform as window_transform

EdgeMode = Literal["drop", "pad", "shift"]
NanPolicy = Literal["keep", "zero"]
Normalizer = Callable[[npt.NDArray[np.float32]], npt.NDArray[np.float32]]

LABEL_NODATA = 255


@dataclass(frozen=True)
class Patch:
    """One patch: ``array`` (C, H, W) float32, ``valid`` (H, W) bool, georeferencing."""

    index: int
    row_off: int
    col_off: int
    array: npt.NDArray[np.float32]
    valid: npt.NDArray[np.bool_]
    transform: Affine
    bounds: tuple[float, float, float, float]  # (left, bottom, right, top) in the raster CRS
    label: npt.NDArray[np.uint8] | None = None


class BandNormalizer:
    """Per-band standardisation ``(x - mean) / std``; NaN passes through unchanged."""

    def __init__(self, mean: Sequence[float], std: Sequence[float]) -> None:
        self.mean = np.asarray(mean, dtype=np.float32).reshape(-1, 1, 1)
        std_arr = np.asarray(std, dtype=np.float32).reshape(-1, 1, 1)
        self.std = np.where(std_arr > 0, std_arr, 1.0).astype(np.float32)

    def __call__(self, x: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        return ((x - self.mean) / self.std).astype(np.float32)

    @classmethod
    def fit(cls, arrays: Sequence[npt.NDArray[np.float32]]) -> BandNormalizer:
        """Fit nan-aware per-band mean/std from (C, H, W) arrays."""
        stack = np.concatenate([a.reshape(a.shape[0], -1) for a in arrays], axis=1)
        mean = np.nan_to_num(np.nanmean(stack, axis=1)).tolist()
        std = np.nan_to_num(np.nanstd(stack, axis=1)).tolist()
        return cls(mean, std)


def patch_origins(
    height: int, width: int, size: int = 256, stride: int | None = None, edge: EdgeMode = "drop"
) -> list[tuple[int, int]]:
    """Top-left ``(row, col)`` of every patch for the given grid size and edge mode."""
    stride = size if stride is None else stride
    if size <= 0 or stride <= 0:
        raise ValueError("size and stride must be positive")
    if edge not in ("drop", "pad", "shift"):
        raise ValueError(f"unknown edge mode {edge!r}")

    def axis(n: int) -> list[int]:
        full = list(range(0, n - size + 1, stride)) if n >= size else []
        if edge == "drop":
            return full
        if full and full[-1] + size >= n:
            return full
        if edge == "shift" and n >= size:
            return [*full, n - size]
        start = (full[-1] + stride) if full else 0
        extra = []
        while start < n:
            extra.append(start)
            start += stride
        return [*full, *extra]

    return [(r, c) for r in axis(height) for c in axis(width)]


class PatchDataset:
    """Sequence of patches over one composite COG (opens the file per access, picklable)."""

    def __init__(
        self,
        path: Path | str,
        *,
        size: int = 256,
        stride: int | None = None,
        edge: EdgeMode = "drop",
        nan_policy: NanPolicy = "keep",
        fill_value: float = 0.0,
        valid_bands: Sequence[int] | None = None,
        normalize: Normalizer | None = None,
        labels: npt.NDArray[np.uint8] | None = None,
        label_nodata: int = LABEL_NODATA,
    ) -> None:
        if nan_policy not in ("keep", "zero"):
            raise ValueError(f"unknown nan_policy {nan_policy!r}")
        self.path = Path(path)
        self.size = size
        self.edge: EdgeMode = edge
        self.nan_policy: NanPolicy = nan_policy
        self.fill_value = fill_value
        self.valid_bands = None if valid_bands is None else list(valid_bands)
        self.normalize = normalize
        self.labels = labels
        self.label_nodata = label_nodata
        with rasterio.open(self.path) as src:
            self.height, self.width, self.count = src.height, src.width, src.count
            self.crs = src.crs
            self.transform: Affine = src.transform
        if labels is not None and labels.shape != (self.height, self.width):
            raise ValueError(f"labels shape {labels.shape} != raster {(self.height, self.width)}")
        self.origins = patch_origins(self.height, self.width, size, stride, edge)

    def __len__(self) -> int:
        return len(self.origins)

    def __iter__(self) -> Iterator[Patch]:
        for i in range(len(self)):
            yield self[i]

    def __getitem__(self, i: int) -> Patch:
        if i < 0:
            i += len(self)
        if not 0 <= i < len(self):
            raise IndexError(i)
        row, col = self.origins[i]
        h = min(self.size, self.height - row)
        w = min(self.size, self.width - col)
        with rasterio.open(self.path) as src:
            data = src.read(window=Window(col, row, w, h)).astype(np.float32, copy=False)

        array = np.full((self.count, self.size, self.size), np.nan, dtype=np.float32)
        array[:, :h, :w] = data
        in_raster = np.zeros((self.size, self.size), dtype=bool)
        in_raster[:h, :w] = True
        bands = array if self.valid_bands is None else array[self.valid_bands]
        valid = in_raster & np.isfinite(bands).all(axis=0)

        if self.normalize is not None:
            array = self.normalize(array)
        if self.nan_policy == "zero":
            array = np.nan_to_num(
                array, nan=self.fill_value, posinf=self.fill_value, neginf=self.fill_value
            )

        label = None
        if self.labels is not None:
            label = np.full((self.size, self.size), self.label_nodata, dtype=np.uint8)
            label[:h, :w] = self.labels[row : row + h, col : col + w]

        tf: Affine = window_transform(Window(col, row, self.size, self.size), self.transform)
        left, top = tf @ (0, 0)
        right, bottom = tf @ (self.size, self.size)
        return Patch(
            index=i,
            row_off=row,
            col_off=col,
            array=array,
            valid=valid,
            transform=tf,
            bounds=(left, bottom, right, top),
            label=label,
        )
