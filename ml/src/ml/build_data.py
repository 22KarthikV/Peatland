"""Build the Galloway training data: composite COGs, weak-label rasters and a manifest (M9 prep).

Run through the thin wrapper ``scripts/build_training_data.py`` (see ``--help``). Outputs, all
under ``--data-dir`` (default ``data/``, git-ignored)::

    composites/<region>__<YYYYMMDD>-<YYYYMMDD>.tif   18-band composite COG (+ .json sidecar)
    labels/<region>.tif                              uint8 class ids on exactly the composite grid
    raw/                                             cached source downloads
    pipeline/                                        boundary GeoJSON + pipeline run configs
    MANIFEST.json                                    sources, licences, fetch date, checksums

Weak labels (``docs/labels/training-labels.md`` has the reasoning)
------------------------------------------------------------------
Two open vector sources are rasterised to the composite grid and combined:

* NFI Scotland 2025 (Forestry Commission, OGL) attribute ``IFT_IOA``: conifer / broadleaved /
  mixed -> ``woodland``; felled / ground prep / windblow -> ``felled_or_burnt_bare``; young
  trees, assumed woodland, low density, shrub, failed -> unlabelled (uncertain canopy).
* NatureScot Carbon and Peatland Map 2016 (CPM2016), attribute ``importance`` (class 1-5, 0
  mineral, -1 unknown, -2 non-soil): class 1-2 -> bog candidate, class 5 and -1 ->
  unlabelled, the rest -> other.

NFI woodland and felled win over CPM (the CPM vegetation layer is from 1988). Where NFI says
"grassland" or "bare area" on CPM bog, or CPM is ambiguous, the pixel is 255. The Glen Trool demo
box plus a buffer is forced to 255, so nothing there can leak into training.

Circularity: parts of the NFI are derived from Sentinel-2 imagery, the same sensor the model
sees. Agreement with these labels overstates skill; judge the model on the hand labels (L3).

The functions that touch the network take an injectable ``get`` / ``opener`` so tests never do.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import rasterio
from rasterio.transform import Affine
from shapely.geometry import box

from ml.labels import load_classes, rasterize_labels

Bbox = tuple[float, float, float, float]  # (west, south, east, north), WGS84
NODATA = 255
TARGET_CRS = "EPSG:32630"

# Region choice (see tasks/handoffs/M9prep.md): both lie outside the burn box by >= 2 km.
HOLDOUT_BBOX_WGS84: Bbox = (-4.55, 55.05, -4.30, 55.30)  # Glen Trool to Loch Doon burn (ADR 0003)
HOLDOUT_BUFFER_M = 1000.0
REGIONS: dict[str, Bbox] = {
    # Cairnsmore of Fleet, Cree valley and Clatteringshaws: blanket bog, plantations, felling.
    "galloway-south": (-4.52, 54.90, -4.20, 55.03),
    # Carrick / Arecleoch forests west of the burn: plantations on deep peat, restock, bog.
    "galloway-west": (-4.80, 55.05, -4.60, 55.25),
}
DEFAULT_REGIONS = ("galloway-south", "galloway-west")  # bog is only 3-4 % per region: use both
DEFAULT_WINDOW = (date(2025, 4, 1), date(2025, 5, 31))  # spring, matches the demo-date phenology
DEFAULT_CLOUD_THRESHOLD = 30.0

# ------------------------------------------------------------------------------- sources

USER_AGENT = "Mozilla/5.0 (compatible; peatwatch-build-data/1.0; +https://github.com/22KarthikV)"
NFI_LAYER_URL = (
    "https://services2.arcgis.com/mHXjwgl3OARRqqD4/arcgis/rest/services/"
    "National_Forest_Inventory_Scotland_2025/FeatureServer/0"
)
NFI_HUB_PAGE = (
    "https://data-forestry.opendata.arcgis.com/datasets/de94c77f724345e69283238f75c69848/about"
)
NFI_CLASS_COLUMN = "IFT_IOA"
CPM_WFS_URL = "https://ogc.nature.scot/geoserver/renewables/ows"
CPM_WFS_LAYER = "renewables:carbonpeatlandmap2016"
CPM_ZIP_URL = "https://gis-downloads.nature.scot/CPM2016_SCOTLAND_SHP_27700.zip"
CPM_CLASS_COLUMN = "importance"

SOURCE_INFO: dict[str, dict[str, str]] = {
    "nfi": {
        "name": "National Forest Inventory Scotland 2025 (woodland map)",
        "publisher": "Forestry Commission",
        "licence": "Open Government Licence v3.0 (no public access constraints)",
        "attribution": "(c) Forestry Commission copyright. Contains Ordnance Survey data "
        "(c) Crown copyright and database right 2026.",
        "url": NFI_HUB_PAGE,
        "service": NFI_LAYER_URL,
        "circularity": "Canopy changes were identified partly on Sentinel-2 imagery (spring/"
        "summer 2025) or aerial photos, so labels share a sensor with the model inputs.",
    },
    "cpm2016": {
        "name": "Carbon and Peatland 2016 map (CPM2016)",
        "publisher": "NatureScot",
        "licence": "Open Government Licence v3.0 (NatureScot open data; the catalogue licence "
        "field is empty, see docs/labels/datasets.md section 1)",
        "attribution": "Contains NatureScot information licensed under the Open Government "
        "Licence v3.0.",
        "url": CPM_ZIP_URL,
        "service": f"{CPM_WFS_URL} layer {CPM_WFS_LAYER}",
        "circularity": "None (built from 1:25k/1:250k soil maps and 1988 land cover); coarse "
        "and dated, so only a weak prior for bog.",
    },
}
EXCLUDED_SOURCES = [
    "UKCEH Land Cover Map (bespoke licence and Sentinel-2 circularity): not used.",
    "NatureScot Muirburn Peatland Map 2025 (UK Non-Commercial Government Licence): not used.",
]
CAVEATS = [
    "Weak labels: NFI is partly derived from Sentinel-2 and CPM2016 is a 2016 soil/1988 vegetation "
    "prior, so scores on these labels overstate skill. Evaluate on the independent hand labels.",
    "Pixels inside the Glen Trool demo box plus a 1 km buffer are 255 (never trained on).",
    "Composite window and NFI vintage differ: NFI 2025 covers 2024-04-01 to 2025-03-31.",
]

# NFI IFT_IOA groups (matched case-insensitively). Unknown values count as uncertain.
NFI_WOODLAND = {
    "conifer",
    "broadleaved",
    "mixed mainly conifer",
    "mixed mainly broadleaved",
    "coppice",
    "coppice with standards",
}
NFI_FELLED = {"felled", "ground prep", "windblow"}
NFI_UNCERTAIN = {"young trees", "assumed woodland", "low density", "shrub", "failed"}
NFI_HARD_OTHER = {
    "open water",
    "river",
    "urban",
    "road",
    "quarry",
    "windfarm",
    "power line",
    "agriculture land",
}
NFI_SOFT_OTHER = {"grassland", "bare area"}
NFI_NEUTRAL = {"other vegetation"}

# Intermediate raster codes (255 = no polygon).
NFI_CODES = {"woodland": 1, "felled": 2, "uncertain": 3, "hard_other": 4, "soft_other": 5}
NFI_NEUTRAL_CODE = 6
CPM_CODES = {"bog": 1, "other": 2, "ambiguous": 3}
# Painting order for overlaps (later wins): uncertain last, so overlaps become unlabelled.
_NFI_ORDER = ("neutral", "soft_other", "hard_other", "woodland", "felled", "uncertain")


class DataFetchError(RuntimeError):
    """A source could not be fetched or read; the message says what to do by hand."""


# --------------------------------------------------------------------------- region maths


def bbox_polygon_utm(bbox: Bbox) -> Any:
    """The WGS84 box as a shapely polygon in EPSG:32630 (vertices reprojected)."""
    gs = gpd.GeoSeries([box(*bbox)], crs="EPSG:4326").to_crs(TARGET_CRS)
    return gs.iloc[0]


def check_outside_holdout(
    bbox: Bbox,
    holdout: Bbox = HOLDOUT_BBOX_WGS84,
    min_distance_m: float = HOLDOUT_BUFFER_M,
) -> float:
    """Distance in metres from ``bbox`` to the hold-out box; raise if below ``min_distance_m``."""
    w, s, e, n = bbox
    if not (w < e and s < n):
        raise ValueError(f"bad bbox {bbox}: need west < east and south < north")
    dist = float(bbox_polygon_utm(bbox).distance(bbox_polygon_utm(holdout)))
    if dist < min_distance_m:
        raise ValueError(
            f"bbox {bbox} is {dist:.0f} m from the hold-out box {holdout}; "
            f"need at least {min_distance_m:.0f} m"
        )
    return dist


def boundary_geojson(bbox: Bbox) -> dict[str, Any]:
    """GeoJSON Feature (EPSG:4326 polygon) used as the pipeline site boundary."""
    w, s, e, n = bbox
    ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


# -------------------------------------------------------------------------------- fetching

Getter = Callable[[str], bytes]
Opener = Callable[..., Any]


def http_get(url: str, *, timeout: float = 180.0, opener: Opener | None = None) -> bytes:
    """GET ``url`` with a browser-like user agent. Raises ``DataFetchError`` on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            data: bytes = resp.read()
            return data
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DataFetchError(f"GET failed for {url[:200]}: {exc}") from exc


def download_file(
    url: str,
    dest: Path,
    *,
    refresh: bool = False,
    opener: Opener | None = None,
    chunk: int = 1 << 20,
) -> Path:
    """Stream ``url`` to ``dest`` (via ``.part``). A non-empty existing file is a cache hit."""
    if dest.is_file() and dest.stat().st_size > 0 and not refresh:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=60) as resp, part.open("wb") as fh:
            total = 0
            while block := resp.read(chunk):
                fh.write(block)
                total += len(block)
        if total == 0:
            raise DataFetchError(f"empty response from {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        part.unlink(missing_ok=True)
        raise DataFetchError(f"download failed for {url}: {exc}") from exc
    part.replace(dest)
    return dest


def _json(data: bytes, what: str) -> dict[str, Any]:
    try:
        obj = json.loads(data)
    except ValueError as exc:
        raise DataFetchError(f"{what}: response is not JSON ({data[:120]!r})") from exc
    if not isinstance(obj, dict):
        raise DataFetchError(f"{what}: unexpected JSON payload")
    if "error" in obj:
        raise DataFetchError(f"{what}: server error {obj['error']}")
    return obj


def fetch_nfi_geojson(
    bbox: Bbox,
    *,
    service_url: str = NFI_LAYER_URL,
    get: Getter = http_get,
    page_size: int = 2000,
    pad_deg: float = 0.01,
    max_pages: int = 500,
) -> dict[str, Any]:
    """Page NFI polygons intersecting ``bbox`` (EPSG:27700 GeoJSON) from the ArcGIS REST layer."""
    w, s, e, n = bbox
    env = f"{w - pad_deg},{s - pad_deg},{e + pad_deg},{n + pad_deg}"
    features: list[dict[str, Any]] = []
    for page in range(max_pages):
        params = {
            "where": "1=1",
            "geometry": env,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "CATEGORY,IFT_IOA",
            "outSR": "27700",
            "orderByFields": "FID",
            "resultOffset": str(page * page_size),
            "resultRecordCount": str(page_size),
            "f": "geojson",
        }
        obj = _json(get(f"{service_url}/query?{urllib.parse.urlencode(params)}"), "NFI query")
        got = obj.get("features", [])
        features.extend(got)
        props = obj.get("properties") or {}
        more = bool(props.get("exceededTransferLimit") or obj.get("exceededTransferLimit"))
        if not more or not got:
            break
    else:
        raise DataFetchError(f"NFI query did not finish after {max_pages} pages; narrow the bbox")
    return {"type": "FeatureCollection", "features": features}


def fetch_cpm_geojson(
    bbox: Bbox,
    *,
    wfs_url: str = CPM_WFS_URL,
    get: Getter = http_get,
    pad_deg: float = 0.01,
    max_features: int = 100_000,
) -> dict[str, Any]:
    """CPM2016 polygons intersecting ``bbox`` (EPSG:27700 GeoJSON) from the NatureScot WFS."""
    w, s, e, n = bbox
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": CPM_WFS_LAYER,
        # WFS 2.0 with an EPSG:4326 URN is latitude first.
        "bbox": f"{s - pad_deg},{w - pad_deg},{n + pad_deg},{e + pad_deg},"
        "urn:ogc:def:crs:EPSG::4326",
        "srsName": "EPSG:27700",
        "outputFormat": "application/json",
        "propertyName": f"the_geom,{CPM_CLASS_COLUMN},primary_la",
        "count": str(max_features),
    }
    obj = _json(get(f"{wfs_url}?{urllib.parse.urlencode(params)}"), "CPM2016 WFS")
    feats = obj.get("features", [])
    if len(feats) >= max_features:
        raise DataFetchError("CPM2016 WFS result was truncated; narrow the bbox")
    return {"type": "FeatureCollection", "features": feats}


def _bbox_key(bbox: Bbox) -> str:
    return "_".join(f"{v:.4f}" for v in bbox)


def cached_geojson(
    cache_path: Path, fetch: Callable[[], dict[str, Any]], *, refresh: bool = False
) -> Path:
    """Write ``fetch()`` to ``cache_path`` unless it exists (cache hit)."""
    if cache_path.is_file() and cache_path.stat().st_size > 0 and not refresh:
        return cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    fc = fetch()
    tmp = cache_path.with_name(cache_path.name + ".part")
    tmp.write_text(json.dumps(fc), encoding="utf-8")
    tmp.replace(cache_path)
    return cache_path


def read_vector(path: Path, bbox: Bbox, *, assume_crs: str = "EPSG:27700") -> gpd.GeoDataFrame:
    """Read a vector file (shp, gpkg, geojson or a zip holding a shapefile) clipped to ``bbox``.

    The bbox filter is reprojected to the file CRS. A file without a CRS gets ``assume_crs``.
    """
    if not path.exists():
        raise DataFetchError(f"file not found: {path}")
    target: str = str(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            shps = sorted(n for n in zf.namelist() if n.lower().endswith(".shp"))
        if not shps:
            raise DataFetchError(f"no .shp inside {path}")
        target = f"/vsizip/{path.resolve().as_posix()}/{shps[0]}"
    mask = gpd.GeoSeries([box(*bbox)], crs="EPSG:4326")
    try:
        gdf = gpd.read_file(target, bbox=mask)
    except Exception as exc:  # fiona/pyogrio raise assorted types
        raise DataFetchError(f"cannot read {path}: {exc}") from exc
    if gdf.crs is None:
        gdf = gdf.set_crs(assume_crs)
    return gdf


def geojson_to_frame(fc: dict[str, Any], default_crs: str = "EPSG:27700") -> gpd.GeoDataFrame:
    crs = default_crs
    name = ((fc.get("crs") or {}).get("properties") or {}).get("name", "")
    if "27700" in str(name):
        crs = "EPSG:27700"
    elif "4326" in str(name) or "CRS84" in str(name):
        crs = "EPSG:4326"
    if not fc.get("features"):
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)
    return gpd.GeoDataFrame.from_features(fc["features"], crs=crs)


# ---------------------------------------------------------------------- source -> groups


def _find_column(gdf: gpd.GeoDataFrame, wanted: str) -> str:
    lower = {str(c).lower(): str(c) for c in gdf.columns}
    if wanted.lower() in lower:
        return lower[wanted.lower()]
    raise DataFetchError(
        f"column {wanted!r} not found; the file has {sorted(str(c) for c in gdf.columns)}. "
        "Pass the right column with --nfi-column / --cpm-column."
    )


def nfi_group(value: Any) -> str:
    """NFI ``IFT_IOA`` value -> group name (unknown values are 'uncertain')."""
    v = str(value).strip().lower() if value is not None else ""
    for name, members in (
        ("woodland", NFI_WOODLAND),
        ("felled", NFI_FELLED),
        ("uncertain", NFI_UNCERTAIN),
        ("hard_other", NFI_HARD_OTHER),
        ("soft_other", NFI_SOFT_OTHER),
        ("neutral", NFI_NEUTRAL),
    ):
        if v in members:
            return name
    return "uncertain"


def cpm_group(value: Any) -> str:
    """CPM2016 class -> 'bog' (1, 2), 'ambiguous' (5, -1, missing) or 'other' (0, 3, 4, -2)."""
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return "ambiguous"
    if v in (1, 2):
        return "bog"
    if v in (0, 3, 4, -2):
        return "other"
    return "ambiguous"


def rasterize_groups(
    gdf: gpd.GeoDataFrame,
    groups: Sequence[str],
    codes: dict[str, int],
    *,
    transform: Affine,
    shape: tuple[int, int],
    crs: Any,
) -> npt.NDArray[np.uint8]:
    """Burn ``groups`` (one per row) as ``codes`` on the grid with ``ml.labels``; 255 = none."""
    if len(gdf) == 0:
        return np.full(shape, NODATA, dtype=np.uint8)
    frame = gdf[["geometry"]].copy()
    frame["class"] = list(groups)
    return rasterize_labels(
        frame, transform=transform, shape=shape, crs=crs, classes=codes, nodata=NODATA
    )


def rasterize_nfi(
    gdf: gpd.GeoDataFrame,
    column: str,
    *,
    transform: Affine,
    shape: tuple[int, int],
    crs: Any,
) -> npt.NDArray[np.uint8]:
    col = _find_column(gdf, column) if len(gdf) else column
    groups = [nfi_group(v) for v in gdf[col]] if len(gdf) else []
    codes = {**NFI_CODES, "neutral": NFI_NEUTRAL_CODE}
    if len(gdf):
        order = {g: i for i, g in enumerate(_NFI_ORDER)}
        idx = sorted(range(len(groups)), key=lambda i: order[groups[i]])
        gdf = gdf.iloc[idx]
        groups = [groups[i] for i in idx]
    return rasterize_groups(gdf, groups, codes, transform=transform, shape=shape, crs=crs)


def rasterize_cpm(
    gdf: gpd.GeoDataFrame,
    column: str,
    *,
    transform: Affine,
    shape: tuple[int, int],
    crs: Any,
) -> npt.NDArray[np.uint8]:
    col = _find_column(gdf, column) if len(gdf) else column
    groups = [cpm_group(v) for v in gdf[col]] if len(gdf) else []
    return rasterize_groups(gdf, groups, CPM_CODES, transform=transform, shape=shape, crs=crs)


def combine_sources(
    nfi: npt.NDArray[np.uint8], cpm: npt.NDArray[np.uint8]
) -> npt.NDArray[np.uint8]:
    """Apply the label rules to the NFI and CPM group rasters (see module docstring).

    Class ids come from ``contracts/classes.yaml``. A pixel with no CPM polygon counts as
    non-bog (sea, estuary, gaps).
    """
    if nfi.shape != cpm.shape:
        raise ValueError("nfi and cpm rasters must have the same shape")
    ids, _ = load_classes()
    other, bog = ids["other"], ids["bog"]
    wood, felled = ids["woodland"], ids["felled_or_burnt_bare"]

    out = np.full(nfi.shape, NODATA, dtype=np.uint8)
    cpm_bog = cpm == CPM_CODES["bog"]
    cpm_other = (cpm == CPM_CODES["other"]) | (cpm == NODATA)
    free = (nfi == NODATA) | (nfi == NFI_NEUTRAL_CODE)  # NFI says nothing about the ground
    out[free & cpm_bog] = bog
    out[free & cpm_other] = other
    out[(nfi == NFI_CODES["soft_other"]) & cpm_other] = other  # bog or ambiguous stays 255
    out[nfi == NFI_CODES["hard_other"]] = other
    out[nfi == NFI_CODES["woodland"]] = wood
    out[nfi == NFI_CODES["felled"]] = felled
    return out


def apply_exclusions(
    labels: npt.NDArray[np.uint8],
    bboxes_wgs84: Iterable[Bbox],
    buffer_m: float,
    *,
    transform: Affine,
    crs: Any,
) -> npt.NDArray[np.uint8]:
    """Set pixels touching any ``bboxes_wgs84`` (buffered by ``buffer_m``) to 255."""
    out = labels.copy()
    polys = [bbox_polygon_utm(b).buffer(buffer_m) for b in bboxes_wgs84]
    if not polys:
        return out
    frame = gpd.GeoDataFrame({"class": ["x"] * len(polys)}, geometry=polys, crs=TARGET_CRS)
    mask = rasterize_labels(
        frame,
        transform=transform,
        shape=out.shape,
        crs=crs,
        classes={"x": 1},
        nodata=NODATA,
        all_touched=True,
    )
    out[mask == 1] = NODATA
    return out


def class_counts(labels: npt.NDArray[np.uint8]) -> dict[str, int]:
    ids, nodata = load_classes()
    counts = {name: int((labels == cid).sum()) for name, cid in ids.items()}
    counts["unlabelled"] = int((labels == nodata).sum())
    return counts


@dataclass(frozen=True)
class LabelResult:
    path: Path
    counts: dict[str, int]
    labelled_fraction: float


def build_label_raster(
    composite: Path,
    nfi: gpd.GeoDataFrame,
    cpm: gpd.GeoDataFrame,
    out_path: Path,
    *,
    nfi_column: str = NFI_CLASS_COLUMN,
    cpm_column: str = CPM_CLASS_COLUMN,
    exclusions: Iterable[Bbox] = (HOLDOUT_BBOX_WGS84,),
    exclusion_buffer_m: float = HOLDOUT_BUFFER_M,
) -> LabelResult:
    """Rasterise and combine both sources on exactly the ``composite`` grid; write a GeoTIFF."""
    with rasterio.open(composite) as src:
        transform, shape, crs = src.transform, (src.height, src.width), src.crs
        if str(crs) != TARGET_CRS:
            raise ValueError(f"composite CRS is {crs}, expected {TARGET_CRS}")
    kw: dict[str, Any] = {"transform": transform, "shape": shape, "crs": crs}
    nfi_r = rasterize_nfi(nfi, nfi_column, **kw)
    cpm_r = rasterize_cpm(cpm, cpm_column, **kw)
    if (nfi_r == NODATA).all() and (cpm_r == NODATA).all():
        # Without this check a wrong CRS or bbox would label everything as 'other'.
        raise DataFetchError(
            "no pixel got a label: the vector sources do not cover this composite "
            "(wrong file, CRS or bbox?)"
        )
    labels = combine_sources(nfi_r, cpm_r)
    labels = apply_exclusions(labels, exclusions, exclusion_buffer_m, transform=transform, crs=crs)
    counts = class_counts(labels)
    n_lab = labels.size - counts["unlabelled"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        out_path,
        "w",
        driver="GTiff",
        width=shape[1],
        height=shape[0],
        count=1,
        dtype="uint8",
        crs=crs,
        transform=transform,
        nodata=NODATA,
        compress="DEFLATE",
        tiled=True,
        blockxsize=256,
        blockysize=256,
    ) as dst:
        dst.write(labels, 1)
        dst.set_band_description(1, "class")
    return LabelResult(out_path, counts, n_lab / labels.size)


# ----------------------------------------------------------------------------- composites


class _NotFound(Exception):
    """Stands in for botocore's ClientError (404) for the offline S3 stub."""

    def __init__(self) -> None:
        super().__init__("not found")
        self.response = {"Error": {"Code": "404"}}


class LocalOnlyS3:
    """Offline stand-in for the S3 client: nothing is stored, nothing is ever 'up to date'.

    Lets ``pipeline.runner.run_pipeline`` write the COG locally without LocalStack.
    """

    class exceptions:  # noqa: N801  (mirrors boto3's client.exceptions namespace)
        ClientError = _NotFound

    def head_object(self, **_: Any) -> None:
        raise _NotFound()

    def upload_file(self, *_: Any, **__: Any) -> None:
        return None


def composite_name(region: str, start: date, end: date) -> str:
    return f"{region}__{start:%Y%m%d}-{end:%Y%m%d}"


def write_pipeline_config(
    region: str,
    bbox: Bbox,
    start: date,
    end: date,
    cloud_threshold: float,
    data_dir: Path,
) -> Path:
    """Write the boundary GeoJSON and a ``peatwatch-pipeline run`` YAML config; return the YAML."""
    pdir = data_dir / "pipeline"
    pdir.mkdir(parents=True, exist_ok=True)
    boundary = pdir / f"{region}.geojson"
    boundary.write_text(json.dumps(boundary_geojson(bbox)), encoding="utf-8")
    cfg = pdir / f"{composite_name(region, start, end)}.yaml"
    out_dir = (pdir / "out").as_posix()
    cfg.write_text(
        f"site: {region}\n"
        f"boundary_path: {boundary.name}\n"
        f"window:\n  start: {start.isoformat()}\n  end: {end.isoformat()}\n"
        f"cloud_threshold: {cloud_threshold}\n"
        f"output_dir: {out_dir}\n",
        encoding="utf-8",
    )
    return cfg


def pipeline_command(config: Path) -> str:
    return f"uv run peatwatch-pipeline run {config.as_posix()}"


def run_composite(
    region: str,
    bbox: Bbox,
    start: date,
    end: date,
    cloud_threshold: float,
    data_dir: Path,
    *,
    refresh: bool = False,
    runner: Callable[..., Any] | None = None,
) -> tuple[Path, Path | None]:
    """Build one composite COG with the pipeline into ``data/composites``.

    Uses ``pipeline.runner.run_pipeline`` with an offline S3 stub, so no LocalStack is needed
    (it reads public Sentinel-2 and Copernicus DEM). Returns ``(tif, sidecar)``; an existing
    file is reused unless ``refresh``.
    """
    name = composite_name(region, start, end)
    dest = data_dir / "composites" / f"{name}.tif"
    side_dest = dest.with_suffix(".json")
    cfg_path = write_pipeline_config(region, bbox, start, end, cloud_threshold, data_dir)
    if dest.is_file() and not refresh:
        return dest, side_dest if side_dest.is_file() else None
    try:
        from pipeline.cli import start_run
        from pipeline.config import load_config
        from pipeline.runner import run_pipeline
        from pipeline.stac import NoUsableScenesError
    except ImportError as exc:  # pragma: no cover - env without the pipeline package
        raise DataFetchError(
            "the pipeline package is not installed; run `uv sync --all-packages` first"
        ) from exc
    cfg = load_config(cfg_path)
    logger = start_run(cfg)
    run = runner or run_pipeline
    try:
        result = run(cfg, logger, s3=LocalOnlyS3())
    except NoUsableScenesError as exc:
        raise DataFetchError(
            f"no Sentinel-2 scenes under {cloud_threshold}% cloud for {region} "
            f"{start}..{end}: widen the window or raise --cloud-threshold ({exc})"
        ) from exc
    if result.tif_path is None:
        raise DataFetchError("pipeline returned no composite file")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(result.tif_path, dest)
    if result.sidecar_path is not None:
        shutil.copyfile(result.sidecar_path, side_dest)
    return dest, side_dest if side_dest.is_file() else None


# ------------------------------------------------------------------------------- manifest


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def file_record(path: Path, root: Path) -> dict[str, Any]:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix()
    return {"path": rel, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _git_sha() -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def update_manifest(
    data_dir: Path,
    *,
    region: str,
    bbox: Bbox,
    composites: list[dict[str, Any]],
    labels: dict[str, Any] | None,
    sources: dict[str, dict[str, Any]],
    now: datetime | None = None,
) -> Path:
    """Merge one region into ``MANIFEST.json`` (created if missing) and return its path."""
    path = data_dir / "MANIFEST.json"
    manifest: dict[str, Any] = {}
    if path.is_file():
        manifest = json.loads(path.read_text(encoding="utf-8"))
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest.update(
        {
            "manifest_version": 1,
            "updated": stamp,
            "code_version": _git_sha(),
            "crs": TARGET_CRS,
            "classes_contract": "contracts/classes.yaml",
            "label_nodata": NODATA,
            "holdout": {
                "bbox_wgs84": list(HOLDOUT_BBOX_WGS84),
                "buffer_m": HOLDOUT_BUFFER_M,
                "note": "label pixels inside the buffered box are 255",
            },
            "caveats": CAVEATS,
            "excluded_sources": EXCLUDED_SOURCES,
        }
    )
    manifest.setdefault("sources", {}).update(sources)
    region_entry = manifest.setdefault("regions", {}).setdefault(region, {})
    region_entry["bbox_wgs84"] = list(bbox)
    if composites:
        known = {c["path"]: c for c in region_entry.get("composites", [])}
        known.update({c["path"]: c for c in composites})
        region_entry["composites"] = [known[k] for k in sorted(known)]
    if labels is not None:
        region_entry["labels"] = labels
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------------------------ CLI


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date: {text!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_training_data.py",
        description=(
            "Build the Galloway training data: 18-band composite COG(s) via the pipeline, weak "
            "label rasters from NFI Scotland 2025 + CPM2016 on exactly the composite grid, and "
            "data/MANIFEST.json. Network use: Sentinel-2 + elevation (composites), NFI ArcGIS "
            "REST and NatureScot WFS (labels), all cached under <data-dir>/raw."
        ),
    )
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    p.add_argument(
        "--region",
        action="append",
        choices=sorted(REGIONS),
        help=f"named training region (repeatable; default {', '.join(DEFAULT_REGIONS)})",
    )
    p.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("W", "S", "E", "N"),
        help="custom WGS84 bbox instead of --region (needs --name)",
    )
    p.add_argument("--name", help="slug for a custom --bbox, e.g. galloway-east")
    p.add_argument(
        "--window",
        nargs=2,
        type=_parse_date,
        metavar=("START", "END"),
        default=list(DEFAULT_WINDOW),
        help="inclusive composite date window (default 2025-04-01 2025-05-31)",
    )
    p.add_argument("--cloud-threshold", type=float, default=DEFAULT_CLOUD_THRESHOLD)
    p.add_argument(
        "--skip-composite", action="store_true", help="labels only (needs the composite)"
    )
    p.add_argument("--skip-labels", action="store_true", help="composite only")
    p.add_argument("--refresh", action="store_true", help="ignore cached downloads / composites")
    p.add_argument(
        "--nfi-path",
        type=Path,
        help="local NFI Scotland vector file (shp/gpkg/geojson/zip) instead of the REST query",
    )
    p.add_argument(
        "--cpm-path",
        type=Path,
        help="local CPM2016 shapefile (.shp or the CPM2016_SCOTLAND_SHP_27700.zip)",
    )
    p.add_argument(
        "--cpm-source",
        choices=("wfs", "zip"),
        default="wfs",
        help="wfs = small bbox query (default); zip = download the 242 MB national shapefile",
    )
    p.add_argument("--nfi-column", default=NFI_CLASS_COLUMN)
    p.add_argument("--cpm-column", default=CPM_CLASS_COLUMN)
    p.add_argument(
        "--dry-run", action="store_true", help="print the plan and pipeline commands only"
    )
    return p


def resolve_regions(args: argparse.Namespace) -> dict[str, Bbox]:
    if args.bbox:
        if not args.name:
            raise ValueError("--bbox needs --name (a slug such as galloway-east)")
        if args.region:
            raise ValueError("use either --region or --bbox, not both")
        return {args.name: (args.bbox[0], args.bbox[1], args.bbox[2], args.bbox[3])}
    names = args.region or list(DEFAULT_REGIONS)
    return {n: REGIONS[n] for n in names}


def load_label_sources(
    bbox: Bbox,
    raw_dir: Path,
    *,
    nfi_path: Path | None,
    cpm_path: Path | None,
    cpm_source: str,
    refresh: bool,
    get: Getter = http_get,
    opener: Opener | None = None,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict[str, Path]]:
    """Return ``(nfi, cpm, files)``; ``files`` maps source key -> the cached or given file."""
    key = _bbox_key(bbox)
    if nfi_path is not None:
        nfi_file = nfi_path
        nfi = read_vector(nfi_file, bbox)
    else:
        nfi_file = cached_geojson(
            raw_dir / f"nfi_scotland_2025_{key}.geojson",
            lambda: fetch_nfi_geojson(bbox, get=get),
            refresh=refresh,
        )
        nfi = geojson_to_frame(json.loads(nfi_file.read_text(encoding="utf-8")))
    if cpm_path is not None:
        cpm_file = cpm_path
        cpm = read_vector(cpm_file, bbox)
    elif cpm_source == "zip":
        cpm_file = download_file(
            CPM_ZIP_URL, raw_dir / "CPM2016_SCOTLAND_SHP_27700.zip", refresh=refresh, opener=opener
        )
        cpm = read_vector(cpm_file, bbox)
    else:
        cpm_file = cached_geojson(
            raw_dir / f"cpm2016_{key}.geojson",
            lambda: fetch_cpm_geojson(bbox, get=get),
            refresh=refresh,
        )
        cpm = geojson_to_frame(json.loads(cpm_file.read_text(encoding="utf-8")))
    if len(nfi) == 0:
        print(f"warning: no NFI polygons in {bbox}; every pixel will be non-woodland", flush=True)
    if len(cpm) == 0:
        print(f"warning: no CPM2016 polygons in {bbox}; no bog labels will result", flush=True)
    return nfi.to_crs(TARGET_CRS), cpm.to_crs(TARGET_CRS), {"nfi": nfi_file, "cpm2016": cpm_file}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Sentinel-2 COGs on AWS Earth Search are public; read them without credentials.
    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
    try:
        regions = resolve_regions(args)
        start, end = args.window
        if end < start:
            raise ValueError(f"window end {end} is before start {start}")
        for name, bbox in regions.items():
            d = check_outside_holdout(bbox)
            print(f"{name}: bbox {bbox} is {d / 1000:.1f} km from the hold-out box")
        data_dir: Path = args.data_dir
        if args.dry_run:
            for name, bbox in regions.items():
                cfg = write_pipeline_config(name, bbox, start, end, args.cloud_threshold, data_dir)
                print(f"pipeline config: {cfg}")
                print(f"  equivalent CLI (needs LocalStack): {pipeline_command(cfg)}")
            return 0
        for name, bbox in regions.items():
            _build_region(args, name, bbox, start, end, data_dir)
    except (ValueError, DataFetchError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


def _build_region(
    args: argparse.Namespace, name: str, bbox: Bbox, start: date, end: date, data_dir: Path
) -> None:
    composites: list[dict[str, Any]] = []
    comp_path = data_dir / "composites" / f"{composite_name(name, start, end)}.tif"
    if not args.skip_composite:
        print(f"[{name}] composite {start}..{end} (cloud <= {args.cloud_threshold}%)", flush=True)
        tif, side = run_composite(
            name, bbox, start, end, args.cloud_threshold, data_dir, refresh=args.refresh
        )
        comp_path = tif
        composites.append({**file_record(tif, data_dir), "window": [str(start), str(end)]})
        if side is not None:
            composites[-1]["sidecar"] = file_record(side, data_dir)
        print(f"[{name}] wrote {tif}", flush=True)
    labels_rec: dict[str, Any] | None = None
    sources: dict[str, dict[str, Any]] = {}
    if not args.skip_labels:
        if not comp_path.is_file():
            raise DataFetchError(f"composite not found: {comp_path} (run without --skip-composite)")
        print(f"[{name}] labels", flush=True)
        nfi, cpm, files = load_label_sources(
            bbox,
            data_dir / "raw",
            nfi_path=args.nfi_path,
            cpm_path=args.cpm_path,
            cpm_source=args.cpm_source,
            refresh=args.refresh,
        )
        res = build_label_raster(
            comp_path,
            nfi,
            cpm,
            data_dir / "labels" / f"{name}.tif",
            nfi_column=args.nfi_column,
            cpm_column=args.cpm_column,
        )
        labels_rec = {
            **file_record(res.path, data_dir),
            "class_counts": res.counts,
            "labelled_fraction": round(res.labelled_fraction, 4),
            "grid_from": comp_path.name,
        }
        fetched = datetime.now(UTC).strftime("%Y-%m-%d")
        for key, f in files.items():
            sources[key] = {
                **SOURCE_INFO[key],
                "fetched": fetched,
                "file": file_record(f, data_dir),
                "fetch_bbox_wgs84": list(bbox),
                "columns": {"nfi": args.nfi_column, "cpm2016": args.cpm_column}[key],
            }
        print(f"[{name}] class counts: {res.counts}", flush=True)
    manifest = update_manifest(
        data_dir, region=name, bbox=bbox, composites=composites, labels=labels_rec, sources=sources
    )
    print(f"[{name}] manifest: {manifest}", flush=True)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
