# PRD: Peatland Condition Monitoring Platform

Nov 2, 2026 · @Karthik

## 1. Overview

**PeatWatch** (working name) is a small end-to-end platform that measures the condition of a peatland or woodland site from satellite imagery and shows how it changes over time. It is a portfolio project built to mirror the Junior Software Engineer role at Calterra: a data pipeline, a PyTorch model, a web dashboard and AWS infrastructure, all owned by one person.

**Problem.** Nature capital projects need repeatable, evidence-based monitoring of site condition. Doing this by hand from raw imagery is slow and hard to reproduce.

**Goals**

- Ingest satellite and elevation data for a chosen Scottish site automatically and reproducibly.
- Train and evaluate a PyTorch model that classifies land condition per pixel.
- Compare two dates and report what changed, in hectares and percent.
- Let a user view results on a map through a web dashboard.
- Deploy everything to AWS with infrastructure as code, tests and CI/CD.

**Non-goals**

- Carbon credit calculations or any claim of regulatory-grade verification.
- Multi-tenant auth, billing or a production-scale user base.
- State-of-the-art model accuracy. A sound, honest evaluation matters more than a high score.

**Success criteria**

- A new site can go from a boundary file to a map with change statistics with one command or one upload.
- The model beats a simple baseline (e.g. NDVI thresholding) on a held-out spatial test area.
- Test coverage above 70% on pipeline and API code; CI is green on every merge.
- A public repo with a clear README, architecture diagram and short demo video or GIF.

**Mapping to the job spec**

| Job requirement | Where the project shows it |
| --- | --- |
| Train, evaluate and deploy geospatial ML models | Component 2 (model) |
| Build the platform UI and improve UX | Component 4 (dashboard) |
| Cloud infrastructure and deployments on AWS | Component 5 (infrastructure) |
| Process and manage large aerial, satellite and elevation datasets | Component 1 (pipeline) |
| Python, Git, tests, command line, clean code | All components; Section 9 |
| Learns independently, owns what they build | Solo delivery with documented decisions |

## 2. Users and scope

The MVP serves one persona and one site; everything else is a stretch goal.

**Personas**

- **Project analyst.** Works on a peatland or woodland project, wants to see current condition and change since a baseline date, and export numbers for a report.
- **Developer (you).** Needs to rerun the pipeline for a new site or date, retrain the model and redeploy with minimal manual steps.

**User stories**

- As an analyst, I upload a site boundary (GeoJSON) so the platform tracks that site.
- As an analyst, I see a map of land condition classes for a chosen date.
- As an analyst, I pick two dates and see a change map plus a table of area per class in hectares.
- As an analyst, I download the statistics as CSV and the change map as GeoTIFF.
- As a developer, I trigger a processing run from the command line and watch its status.

**MVP vs stretch**

| Feature | MVP | Stretch |
| --- | --- | --- |
| One pilot site (e.g. a Scottish peatland area) | Yes | Several sites |
| Sentinel-2 imagery + elevation data | Yes | Aerial imagery, LiDAR-derived canopy height |
| 3 to 4 land condition classes | Yes | Finer classes, confidence maps |
| Two-date change detection | Yes | Full time series and trend chart |
| Map dashboard with time selector | Yes | Auth, saved reports, PDF export |
| Manual model retrain via CLI | Yes | Scheduled retraining, model registry |

## 3. System architecture

The system is four stages around shared S3 storage; the model is the core of the project and the other three make it usable.

&#91;embedded content: system architecture · 4 stages, S3 storage, 1 database\]

Data flows along the top row from sources to the model, then back along the middle row to the analyst. The API and tile server read results from S3 and keep metadata in PostGIS.

**Technology choices**

| Layer | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12 (pipeline, ML, API), TypeScript (web) | Python is the role's core language |
| Raster format | Cloud-Optimized GeoTIFF + STAC catalogue | Streams only the pixels needed; industry standard |
| ML | PyTorch with `segmentation_models_pytorch` | Named in the job spec; fast to a working U-Net |
| API | FastAPI | Typed, auto-documented, quick to test |
| Spatial DB | PostgreSQL + PostGIS | Standard spatial queries and boundary validation |
| Tiles | TiTiler | Serves map tiles directly from COGs, no tile pre-generation |
| Map UI | MapLibre GL JS | Open source, handles raster and vector layers |
| Infrastructure | Terraform on AWS | Matches the role's AWS focus; reproducible |

## 4. Component 1: Data pipeline

**Purpose.** Turn a site boundary and a date range into analysis-ready raster stacks the model can read, stored as Cloud-Optimized GeoTIFFs (COGs) and indexed in a STAC catalogue.

**Inputs**

- Site boundary as GeoJSON (EPSG:4326).
- Date range and season window (default May to September, to limit snow and low sun).
- Config file (YAML) with data sources, cloud threshold and output grid.

**Data sources** (verify licences and access before building)

| Data | Source | Use |
| --- | --- | --- |
| Sentinel-2 L2A (10 m, 13 bands) | Copernicus Data Space or the AWS Earth Search STAC | Main model input |
| Elevation | Scottish public-sector LiDAR terrain models, with Copernicus DEM (30 m) as fallback | Elevation, slope, wetness proxies |
| Training labels | NatureScot peatland condition map and National Forest Inventory woodland map | Class labels, see open questions |

**Processing steps**

1. Query the STAC API for scenes intersecting the site and date range.
2. Filter scenes by cloud cover and mask remaining cloud, shadow and snow with the Scene Classification Layer.
3. Build a median composite per season and year to reduce noise.
4. Reproject everything to one grid (EPSG:27700, 10 m) and clip to the site buffer.
5. Derive features: NDVI, NDWI, a moisture index, elevation, slope and a topographic wetness proxy.
6. Write a multi-band COG per site and year, with metadata (sources, scene IDs, processing version).
7. Register outputs in the database and the STAC catalogue.

**Functional requirements**

- Idempotent: rerunning with the same config produces the same output and skips finished steps.
- Runs locally (CLI) and on AWS (container) from the same code.
- Logs scene IDs, versions and parameters for every run so results are reproducible.
- Handles an empty or heavily clouded period by failing clearly, not silently.

**Tech.** Python 3.12, `pystac-client`, `rasterio`, `rioxarray` or `stackstac`, `geopandas`, `numpy`, `typer` for the CLI, `pytest` for tests.

**Output contract.** `s3://<bucket>/composites/<site_id>/<year>/<season>.tif` (COG, float32, band names in metadata) plus a JSON sidecar describing bands and provenance.

## 5. Component 2: Machine learning model

**Purpose.** Classify every 10 m pixel of a composite into a land condition class, then compare two dates to quantify change.

**Task definition**

- Semantic segmentation, one class per pixel.
- Proposed classes: bare or degraded peat, vegetated peat, woodland, water or other. Final list depends on label availability (see open questions).
- Input: 256 by 256 pixel patches (about 2.5 km square) with roughly 12 to 15 channels: Sentinel-2 bands, NDVI, NDWI, elevation and slope.

**Model**

- Baselines first: (a) NDVI threshold rule, (b) per-pixel random forest on the same features.
- Main model: U-Net with a pretrained ResNet encoder (e.g. via `segmentation_models_pytorch`), first layer adapted to the channel count.
- Loss: cross entropy plus Dice, with class weights for imbalance.
- Augmentation: flips, 90 degree rotations, light noise. No colour jitter, since bands are physical measurements.

**Data splits**

- Split by spatial blocks (e.g. 5 km grid cells), never by random patches, to avoid leakage from neighbouring pixels.
- Roughly 60% train, 20% validation, 20% test; the test blocks are never used for tuning.

**Evaluation**

| Metric | Why |
| --- | --- |
| Per-class IoU and F1 | Shows which classes fail, not just the average |
| Mean IoU | Single headline number for comparing runs |
| Confusion matrix | Reveals which classes are mixed up |
| Comparison to both baselines | Shows the deep model earns its complexity |
| Visual check on 3 to 5 test tiles | Catches errors that metrics hide |

**Change detection.** Classify the composite for date A and date B, then build a transition matrix (class at A against class at B) and a change raster. Report area per transition in hectares. Document the known limit: errors in either map compound in the change result.

**Training and tracking**

- Config-driven training script with fixed random seeds.
- Experiment tracking with MLflow (local file store is enough); log config, metrics, git commit and model file.
- Train on a GPU (Colab, a university machine or a short EC2 spot instance); inference runs on CPU.

**Inference and serving**

- Sliding-window inference over a full composite with overlap blending to avoid tile-edge artefacts.
- Exported as TorchScript or ONNX; wrapped in a container that reads a COG and writes a classification COG plus a change COG.
- Model versions are recorded in the database and referenced by every result.

## 6. Component 3: Backend API and database

**Purpose.** Store sites, runs and results, start processing jobs, and serve data and map tiles to the dashboard.

**Stack.** FastAPI, PostgreSQL with PostGIS, SQLAlchemy and GeoAlchemy2, Alembic migrations, Pydantic schemas, and TiTiler to serve map tiles straight from COGs in S3.

**Data model**

| Table | Key fields | Notes |
| --- | --- | --- |
| sites | id, name, boundary (polygon), created\_at | One row per monitored area |
| composites | id, site\_id, year, season, cog\_uri, pipeline\_version | Output of the pipeline |
| models | id, name, version, metrics (JSON), artifact\_uri, git\_commit | One row per trained model |
| runs | id, site\_id, kind, status, params (JSON), started\_at, finished\_at, error | Tracks every processing job |
| classifications | id, composite\_id, model\_id, cog\_uri | Land condition map |
| changes | id, site\_id, from\_composite\_id, to\_composite\_id, model\_id, cog\_uri, stats (JSON) | Change map and area per transition |

**Endpoints (MVP)**

| Method and path | Purpose |
| --- | --- |
| `POST /sites` | Create a site from a GeoJSON boundary |
| `GET /sites` and `GET /sites/{id}` | List sites and show one with available dates |
| `POST /sites/{id}/runs` | Start a pipeline and inference run for a date range |
| `GET /runs/{id}` | Poll run status and errors |
| `GET /sites/{id}/classifications` | List classification maps by date |
| `POST /sites/{id}/changes` | Compute change between two dates |
| `GET /changes/{id}/stats` | Area per transition in hectares (JSON or CSV) |
| `GET /health` | Liveness check for the load balancer |

Map tiles come from TiTiler at `/tiles/{cog}/{z}/{x}/{y}`, not through the API code.

**Requirements**

- Input validation: boundary must be a valid polygon inside the supported region and under a maximum area.
- Long jobs are asynchronous: the API returns a run ID immediately and the job runs on AWS Batch.
- Consistent error format (code, message, detail) and OpenAPI docs generated automatically.
- Secrets come from environment variables or AWS Secrets Manager, never from the repo.
- Stretch: API-key or Cognito authentication.

## 7. Component 4: Web dashboard

**Purpose.** Give an analyst a fast, clear view of site condition and change, with no GIS knowledge needed.

**Stack.** React, TypeScript, Vite, MapLibre GL JS for the map, TanStack Query for data fetching, Recharts for the statistics chart, Vitest and Playwright for tests.

**Screens**

| Screen | What it shows | Key interactions |
| --- | --- | --- |
| Sites list | All sites with name, area and latest run date | Search, create site, open site |
| New site | Boundary upload (GeoJSON) or draw-on-map | Validate shape, preview on map, submit |
| Site map | Base map, site boundary, classification layer for a chosen date | Date selector, layer opacity slider, legend, hover for class name |
| Change view | Side-by-side or swipe comparison of two dates, plus change layer | Pick two dates, toggle layers, click a transition for detail |
| Statistics panel | Area per class and per transition in hectares and percent | Bar chart, sortable table, CSV download |
| Run status | Progress and errors of a processing job | Auto-refresh, retry on failure |

**UX requirements**

- Map loads within about 3 seconds on a normal connection; tiles stream from COGs, with no full raster download.
- Every state is designed: loading skeletons, empty states, and plain-language errors with a next step.
- Colour palette for classes is colour-blind safe, and the legend is always visible.
- Keyboard-reachable controls, visible focus and sensible labels (aim for WCAG AA basics).
- Works at laptop width first; usable on a tablet. Mobile is out of scope for the MVP.

**Acceptance test.** A person who has never seen the tool uploads a boundary, waits for the run, and finds the hectares of woodland gained between two dates in under 5 minutes, without help.

**Hosting.** Static build on S3 behind CloudFront; API and tile URLs set by environment.

## 8. Component 5: AWS infrastructure and CI/CD

**Purpose.** Run the whole system reproducibly in the cloud, defined as code, at a cost a student can afford.

**Services**

| Need | AWS service | Notes |
| --- | --- | --- |
| Raster storage | S3 (raw, composites, results buckets) | Lifecycle rules move old raw data to cheaper storage |
| Heavy jobs (pipeline, inference) | AWS Batch on Fargate, or ECS tasks | Container images in ECR; jobs started by the API |
| API and tile server | ECS Fargate behind an Application Load Balancer | Two small services: API and TiTiler |
| Database | RDS PostgreSQL with PostGIS | Smallest instance class; automated backups on |
| Frontend | S3 + CloudFront | HTTPS, caching |
| Secrets and config | Secrets Manager or SSM Parameter Store | No credentials in code |
| Logs and alarms | CloudWatch | Log groups per service; alarm on failed runs |
| Cost guard | AWS Budgets | Alert at a fixed monthly limit |

**Infrastructure as code.** Terraform with separate `dev` and `prod` workspaces or folders, remote state in S3, least-privilege IAM roles for each service.

**CI/CD (GitHub Actions)**

1. On every pull request: lint (ruff, eslint), type-check (mypy, tsc), run unit tests, build Docker images.
2. On merge to main: push images to ECR, run `terraform plan`, then deploy to `dev` automatically.
3. Promote to `prod` by manual approval.
4. AWS access from Actions uses OIDC federation, not stored keys.

**Cost approach.** Aim for a small monthly bill by stopping the database and ECS services when not demoing, using spot capacity for training, and keeping one pilot site. Set a budget alert on day one. Treat any figure as an estimate until you check AWS pricing.

**Local development.** `docker compose up` starts Postgres/PostGIS, the API, TiTiler and the frontend, with MinIO standing in for S3, so the whole stack runs offline.

## 9. Non-functional requirements and testing

The job spec names Git, tests, the command line and clear code, so these are first-class requirements, not polish.

**Non-functional requirements**

| Area | Requirement |
| --- | --- |
| Reproducibility | Same config and same input produce the same output; every result records data, code and model versions |
| Performance | Pipeline for one pilot site finishes in under 30 minutes; API responses under 500 ms excluding jobs |
| Reliability | Failed jobs report a clear error; retries are safe because steps are idempotent |
| Security | No secrets in Git; least-privilege IAM; private buckets; HTTPS only |
| Maintainability | Typed Python, linted and formatted in CI; modules with one clear job each |
| Documentation | README with setup in under 10 minutes, architecture diagram, and a decision log |

**Testing strategy**

| Layer | What is tested | Tools |
| --- | --- | --- |
| Unit | Index calculations, cloud masking, compositing, window blending, API validation | pytest |
| Data | Output rasters have expected CRS, resolution, bands and no unexpected nodata | pytest on small fixture rasters |
| Model | Shapes and dtypes, a tiny overfit test (loss falls on 8 patches), metric functions on known inputs | pytest, PyTorch |
| Integration | API with a real PostGIS container; pipeline on a tiny sample site | pytest, Docker Compose |
| Frontend | Components and the upload to results flow | Vitest, Playwright |
| Infrastructure | `terraform validate` and `plan` in CI | GitHub Actions |

**Repository layout (monorepo)**

```text
peatwatch/
  pipeline/     # ingestion, compositing, features (Python package + CLI)
  ml/           # datasets, training, evaluation, inference
  api/          # FastAPI app, models, migrations
  web/          # React dashboard
  infra/        # Terraform
  docs/         # architecture, decision log, model card
  .github/      # workflows
  docker-compose.yml
```

**Git practice.** Small commits, short-lived branches, pull requests with CI checks, conventional commit messages, and a tagged release at each milestone.

## 10. Milestones and risks

The plan runs from today (2 November) to the closing date (30 November), with the application sent a few days early.

&#91;embedded content: build plan · 7 workstreams, closing date 30 Nov\]

The model and the API overlap in week two, which is the only way the dashboard can start by 14 November. If time runs short, cut stretch items first and keep the thin end-to-end path working.

**Done when**

| Workstream | Done when |
| --- | --- |
| Setup and CI skeleton | Repo, `docker compose up` and a green CI run exist |
| 1 Pipeline | One command produces a COG composite for the pilot site and year |
| 2 Model | U-Net beats both baselines on held-out test blocks; metrics and confusion matrix saved |
| 3 API and database | All MVP endpoints pass integration tests against PostGIS |
| 4 Dashboard | The 5-minute acceptance test in Section 7 passes |
| 5 AWS deploy | Pushing to main deploys to `dev`; the demo runs on a public URL |
| Docs, demo, polish | README, architecture diagram, model card and a short demo recording |

**Risks**

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Four weeks is tight alongside coursework and work shifts | High | Fix scope at the MVP table in Section 2; protect weekly build blocks; cut AWS extras before cutting the model |
| Training labels are hard to match or licence-restricted | High | Decide the class list in week one; fall back to 2 or 3 classes or a public land cover product |
| Cloud cover in Scotland leaves thin composites | Medium | Use seasonal median composites over several months; report coverage per pixel |
| No GPU available | Medium | Use Colab or a short EC2 spot run; keep the model small and patches modest |
| AWS costs creep up | Medium | Budget alert on day one; stop the database and services between demos |
| Time lost on infrastructure fiddling | Medium | Build locally with Docker Compose first; deploy one service at a time |

**Open questions**

- [ ] Which pilot site (a peatland area with good label coverage and a manageable size)?
- [ ] Which exact label datasets are usable, and under what licence?
- [ ] Where will training run: Colab, a university machine or EC2?
- [ ] Which date pair best shows change for the demo?
