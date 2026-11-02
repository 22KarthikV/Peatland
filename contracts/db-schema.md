# Database schema (contract C3)

Contract version: 1. Consumers: `api` (SQLAlchemy/GeoAlchemy2 models and Alembic migrations),
worker (writes runs and products), `web` (through the API only). Built on C2: product rows mirror
the sidecars in `cog-layout.md` section 5, and their `idempotency_key` is the key from section 6.
PostgreSQL 16 with PostGIS 3 and `pgcrypto` (for `gen_random_uuid()`).

## 1. Conventions

- Primary keys are `uuid DEFAULT gen_random_uuid()`. The API exposes them as strings.
- Timestamps are `timestamptz`, stored in UTC; `created_at` defaults to `now()`.
- Hashes (`idempotency_key`, `boundary_sha256`) are `char(64)` lowercase hex, checked with
  `CHECK (col ~ '^[0-9a-f]{64}$')`.
- JSON columns are `jsonb`.
- **Geometry:** site boundaries are stored as `geometry(MultiPolygon, 4326)` (WGS 84, the GeoJSON
  CRS). A `Polygon` input is promoted with `ST_Multi`. Rasters are in EPSG:32630 (C2), so areas are
  computed in that CRS, `ST_Area(ST_Transform(boundary, 32630)) / 10000`, which matches the 10 m
  pixel count (1 pixel = 0.01 ha) used for statistics.
- **Areas are hectares** everywhere (`*_ha` columns and JSON keys), `numeric(12, 2)` in columns.
- Foreign keys use `ON DELETE RESTRICT`; the MVP has no delete endpoints. Every FK column has an
  index (listed below or covered by a unique index's leading column).
- `<site>`, `<year>`, `<season>`, `<model>`, `<model_version>` are the C2 path tokens.

## 2. Enum types

```sql
CREATE TYPE run_kind   AS ENUM ('pipeline', 'change');
CREATE TYPE run_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'cancelled');
```

- `pipeline`: composite for each requested window, then classification with the chosen model
  (`POST /sites/{id}/runs`).
- `change`: change map and statistics between two classified windows (`POST /sites/{id}/changes`).

## 3. Tables

### sites

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `uuid` | no | PK |
| `slug` | `text` | no | Unique. C2 `<site>` token, `CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$')`; derived from `name` when not given |
| `name` | `text` | no | `CHECK (length(name) BETWEEN 1 AND 200)` |
| `boundary` | `geometry(MultiPolygon, 4326)` | no | `CHECK (ST_IsValid(boundary))` |
| `area_ha` | `numeric(12,2)` | no | Generated: `ST_Area(ST_Transform(boundary, 32630)) / 10000`, stored |
| `boundary_sha256` | `char(64)` | no | SHA-256 of the GeoJSON bytes stored at `boundary_uri`; feeds the C2 idempotency key |
| `boundary_uri` | `text` | no | `s3://peatwatch-data/sites/<site>/boundary.geojson` |
| `created_at` | `timestamptz` | no | |

Indexes: `UNIQUE (slug)`; `GIST (boundary)`; `btree (created_at DESC)` for listing.

Validation (in the API before insert, error codes in `openapi.yaml`): valid polygon, inside the
supported region (the union of Sentinel-2 tiles 30UUF, 30UVF, 30UUG, 30UVG, configured as a
polygon), and `area_ha` not above `MAX_SITE_AREA_HA` (default 50 000 ha).

### composites

Output of the pipeline, one row per site and window. Mirrors a composite sidecar.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `uuid` | no | PK |
| `site_id` | `uuid` | no | FK `sites.id` |
| `year` | `smallint` | no | `CHECK (year BETWEEN 2015 AND 2100)` |
| `season` | `text` | no | `CHECK (season ~ '^(spring\|summer\|autumn\|winter\|annual\|w[0-9]{8}-[0-9]{8})$')` |
| `date_start` | `date` | no | sidecar `date_range.start` |
| `date_end` | `date` | no | sidecar `date_range.end`, `CHECK (date_end >= date_start)` |
| `cog_uri` | `text` | no | Unique. `s3://peatwatch-data/composites/<site>/<year>/<season>.tif` |
| `pipeline_version` | `text` | no | sidecar `versions.code` (git sha), plus `versions.package` when present, e.g. `pipeline 0.1.0 (a1b2c3d)` |
| `scene_ids` | `text[]` | no | sidecar `scene_ids` |
| `cloud_stats` | `jsonb` | no | sidecar `cloud_stats` |
| `parameters` | `jsonb` | no | sidecar `parameters` |
| `idempotency_key` | `char(64)` | no | Unique. sidecar `idempotency_key` |
| `run_id` | `uuid` | yes | FK `runs.id`; the run that last wrote the object |
| `created_at` | `timestamptz` | no | sidecar `created_at` |

Indexes: `UNIQUE (idempotency_key)`; `UNIQUE (cog_uri)`; `UNIQUE (site_id, year, season)` (the
S3 key is deterministic, so a rerun with new parameters replaces the row in place, see section 6);
`btree (site_id, date_start)`; `btree (run_id)`.

### models

One row per trained model version.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `uuid` | no | PK |
| `name` | `text` | no | C2 `<model>`, `CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,31}$')`, e.g. `ndvi_threshold`, `rf`, `unet` |
| `version` | `text` | no | C2 `<model_version>`, `CHECK (version ~ '^[0-9A-Za-z._-]{1,32}$')` |
| `metrics` | `jsonb` | no | mean IoU, per-class IoU and F1, confusion matrix, validation-set scores; default `'{}'` |
| `artifact_uri` | `text` | no | `s3://peatwatch-artifacts/models/<model>/<model_version>/model.onnx` (or `model.pt`) |
| `model_card_uri` | `text` | yes | `s3://peatwatch-artifacts/models/<model>/<model_version>/model-card.json` |
| `git_commit` | `text` | no | `CHECK (git_commit ~ '^[0-9a-f]{7,40}$')` |
| `classes_contract` | `smallint` | no | `classes.yaml` version the model predicts |
| `is_default` | `boolean` | no | default `false`; at most one row true (partial unique index) |
| `created_at` | `timestamptz` | no | |

Indexes: `UNIQUE (name, version)`; `UNIQUE (is_default) WHERE is_default`.

### runs

Tracks every processing job. Created by the API in `queued`, then owned by the worker.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `uuid` | no | PK; also the `<run_id>` in `runs/<run_id>/log.jsonl` and SQS messages |
| `site_id` | `uuid` | no | FK `sites.id` |
| `kind` | `run_kind` | no | |
| `status` | `run_status` | no | default `queued`; see section 4 |
| `model_id` | `uuid` | no | FK `models.id`; the model used for classification or change |
| `params` | `jsonb` | no | The validated request body, resolved (e.g. windows, cloud threshold, model) |
| `idempotency_key` | `char(64)` | no | SHA-256 of canonical JSON `{kind, site, boundary_sha256, params, model}` (C2 canonical-JSON rules). Not unique overall; see index below |
| `progress` | `jsonb` | no | default `'{}'`; `{"step": "composite", "done": 1, "total": 3}` for the run-status screen |
| `outputs` | `jsonb` | no | default `'{}'`; ids of products written or reused: `{"composite_ids": [], "classification_ids": [], "change_id": null}` |
| `error` | `jsonb` | yes | `{code, message, detail}` (same shape as the API error); set only when `failed` |
| `log_uri` | `text` | yes | `s3://peatwatch-artifacts/runs/<run_id>/log.jsonl` |
| `created_at` | `timestamptz` | no | enqueue time |
| `started_at` | `timestamptz` | yes | set on `queued -> running` |
| `finished_at` | `timestamptz` | yes | set on entering a terminal state |
| `heartbeat_at` | `timestamptz` | yes | worker updates while `running`; a stale heartbeat lets the reaper fail the run |

Checks:
- `CHECK ((status = 'failed') = (error IS NOT NULL))`
- `CHECK (status NOT IN ('running', 'succeeded') OR started_at IS NOT NULL)`
- `CHECK ((status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL))`
- `CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)`

(`failed` and `cancelled` may have a null `started_at` when they come straight from `queued`.)

Indexes: `btree (site_id, created_at DESC)`; `btree (status, created_at)` (worker and reaper);
`btree (model_id)`; **`UNIQUE (idempotency_key) WHERE status IN ('queued', 'running')`**.

Run idempotency: products are deduplicated by their own keys (C2 section 6), so the run key only
stops duplicate *active* jobs. When the API receives a run or change request whose key matches an
active run, it returns that run (`200`) instead of enqueuing another. Once the earlier run is
terminal, the same request enqueues a new run; that is also how "retry on failure" works, and the
new run skips any product whose sidecar key already matches. `run_id` is never part of a product
key.

### classifications

Land condition map for one composite and one model. Mirrors a classification sidecar.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `uuid` | no | PK |
| `composite_id` | `uuid` | no | FK `composites.id` |
| `model_id` | `uuid` | no | FK `models.id` |
| `cog_uri` | `text` | no | Unique. `s3://peatwatch-data/classifications/<site>/<year>/<season>/<model>-<model_version>.tif` |
| `class_areas_ha` | `jsonb` | no | `{"<class_id>": hectares}` inside the boundary, nodata excluded; for the statistics panel |
| `idempotency_key` | `char(64)` | no | Unique. sidecar `idempotency_key`; its `inputs` holds the composite's key |
| `run_id` | `uuid` | yes | FK `runs.id` |
| `created_at` | `timestamptz` | no | sidecar `created_at` |

Indexes: `UNIQUE (idempotency_key)`; `UNIQUE (cog_uri)`; `UNIQUE (composite_id, model_id)`;
`btree (model_id)`; `btree (run_id)`.

The site is reached through `composite_id`; `GET /sites/{id}/classifications` joins
`classifications -> composites` on `site_id` and orders by `composites.date_start`.

### changes

Change map and area per transition between two windows, same model on both sides.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `uuid` | no | PK |
| `site_id` | `uuid` | no | FK `sites.id` (denormalised for listing and checks) |
| `from_composite_id` | `uuid` | no | FK `composites.id`, the earlier window |
| `to_composite_id` | `uuid` | no | FK `composites.id`, the later window |
| `from_classification_id` | `uuid` | no | FK `classifications.id` |
| `to_classification_id` | `uuid` | no | FK `classifications.id` |
| `model_id` | `uuid` | no | FK `models.id`; equals the model of both classifications (checked in the worker) |
| `cog_uri` | `text` | no | Unique. `s3://peatwatch-data/changes/<site>/<y1>-<season1>__<y2>-<season2>/<model>-<model_version>.tif` |
| `stats` | `jsonb` | no | Area per transition in hectares; shape below |
| `stats_csv_uri` | `text` | yes | `s3://peatwatch-artifacts/exports/<run_id>/stats.csv` |
| `idempotency_key` | `char(64)` | no | Unique. sidecar `idempotency_key`; `inputs` holds the two classification keys |
| `run_id` | `uuid` | yes | FK `runs.id` |
| `created_at` | `timestamptz` | no | sidecar `created_at` |

Checks: `CHECK (from_composite_id <> to_composite_id)`. The worker also checks that both
composites belong to `site_id` and that the "from" window starts before the "to" window.

Indexes: `UNIQUE (idempotency_key)`; `UNIQUE (cog_uri)`;
`UNIQUE (from_composite_id, to_composite_id, model_id)`; `btree (site_id, created_at DESC)`;
`btree (to_composite_id)`; `btree (from_classification_id)`; `btree (to_classification_id)`;
`btree (model_id)`; `btree (run_id)`.

`stats` shape (the JSON body of `GET /changes/{id}/stats` is built from it):

```json
{
  "pixel_area_ha": 0.01,
  "total_ha": 1234.56,
  "transitions": [
    {"from_class_id": 1, "to_class_id": 3, "area_ha": 12.34, "percent": 1.0}
  ],
  "class_areas_ha": {"from": {"1": 800.0}, "to": {"1": 787.66}}
}
```

`transitions` lists every non-zero (from, to) pair including `from == to` (unchanged), decoded
from the change COG as `from = v // 16`, `to = v % 16` (C2 section 4). `percent` is of `total_ha`
(valid pixels inside the boundary). Class names and colours come from `classes.yaml`.

## 4. Run state machine

```
POST -> queued --claim--> running --done--> succeeded
          |                 |
          |                 +--error/stale--> failed
          |                 +--cancel-------> cancelled
          +--enqueue fails------------------> failed
          +--cancel-------------------------> cancelled
```

| From | To | Who | When |
|---|---|---|---|
| `queued` | `running` | worker | claims the SQS message; sets `started_at`, `heartbeat_at` |
| `queued` | `failed` | api | the SQS send fails after the row is created (error `enqueue_failed`) |
| `queued` | `cancelled` | api/operator | cancelled before a worker picks it up |
| `running` | `succeeded` | worker | all steps done; sets `outputs`, `finished_at` |
| `running` | `failed` | worker or reaper | step error (e.g. `no_usable_scenes`) or heartbeat older than the timeout (`worker_lost`); sets `error`, `finished_at` |
| `running` | `cancelled` | worker | cancel requested; worker stops between steps; sets `finished_at` |

`succeeded`, `failed` and `cancelled` are terminal; no transition leaves them. A retry is a new
run (section 3, runs). Every transition is a compare-and-set so two workers cannot both claim a
run:

```sql
UPDATE runs SET status = 'running', started_at = now(), heartbeat_at = now()
WHERE id = :id AND status = 'queued'
RETURNING id;          -- zero rows: someone else has it, or it was cancelled; ack and drop
```

The allowed set is enforced in the API/worker repository layer and by a `BEFORE UPDATE OF status`
trigger that raises on any pair not in the table above. The MVP exposes no cancel endpoint;
cancellation is an operator action.

## 5. URIs and S3 keys

Every `*_uri` column holds a full `s3://<bucket>/<key>` URI, where `<key>` follows
`cog-layout.md` section 3 exactly:

| Column | Bucket | Key |
|---|---|---|
| `sites.boundary_uri` | `peatwatch-data` | `sites/<site>/boundary.geojson` |
| `composites.cog_uri` | `peatwatch-data` | `composites/<site>/<year>/<season>.tif` |
| `classifications.cog_uri` | `peatwatch-data` | `classifications/<site>/<year>/<season>/<model>-<model_version>.tif` |
| `changes.cog_uri` | `peatwatch-data` | `changes/<site>/<y1>-<season1>__<y2>-<season2>/<model>-<model_version>.tif` |
| `models.artifact_uri` | `peatwatch-artifacts` | `models/<model>/<model_version>/model.onnx` (or `model.pt`) |
| `models.model_card_uri` | `peatwatch-artifacts` | `models/<model>/<model_version>/model-card.json` |
| `runs.log_uri` | `peatwatch-artifacts` | `runs/<run_id>/log.jsonl` |
| `changes.stats_csv_uri` | `peatwatch-artifacts` | `exports/<run_id>/stats.csv` |

- Sidecar and STAC item are not stored as columns: replace the `.tif` suffix with `.json` or
  `.stac.json`. The STAC item id is the key without extension, `/` replaced by `.`.
- `<site>` is `sites.slug`; `<year>`/`<season>` are the composite's columns;
  `<model>-<model_version>` is `models.name || '-' || models.version`. The API can rebuild any
  product URI from the row and its joins, and a test should assert the stored URI equals the
  rebuilt one.
- The bucket names are fixed by C2; the endpoint differs (LocalStack locally, AWS in Terraform),
  so URIs never contain an endpoint host. TiTiler and presigned URLs resolve them with the
  configured endpoint.

## 6. Ingest and idempotency

The worker writes the product to S3 (sidecar last, C2 rule 2), then upserts its row in the same
transaction that updates the run:

```sql
INSERT INTO composites (...) VALUES (...)
ON CONFLICT (site_id, year, season) DO UPDATE
  SET idempotency_key = EXCLUDED.idempotency_key, parameters = EXCLUDED.parameters, ...
  WHERE composites.idempotency_key <> EXCLUDED.idempotency_key;
```

The same pattern applies to classifications (`ON CONFLICT (composite_id, model_id)`) and changes
(`ON CONFLICT (from_composite_id, to_composite_id, model_id)`). When the step was skipped because
the sidecar key matched, the row is left unchanged and the run's `outputs` references the
existing id. `UNIQUE (idempotency_key)` guarantees one row per exact product; the natural-key
uniques guarantee one row per S3 object.

Replacing a composite in place leaves its classifications and changes pointing at the new row id
(same id, updated columns); their own keys no longer match the new upstream key, so the next run
recomputes them (C2 rule 2).
