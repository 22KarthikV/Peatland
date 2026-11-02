"""Elevation ingestion: Copernicus GLO-30 DEM resampled to the target grid.

Fetches ``cop-dem-glo-30`` tiles from AWS Earth Search (same endpoint and client
injection pattern as :mod:`pipeline.stac`), mosaics them, resamples bilinearly to
the 10 m EPSG:32630 grid given as plain arguments (transform, width, height, crs),
then derives slope and the TWI proxy with :mod:`pipeline.features`.

Outputs are returned in contract order (elevation, slope, twi), matching the
feature names in contracts/bands.yaml.

Limits: LiDAR is not available over the Galloway interior
(docs/labels/candidates.md), so GLO-30 is the only source. GLO-30 is a 30 m
digital *surface* model (DSM): it includes canopy and buildings, and bilinear
resampling to 10 m adds no real detail. Slope and TWI are therefore smoothed and
biased under forest; treat them as coarse wetness context, not micro-topography.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import numpy.typing as npt
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import array_bounds
from rasterio.warp import reproject, transform_bounds

from pipeline.features import slope_degrees, twi_proxy
from pipeline.stac import EARTH_SEARCH_URL, StacClient

DEM_COLLECTION = "cop-dem-glo-30"
DEM_ASSET = "data"
# Margin (m) added around the grid when searching, so edge cells have neighbours.
SEARCH_MARGIN_M = 100.0

F32 = npt.NDArray[np.float32]


class NoDemCoverageError(RuntimeError):
    """Raised when no GLO-30 tiles cover the requested grid."""


def _dem_hrefs(bbox: tuple[float, float, float, float], client: StacClient | None) -> list[str]:
    api: Any = client
    if api is None:
        from pystac_client import Client

        api = Client.open(EARTH_SEARCH_URL)
    search = api.search(collections=[DEM_COLLECTION], bbox=list(bbox))
    hrefs = [item.assets[DEM_ASSET].href for item in search.items() if DEM_ASSET in item.assets]
    if not hrefs:
        raise NoDemCoverageError(f"no {DEM_COLLECTION} tiles cover bbox {bbox}")
    return sorted(hrefs)


def mosaic_to_grid(
    hrefs: list[str],
    transform: Affine,
    width: int,
    height: int,
    crs: CRS | str,
    *,
    opener: Callable[[str], Any] = rasterio.open,
) -> F32:
    """Bilinearly resample DEM rasters onto the grid; first valid tile wins. NaN = no data."""
    out = np.full((height, width), np.nan, dtype=np.float32)
    for href in hrefs:
        with opener(href) as src:
            tile = np.full((height, width), np.nan, dtype=np.float32)
            reproject(
                source=rasterio.band(src, 1),
                destination=tile,
                src_nodata=src.nodata,
                dst_transform=transform,
                dst_crs=crs,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
        fill = np.isnan(out) & ~np.isnan(tile)
        out[fill] = tile[fill]
    return out


def fetch_elevation(
    transform: Affine,
    width: int,
    height: int,
    crs: CRS | str,
    *,
    client: StacClient | None = None,
    opener: Callable[[str], Any] = rasterio.open,
) -> tuple[F32, F32, F32]:
    """Return (elevation, slope, twi) arrays of shape (height, width) on the grid.

    Pass ``client`` (STAC) and ``opener`` (raster open) to avoid the network.
    Cells without DEM data are NaN in all three outputs.
    """
    left, bottom, right, top = array_bounds(height, width, transform)
    m = SEARCH_MARGIN_M
    bbox = transform_bounds(crs, "EPSG:4326", left - m, bottom - m, right + m, top + m)
    hrefs = _dem_hrefs(bbox, client)
    elevation = mosaic_to_grid(hrefs, transform, width, height, crs, opener=opener)
    cell = abs(transform.a)
    return elevation, slope_degrees(elevation, cell), twi_proxy(elevation, cell)
