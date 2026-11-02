"""Change detection between two classification COGs on the same grid.

Streams window by window: change value is ``from * 16 + to`` (nodata 255 when either input is
nodata), a transition-count matrix is accumulated, and the change COG is written with the
colormap from contracts/cog-layout.md section 4.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import rasterio
import yaml
from rasterio.windows import Window

from ml.cog import ClassificationCogWriter, class_colormap
from ml.labels import CLASSES_PATH
from ml.stats import build_stats, stats_csv

NODATA = 255
UNCHANGED_RGBA = (0xDD, 0xDD, 0xDD, 255)
_N = 16


class GridMismatchError(ValueError):
    """The two classification rasters do not share the same grid."""


def change_colormap(
    path: Path | str = CLASSES_PATH,
) -> dict[int, tuple[int, int, int, int]]:
    """from==to -> #DDDDDD, otherwise the ``to`` class colour; 255 transparent."""
    data = yaml.safe_load(Path(path).read_text("utf-8"))
    base = class_colormap(path)
    ids = [int(c["id"]) for c in data["classes"]]
    cmap: dict[int, tuple[int, int, int, int]] = {}
    for f in ids:
        for t in ids:
            cmap[f * _N + t] = UNCHANGED_RGBA if f == t else base[t]
    cmap[int(data["nodata"])] = (0, 0, 0, 0)
    return cmap


def change_array(a: npt.NDArray[np.uint8], b: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    out = (a.astype(np.uint16) * _N + b).astype(np.uint8)
    out[(a == NODATA) | (b == NODATA)] = NODATA
    return out


class _ChangeCogWriter(ClassificationCogWriter):
    def __enter__(self) -> _ChangeCogWriter:
        super().__enter__()
        self._dst.set_band_description(1, "change")
        self._dst.write_colormap(1, change_colormap())
        return self


def _check_grid(a: Any, b: Any) -> None:
    if (
        a.width != b.width
        or a.height != b.height
        or not a.transform.almost_equals(b.transform)
        or a.crs != b.crs
    ):
        raise GridMismatchError(
            f"grid mismatch: {a.width}x{a.height} {a.crs} {tuple(a.transform)[:6]} vs "
            f"{b.width}x{b.height} {b.crs} {tuple(b.transform)[:6]}"
        )


def detect_change(
    from_path: Path | str,
    to_path: Path | str,
    out_path: Path | str,
    *,
    change: dict[str, Any] | None = None,
    window_size: int = 1024,
) -> dict[str, Any]:
    """Write the change COG and return ChangeStats (dict). Memory: one window at a time."""
    matrix = np.zeros((_N, _N), dtype=np.int64)
    with rasterio.open(from_path) as sa, rasterio.open(to_path) as sb:
        _check_grid(sa, sb)
        with _ChangeCogWriter(
            out_path,
            width=sa.width,
            height=sa.height,
            transform=sa.transform,
            crs=sa.crs,
            nodata=NODATA,
        ) as w:
            for row in range(0, sa.height, window_size):
                for col in range(0, sa.width, window_size):
                    win = Window(
                        col,
                        row,
                        min(window_size, sa.width - col),
                        min(window_size, sa.height - row),
                    )
                    out = change_array(sa.read(1, window=win), sb.read(1, window=win))
                    w.write(win, out)
                    valid = out[out != NODATA]
                    matrix += np.bincount(valid, minlength=_N * _N)[: _N * _N].reshape(_N, _N)
    return build_stats(matrix, change)


def write_stats(stats: dict[str, Any], json_path: Path | str, csv_path: Path | str) -> None:
    Path(json_path).write_text(json.dumps(stats, indent=2) + "\n", "utf-8")
    Path(csv_path).write_text(stats_csv(stats), "utf-8")
