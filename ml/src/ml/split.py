"""Spatial block split of patches into train / val / test with a buffer gap.

Method
------
1. The raster CRS plane is cut into square blocks of ``block_size`` metres (default 5 km),
   aligned to the CRS origin, so a block id does not depend on the raster extent.
2. Every block touched by a patch gets a split. Blocks that intersect a hold-out box
   (default: the Glen Trool demo burn, ADR 0003) are forced to ``test``. The remaining blocks
   are shuffled with ``seed`` and filled so the overall block counts approach ``fractions``
   (default 60/20/20).
3. A patch takes the split ``S`` of the block holding its centre and is kept only if
   (a) every block it touches has split ``S`` and (b) no block within ``buffer`` metres of it
   has a *higher-priority* split (priority test > val > train). Other patches are dropped
   (split ``None``). So the buffer strip is carved out of the lower-priority side: train
   gives way to val and test, val gives way to test, and test patches are never dropped
   for buffer reasons. This keeps small val/test blocks usable.

Guarantee: two kept patches in different splits are at least ``buffer`` apart (so with
``buffer > 0`` they neither overlap nor touch as neighbours; with ``buffer == 0`` they still
never overlap). Proof sketch: for kept patches A (split S) and B (split T), priority T > S,
B lies inside T blocks and A has no T block within ``buffer``. Because hold-out boxes lie
inside ``test`` blocks, no kept train or val patch comes within ``buffer`` of a hold-out box,
and every patch lying inside a hold-out box is kept as ``test``. The result is deterministic
for a given seed and set of patch bounds.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from rasterio.crs import CRS
from rasterio.warp import transform_bounds

from ml.dataset import PatchDataset

Split = Literal["train", "val", "test"]
SPLITS: tuple[Split, Split, Split] = ("train", "val", "test")
_PRIORITY: dict[Split, int] = {"train": 0, "val": 1, "test": 2}
Bounds = tuple[float, float, float, float]  # (left, bottom, right, top)
BlockId = tuple[int, int]  # (column, row) index on the block grid

DEFAULT_BLOCK_SIZE = 5000.0
DEFAULT_BUFFER = 1000.0
DEFAULT_FRACTIONS = (0.6, 0.2, 0.2)
# Glen Trool to Loch Doon burn, lon/lat (EPSG:4326), ADR 0003.
DEMO_BBOX_WGS84: Bounds = (-4.55, 55.05, -4.30, 55.30)


@dataclass(frozen=True)
class SplitResult:
    """Per-patch split (``None`` = dropped in the buffer) plus the block assignment."""

    patch_split: list[Split | None]
    block_split: dict[BlockId, Split]
    block_size: float
    buffer: float
    seed: int

    def indices(self, split: Split) -> list[int]:
        """Patch indices assigned to ``split``."""
        return [i for i, s in enumerate(self.patch_split) if s == split]

    @property
    def dropped(self) -> list[int]:
        """Patch indices discarded because they fall in a buffer zone."""
        return [i for i, s in enumerate(self.patch_split) if s is None]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {s: len(self.indices(s)) for s in SPLITS}
        out["dropped"] = len(self.dropped)
        return out


def dataset_patch_bounds(ds: PatchDataset) -> list[Bounds]:
    """Bounds of every patch of ``ds`` in its CRS, without reading pixels."""
    out: list[Bounds] = []
    for row, col in ds.origins:
        corners = [
            ds.transform @ (col + dc, row + dr) for dr in (0, ds.size) for dc in (0, ds.size)
        ]
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        out.append((min(xs), min(ys), max(xs), max(ys)))
    return out


def blocks_touching(bounds: Bounds, block_size: float, pad: float = 0.0) -> list[BlockId]:
    """Blocks sharing positive area with ``bounds`` grown by ``pad`` on every side.

    With ``pad > 0`` this includes every block at distance < ``pad``, including blocks that
    only touch the unpadded box along an edge.
    """
    left, bottom, right, top = bounds
    left, bottom, right, top = left - pad, bottom - pad, right + pad, top + pad
    c0 = math.floor(left / block_size)
    c1 = math.ceil(right / block_size) - 1
    r0 = math.floor(bottom / block_size)
    r1 = math.ceil(top / block_size) - 1
    return [(c, r) for c in range(c0, c1 + 1) for r in range(r0, r1 + 1)]


def block_of_point(x: float, y: float, block_size: float) -> BlockId:
    return (math.floor(x / block_size), math.floor(y / block_size))


def holdout_bounds(crs: Any, bboxes_wgs84: Iterable[Bounds]) -> list[Bounds]:
    """Reproject lon/lat hold-out boxes to ``crs`` (densified, so the result encloses them)."""
    dst = CRS.from_user_input(crs)
    return [transform_bounds("EPSG:4326", dst, *b, densify_pts=21) for b in bboxes_wgs84]


def assign_blocks(
    blocks: Iterable[BlockId],
    *,
    forced_test: Iterable[BlockId] = (),
    fractions: Sequence[float] = DEFAULT_FRACTIONS,
    seed: int = 0,
) -> dict[BlockId, Split]:
    """Assign blocks to splits; ``forced_test`` blocks always go to ``test``."""
    if len(fractions) != 3 or any(f < 0 for f in fractions) or sum(fractions) <= 0:
        raise ValueError(f"fractions must be three non-negative numbers, got {fractions!r}")
    total = float(sum(fractions))
    all_blocks = sorted(set(blocks) | set(forced_test))
    forced = sorted(set(forced_test))
    n = len(all_blocks)
    n_val = round(n * fractions[1] / total)
    n_test = round(n * fractions[2] / total)

    out: dict[BlockId, Split] = {b: "test" for b in forced}
    free = [b for b in all_blocks if b not in out]
    order = np.random.default_rng(seed).permutation(len(free))
    shuffled = [free[i] for i in order]
    n_val = min(n_val, len(shuffled))
    n_test_extra = min(max(0, n_test - len(forced)), len(shuffled) - n_val)
    for k, b in enumerate(shuffled):
        if k < n_val:
            out[b] = "val"
        elif k < n_val + n_test_extra:
            out[b] = "test"
        else:
            out[b] = "train"
    return out


def split_bounds(
    patch_bounds: Sequence[Bounds],
    crs: Any,
    *,
    block_size: float = DEFAULT_BLOCK_SIZE,
    buffer: float = DEFAULT_BUFFER,
    fractions: Sequence[float] = DEFAULT_FRACTIONS,
    seed: int = 0,
    holdout_bboxes_wgs84: Iterable[Bounds] = (DEMO_BBOX_WGS84,),
) -> SplitResult:
    """Split patches given their bounds in ``crs`` (a projected, metre-based CRS)."""
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    if buffer < 0:
        raise ValueError("buffer must be non-negative")
    if not CRS.from_user_input(crs).is_projected:
        raise ValueError("crs must be projected (metres)")

    touched: set[BlockId] = set()
    for b in patch_bounds:
        touched.update(blocks_touching(b, block_size))
    forced: set[BlockId] = set()
    for hb in holdout_bounds(crs, holdout_bboxes_wgs84):
        forced.update(blocks_touching(hb, block_size))
    block_split = assign_blocks(touched, forced_test=forced, fractions=fractions, seed=seed)

    patch_split: list[Split | None] = []
    for b in patch_bounds:
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        own = block_split[block_of_point(cx, cy, block_size)]
        inside = all(block_split[nb] == own for nb in blocks_touching(b, block_size))
        # Unassigned blocks hold no patches (every touched block is assigned), so skip them.
        near = blocks_touching(b, block_size, pad=buffer)
        clear = all(_PRIORITY[block_split.get(nb, own)] <= _PRIORITY[own] for nb in near)
        patch_split.append(own if inside and clear else None)
    return SplitResult(patch_split, block_split, block_size, buffer, seed)


def split_dataset(ds: PatchDataset, **kwargs: Any) -> SplitResult:
    """Split the patches of a ``PatchDataset`` (see ``split_bounds`` for keyword arguments)."""
    return split_bounds(dataset_patch_bounds(ds), ds.crs, **kwargs)
