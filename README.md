# PeatWatch

Peatland and woodland condition monitoring from Sentinel-2 imagery. Draw a site on a map, run a classification for one or two time windows, compare them, and see the change in hectares.

The project covers the full chain: a Sentinel-2 and elevation pipeline, segmentation models, a FastAPI backend with a queue and worker, and a React/MapLibre dashboard. The cloud side runs locally against [LocalStack](https://www.localstack.cloud/); no real AWS account is used.

> **Status: work in progress.** The end-to-end stack runs locally with a rule-based NDVI baseline as the "model". The U-Net has a training script but has not been trained yet, so there are no accuracy numbers to report. See [Status](#status) for details.

## How it works

```
 Browser (React + MapLibre)
        |  REST (X-API-Key)                 map tiles
        v                                      ^
   FastAPI  ---- PostGIS (sites, runs, models) |
        |                                   TiTiler
        |  SQS message                        ^
        v                                     | reads COGs
     Worker  --> pipeline (Sentinel-2 + DEM composites, 18 bands, EPSG:32630, 10 m)
        |    --> model (NDVI baseline / random forest / U-Net) --> classification COG
        +----------------------------------------------------> S3 (LocalStack)
```

- **Pipeline** (`pipeline/`): searches Sentinel-2 L2A on AWS Earth Search (no login), masks clouds, builds seasonal composites with spectral indices and terrain features, and writes Cloud-Optimized GeoTIFFs.
- **ML** (`ml/`): block-wise spatial train/validation/test splits, an NDVI-threshold baseline, a random-forest baseline, a U-Net training script (`segmentation_models_pytorch`), evaluation metrics, COG inference and change detection.
- **API** (`api/`): FastAPI with API-key auth and rate limiting. Sites, runs, classifications, change maps and area statistics (JSON and CSV). A worker consumes jobs from SQS and fails stuck runs with a clear error.
- **Web** (`web/`): React 19, TypeScript, Vite, MapLibre GL. Pages for sites, creating a site, the map with layers and statistics, change view and run status.
- **Infra** (`infra/`): Terraform for buckets, queues, secrets and IAM, applied to LocalStack with `tflocal`. AWS-only resources (ECS, RDS, CloudFront) are being added as validate-and-plan only.
- **Contracts** (`contracts/`): the OpenAPI spec, database schema, COG layout, class list and queue contract that the modules are built against.

Classes: `other`, `bog`, `woodland`, `felled_or_burnt_bare`. Training region: Galloway, Scotland. Held-out demo site: the April 2025 Glen Trool wildfire. See [ADR 0003](docs/decisions/0003-site-and-classes.md).

## Run it locally

Requirements: Docker, [uv](https://docs.astral.sh/uv/), Node.js, and a LocalStack auth token (free account).

```bash
cp .env.example .env            # then put your LOCALSTACK_AUTH_TOKEN in .env
docker compose up -d --build    # postgis, localstack, titiler, api, worker, web
```

Then open <http://localhost:5173>. The API is at <http://localhost:8000/docs>. The development API key is `dev-only-key` (local only).

A minimal check of the tile service: `bash scripts/smoke_tiles.sh`.

### Development

```bash
uv sync --all-packages --extra train     # Python workspace (CPU torch for the train extra)
npm --prefix web ci

uv run python -m pytest                  # Python tests with coverage
uv run ruff check . && uv run ruff format --check .
uv run mypy api/src ml/src
npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web test
```

Terraform (local only):

```bash
terraform -chdir=infra init
tflocal -chdir=infra apply -var-file=envs/local.tfvars
```

## Status

| Area | State |
|---|---|
| Contracts, schema, CI | Done |
| Pipeline (composites, features, COG output) | Done, runs on real Sentinel-2 data |
| NDVI and random-forest baselines, evaluation, change detection | Done |
| API, queue, worker, stale-run reaper | Done |
| Web dashboard (sites, map, change, run status) | Done, reviewed in the browser |
| U-Net training script | Done, not yet trained on real data |
| Terraform core resources on LocalStack | Done |
| Terraform AWS-only resources (validate and plan) | In progress |
| Training data build and Colab run | In progress |
| Trained model, evaluation report, model card | Not started |
| End-to-end browser tests, usability test, demo recording | Not started |

**Known limits.** Labels for training come from public maps (NatureScot peat map, Forestry Commission woodland inventory). The woodland inventory is partly derived from Sentinel-2, so agreement with it overstates skill; the plan is to evaluate on a separate hand-labelled set. The NDVI baseline is a simple rule and is not a measure of what the trained model will achieve.

## Project documents

- [`SPEC.md`](SPEC.md): objectives, decisions and success criteria
- [`CAPABILITY-MAP.md`](CAPABILITY-MAP.md): module breakdown
- [`tasks/plan.md`](tasks/plan.md) and [`tasks/todo.md`](tasks/todo.md): plan and task checklist
- [`docs/decisions/`](docs/decisions): architecture decision records
- [`docs/labels/`](docs/labels): label sources and the hand-labelling protocol
- [`contracts/`](contracts): API, data and queue contracts
