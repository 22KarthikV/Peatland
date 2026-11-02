"""Streaming writer for uint8 classification COGs (contracts/cog-layout.md section 4).

Windows are written into a tiled temporary GTiff as they are produced, then
``gdal COG`` rewrites it with internal 512 px tiles, DEFLATE + PREDICTOR=2, nearest
overviews, an embedded colormap (``classes.yaml`` hex, nodata transparent) and no
wall-clock tags. Memory use is one window at a time.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import TracebackType
from typing import Any

import numpy as np
import numpy.typing as npt
import rasterio
import yaml
from rasterio.crs import CRS
from rasterio.shutil import copy as rio_copy
from rasterio.transform import Affine
from rasterio.windows import Window

from ml.labels import CLASSES_PATH

BLOCK_SIZE = 512
DEFAULT_CRS = "EPSG:32630"


def class_colormap(path: Path | str = CLASSES_PATH) -> dict[int, tuple[int, int, int, int]]:
    """Colormap (id -> RGBA) from classes.yaml; the nodata entry is transparent."""
    data = yaml.safe_load(Path(path).read_text("utf-8"))
    cmap: dict[int, tuple[int, int, int, int]] = {}
    for c in data["classes"]:
        h = str(c["hex"]).lstrip("#")
        cmap[int(c["id"])] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    cmap[int(data["nodata"])] = (0, 0, 0, 0)
    return cmap


class ClassificationCogWriter:
    """Context manager: ``write(window, array)`` repeatedly, COG is produced on clean exit."""

    def __init__(
        self,
        path: Path | str,
        *,
        width: int,
        height: int,
        transform: Affine,
        crs: CRS | str = DEFAULT_CRS,
        nodata: int = 255,
    ) -> None:
        self.path = Path(path)
        self._kw: dict[str, Any] = {
            "width": width,
            "height": height,
            "transform": transform,
            "crs": crs,
            "nodata": nodata,
        }
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._dst: Any = None
        self._src_path = Path()

    def __enter__(self) -> ClassificationCogWriter:
        self._tmp = tempfile.TemporaryDirectory()
        self._src_path = Path(self._tmp.name) / "src.tif"
        self._dst = rasterio.open(
            self._src_path,
            "w",
            driver="GTiff",
            count=1,
            dtype="uint8",
            tiled=True,
            blockxsize=256,
            blockysize=256,
            compress="deflate",
            **self._kw,
        )
        self._dst.set_band_description(1, "class")
        self._dst.write_colormap(1, class_colormap())
        return self

    def write(self, window: Window, array: npt.NDArray[np.uint8]) -> None:
        if self._dst is None:
            raise RuntimeError("writer is not open")
        self._dst.write(np.ascontiguousarray(array, dtype=np.uint8), 1, window=window)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._tmp is not None
        try:
            if self._dst is not None:
                self._dst.close()
            if exc_type is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                rio_copy(
                    self._src_path,
                    self.path,
                    driver="COG",
                    BLOCKSIZE=BLOCK_SIZE,
                    COMPRESS="DEFLATE",
                    PREDICTOR=2,
                    OVERVIEW_RESAMPLING="NEAREST",
                    BIGTIFF="IF_SAFER",
                    NUM_THREADS=1,
                )
        finally:
            self._tmp.cleanup()
