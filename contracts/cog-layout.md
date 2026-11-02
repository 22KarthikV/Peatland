# COG, sidecar, STAC and S3 layout (contract C2)

Contract version: 1. Consumers: `pipeline` (writes composites), `ml` (writes classifications and
changes), `api` (indexes objects, serves downloads), TiTiler and `web` (read COGs). Class ids and
colours come from `classes.yaml`; band order from `bands.yaml`. Anything here changes only through
the orchestrator.

## 1. Buckets

| Bucket | Holds | Written by |
|---|---|---|
| `peatwatch-data` | Raster products (composites, classifications, changes), their sidecars and STAC items, site boundaries | pipeline, ml worker |
| `peatwatch-artifacts` | Model files, run logs, statistics exports | ml, worker, api |

Locally both buckets live in LocalStack (SPEC D1). Keys are deterministic paths and do not encode
parameters; the sidecar records what produced the current object. Rerunning with a new parameter
set or code version gives a new idempotency key and replaces the object (section 6); history lives
in run logs and the DB. Model versions are in the key, so products from different models coexist.

## 2. Path tokens

| Token | Rule | Example |
|---|---|---|
| `<site>` | site slug, `^[a-z0-9][a-z0-9-]{1,62}$`, equals `sites.slug` in the DB | `glen-trool` |
| `<year>` | four-digit year of the window start | `2025` |
| `<season>` | `spring` (Mar-May), `summer` (Jun-Aug), `autumn` (Sep-Nov), `winter` (Dec-Feb, year = December's year), `annual`, or a custom window `w<YYYYMMDD>-<YYYYMMDD>` (inclusive dates) | `summer`, `w20250319-20250319` |
| `<model>` | model name, `^[a-z0-9][a-z0-9_-]{0,31}$` | `unet`, `rf`, `ndvi_threshold` |
| `<model_version>` | model version, `^[0-9A-Za-z._-]{1,32}$` | `1.2.0` |

The custom window form exists because the demo date pair (ADR 0003: 2025-03-19 vs 2025-04-08) is
two short windows, not two seasons. All dates are UTC.

## 3. Keys

All keys are relative to the bucket. Every `.tif` has two siblings with the same stem:
`<stem>.json` (sidecar, `sidecar.schema.json`) and `<stem>.stac.json` (STAC item,
`stac-item.example.json`).

```
peatwatch-data/
  sites/<site>/boundary.geojson
  composites/<site>/<year>/<season>.tif
  composites/<site>/<year>/<season>.json
  composites/<site>/<year>/<season>.stac.json
  classifications/<site>/<year>/<season>/<model>-<model_version>.tif   (+ .json, .stac.json)
  changes/<site>/<y1>-<season1>__<y2>-<season2>/<model>-<model_version>.tif   (+ .json, .stac.json)

peatwatch-artifacts/
  models/<model>/<model_version>/model.onnx        (or model.pt for TorchScript)
  models/<model>/<model_version>/model-card.json
  runs/<run_id>/log.jsonl                          (structured run log)
  exports/<run_id>/stats.csv                       (per-class hectares, change table)
```

Examples:

```
composites/glen-trool/2025/w20250319-20250319.tif
classifications/glen-trool/2025/w20250408-20250408/unet-1.0.0.tif
changes/glen-trool/2025-w20250319-20250319__2025-w20250408-20250408/unet-1.0.0.tif
```

`<y1>-<season1>` is the earlier window. A change map is only produced from two classifications by
the same `<model>-<model_version>`.

## 4. Raster format

| | Composite | Classification | Change |
|---|---|---|---|
| dtype | `float32` | `uint8` | `uint8` |
| Bands | 18: the 12 S2 L2A bands then 6 features, order exactly as `bands.yaml` | 1 | 1 |
| Values | surface reflectance (DN x 0.0001), indices, metres, degrees | class id from `classes.yaml` | `from_id * 16 + to_id` |
| Nodata | `NaN` (GDAL nodata tag `nan`) | `255` (`classes.yaml` `nodata`) | `255` |
| Colormap | none | embedded GeoTIFF palette from `classes.yaml` hex; nodata entry transparent | embedded: `from == to` -> `#DDDDDD`, otherwise the `to` class hex; 255 transparent |
| Band description | band/feature `name` from `bands.yaml` | `class` | `change` |
| Overview resampling | `average` | `nearest` (`mode` acceptable) | `nearest` |
| Compression | `DEFLATE`, `PREDICTOR=3` | `DEFLATE`, `PREDICTOR=2` | `DEFLATE`, `PREDICTOR=2` |

The change encoding fits `uint8` while class ids stay at or below 14 (decode: `from = v // 16`,
`to = v % 16`). Unchanged pixels keep their `from == to` code so "unchanged woodland" stays
distinguishable from "unchanged bog".

Common to all three:
- **CRS: EPSG:32630 (WGS 84 / UTM zone 30N).** All four Sentinel-2 tiles for the training region
  and the demo site (30UUF, 30UVF, 30UUG, 30UVG) are delivered in UTM 30N, so using it avoids
  resampling the spectral data; the label vectors (in EPSG:27700) are reprojected instead, which is
  lossless for polygons.
- **Grid:** 10 m pixels, origin snapped to a multiple of 10 m, so every product for a site shares
  one pixel grid (20 m and 60 m bands and the 30 m terrain features are resampled to 10 m
  with bilinear).
- **COG layout:** GDAL `COG` driver, internal tiles `512 x 512`, `BIGTIFF=IF_SAFER`, pixel-interleaved
  for classification/change and band-interleaved (`INTERLEAVE=BAND`) for composites.
- **Overviews:** internal, factors 2, 4, 8, ... until the smallest overview fits in one 512 x 512
  tile. No external `.ovr` files.
- **Determinism:** no `TIFFTAG_DATETIME` or other wall-clock tags are written, so the same inputs
  produce byte-identical files (Success Criterion 2). Timestamps live only in the sidecar.
- Validity is checked with `rio cogeo validate` (or `rasterio` equivalent) in pipeline and ml tests.

## 5. Sidecar and STAC item

- **Sidecar** (`<stem>.json`): the provenance record, validated by `sidecar.schema.json`
  (JSON Schema 2020-12). It carries the kind, site, window, bands, scene ids, versions, parameters,
  cloud statistics, upstream `inputs`, the idempotency key and `created_at`; change sidecars also
  carry `change_pair` (from and to windows; `year`/`season` equal the "to" window). The API reads sidecars to fill the
  `composites`, `classifications` and `changes` tables.
- **STAC item** (`<stem>.stac.json`): STAC 1.0.0 item for discovery and TiTiler, following
  `stac-item.example.json`. Required: `type`, `stac_version`, `id`, `geometry` (WGS 84), `bbox`,
  `properties.datetime` (null) with `start_datetime`/`end_datetime`, `links`, `assets`. The data
  asset is keyed `data` with media type
  `image/tiff; application=geotiff; profile=cloud-optimized`; the sidecar is asset `sidecar`.
  Projection (`proj:code`, `proj:shape`, `proj:transform`), `raster:bands`, `eo:bands` (composite)
  and `classification:classes` (classification) extensions are used. Item `id` is the key without
  the extension, slashes replaced by `.`; e.g. `composites.glen-trool.2025.summer`.

## 6. Idempotency key

Each product has an idempotency key: the lowercase hex SHA-256 of the canonical JSON of:

```json
{
  "kind": "composite | classification | change",
  "site": "<site slug>",
  "boundary_sha256": "<sha256 of the boundary GeoJSON bytes as stored in S3>",
  "date_range": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "parameters": { "...": "the full resolved parameter set for the step" },
  "versions": {
    "code": "<git commit sha of the producing package>",
    "model": "<model>-<model_version> or null for composites",
    "classes_contract": 1,
    "bands_contract": 1
  },
  "inputs": ["<idempotency keys of upstream products, sorted>"]
}
```

For a change product `date_range` spans the earlier window start to the later window end and
`inputs` holds the two classification keys. A composite's `inputs` is empty; its imagery is pinned
by `parameters` (STAC collection, cloud threshold, etc.) and recorded in the sidecar's `scene_ids`.

Canonical JSON: UTF-8, keys sorted at every level, separators `,` and `:` with no whitespace,
no NaN/Infinity, floats in shortest round-trip form (Python
`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`).

Rules:
1. Before running a step, compute its key. If the target `<stem>.json` exists and its
   `idempotency_key` matches, skip the step (Success Criterion 2: rerun skips finished steps).
2. If the sidecar exists with a different key, the step reruns and overwrites `.tif`, `.json` and
   `.stac.json` together; the sidecar is written last, so a crash leaves a stale or missing key and
   the step reruns.
3. The `run_id` is not part of the key; two runs with the same inputs share the product.
