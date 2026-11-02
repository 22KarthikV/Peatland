# Task List: PeatWatch

Legend. **Model/Effort:** H = Haiku low, S = Sonnet medium, S-lo = Sonnet low, O = Opus high. **Par:** tasks sharing a group letter in the same stage may run together (own worktree each, max 2 at once); `-` = serial. **Size:** XS 1 file, S 1-2, M 3-5. Verify commands are from `SPEC.md`; "(h)" marks a human step.
Every task: write a handoff in `tasks/handoffs/<id>.md`, keep lint, type-check and tests green, touch only the listed files.

---
## Stage 0: Foundation and Labels (two chains in parallel)

### Foundation chain (agent `foundation`)
- [x] **F1 Git repo and Python workspace** | H | - | S
  - Do: `git init`, `.gitignore` (incl. `.claude/worktrees/`, data, `.env`), `.python-version` 3.13, root `pyproject.toml` uv workspace with empty `pipeline`, `ml`, `api` members, README stub, first commit. (h) approve installing `uv` if missing.
  - Accept: `uv sync --all-packages` succeeds on Python 3.13; first commit exists.
  - Verify: `uv run python --version` prints 3.13.x; `git log --oneline` shows one commit.
  - Files: `.gitignore`, `.python-version`, `pyproject.toml`, `README.md`, 3 package `pyproject.toml`s (setup counts as one)
  - Deps: none
- [x] **F2 Docker Compose: PostGIS + LocalStack** | S | - | M
  - Do: compose with PostGIS and LocalStack (S3, SQS, Secrets Manager), healthchecks, init script creating buckets and queue. Document which LocalStack services work on the current free terms.
  - Accept: `docker compose up -d` healthy; bucket and queue exist; PostGIS answers `SELECT postgis_version()`; findings written to `docs/decisions/0001-localstack-scope.md`.
  - Verify: `docker compose up -d` then `docker compose ps` all healthy; script `scripts/smoke_stack.sh` passes.
  - Files: `docker-compose.yml`, `infra/localstack/init.sh`, `scripts/smoke_stack.sh`, ADR
  - Deps: F1
- [x] **F3 Quality tooling and CI skeleton** | S | A with F4 | M
  - Do: ruff, mypy, pytest + coverage config; GitHub Actions workflow running lint, type-check, tests on PR; one placeholder test per package. (h) create GitHub remote.
  - Accept: local lint/type/test pass; workflow file valid.
  - Verify: `uv run ruff check . ; uv run mypy pipeline ml api ; uv run pytest`; `actionlint` or push and see green run.
  - Files: `.github/workflows/ci.yml`, `pyproject.toml` (ruff/mypy/pytest sections), placeholder tests
  - Deps: F1
- [x] **F4 Agent definitions and settings** | S | A with F3 | M
  - Do: `.claude/agents/{pipeline,ml,api,web,infra,docs-demo}.md` (narrow prompt, tools, `model`, `effort`, `maxTurns`, path allow-list, report under 200 words); `.claude/settings.json` env `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25`; handoff template `tasks/handoffs/TEMPLATE.md`. (h) approve the settings change.
  - Accept: six agent files parse; settings valid JSON; template exists.
  - Verify: list agents in the app shows all six; `python -m json.tool .claude/settings.json`.
  - Files: `.claude/agents/*.md`, `.claude/settings.json`, `tasks/handoffs/TEMPLATE.md`
  - Deps: F1
- [x] **F5 Harness smoke test and usage check** | S | - | S
  - Do: spawn two trivial Haiku agents in two worktrees at once (each adds a file in its own folder); confirm they run in parallel, how branch names come back, and that merge works. Test whether auto-compact at 25% takes effect (`/context`). Record real Pro usage and which models are available.
  - Accept: findings in `docs/decisions/0002-agent-harness.md`; SPEC `[test]` items updated to confirmed/failed; cap and tiers adjusted if needed.
  - Verify: both worktree branches merged cleanly; ADR written.
  - Files: ADR, `SPEC.md` (orchestrator edit)
  - Deps: F1, F3, F4

### Labels chain (agent `labels`, no code)
- [x] **L1 Research candidate regions and label data** | S | A with F-chain | M
  - Do: shortlist 3 Scottish regions; for each: Sentinel-2 coverage and cloud, LiDAR/DEM availability, candidate label datasets (peatland condition, land cover, NFI woodland), licence and access, resolution/coarseness, example change events (felling, planting, fire, rewetting) with dates.
  - Accept: `docs/labels/candidates.md` with a comparison table and source links; claims marked verified or unverified.
  - Verify: orchestrator spot-checks 3 links/claims.
  - Files: `docs/labels/candidates.md`
  - Deps: none
- [x] **L2 Decide region, classes, demo site and date pair** | O | - | S
  - Do: pick training region (few hundred km2), held-out demo site, 3-4 classes mapped to label sources, date pair with real change; write the ADR; propose fallback.
  - Accept: ADR `docs/decisions/0003-site-and-classes.md`. (h) you confirm.
  - Verify: you approve in chat.
  - Files: ADR
  - Deps: L1
- [x] **L3 Hand-labelling protocol and validation set** | S | - | S
  - Do: protocol and a small QGIS/GeoJSON workflow for ~100 polygons across classes inside the test blocks; empty template file. (h) you label (or part).
  - Accept: `docs/labels/protocol.md`, `data/labels/handlabel.geojson` template with schema.
  - Verify: file loads in `geopandas`; schema test passes.
  - Files: protocol, template GeoJSON, schema test
  - Deps: L2

### Checkpoint 0
- [ ] `docker compose up -d` healthy; CI green; agents defined; harness behaviour recorded; site and classes confirmed by you.

---
## Stage 1: Contracts (orchestrator, serial)

- [x] **C1 Class list, colormap, band and feature order** | S | - | S
  - Accept: `contracts/classes.yaml` (ids, names, colour-blind-safe hex, nodata value), `contracts/bands.yaml` (12 S2 bands + features, order, units); a validator test.
  - Verify: `uv run pytest contracts`
  - Files: `contracts/classes.yaml`, `contracts/bands.yaml`, `contracts/tests/test_contracts.py`
  - Deps: L2
- [x] **C2 COG, STAC and S3 layout** | O | - | M
  - Accept: key layout `composites/<site>/<year>/<season>.tif` plus classification and change paths; float32 composite, uint8 classification with embedded colormap; JSON sidecar schema (bands, scene ids, versions, parameters); STAC item template; idempotency key definition.
  - Verify: JSON Schema validates a sample sidecar; reviewer pass.
  - Files: `contracts/cog-layout.md`, `contracts/sidecar.schema.json`, `contracts/stac-item.example.json`
  - Deps: C1
- [x] **C3 DB schema and OpenAPI** | O | - | M
  - Accept: tables per PRD (sites, composites, models, runs, classifications, changes) with types, FKs, indexes; run states; draft `contracts/openapi.yaml` with all MVP endpoints, error format `{code,message,detail}`, API-key header, rate-limit response.
  - Verify: `openapi-spec-validator contracts/openapi.yaml`
  - Files: `contracts/db-schema.md`, `contracts/openapi.yaml`
  - Deps: C2
- [x] **C4 Queue message contract and mock fixtures** | S | - | M
  - Accept: SQS job message schema (kind, site, dates, run id, model); JSON fixtures for every endpoint response (for web and api mocks); sample tiny COG fixtures listed.
  - Verify: fixtures validate against `openapi.yaml`.
  - Files: `contracts/queue.schema.json`, `contracts/fixtures/*.json`
  - Deps: C3

### Checkpoint 1 (gate)
- [x] You review and approve `contracts/`. Nothing in Stage 2+ starts before this.

---
## Stage 2: Pipeline (agent `pipeline`)

- [x] **P1 Package skeleton, CLI and config** | S | - | M
  - Accept: `peatwatch-pipeline run --help`; YAML config loaded into a Pydantic model; structured run log writes scene ids, versions, parameters.
  - Verify: `uv run pytest pipeline -k "config or cli"`
  - Files: `pipeline/pyproject.toml`, `pipeline/src/pipeline/{cli,config,log}.py`, tests
  - Deps: CP1
- [x] **P2 Indices and features** | S | B | S
  - Accept: NDVI, NDWI, moisture index, slope, topographic wetness proxy as pure float32 functions; NaN-safe; known-value tests.
  - Verify: `uv run pytest pipeline/tests/test_features.py`
  - Files: `pipeline/src/pipeline/features.py`, test
  - Deps: P1
- [x] **P3 Cloud, shadow and snow mask** | S | B | S
  - Accept: SCL-based mask function; tests with synthetic SCL arrays for each class.
  - Verify: `uv run pytest pipeline/tests/test_mask.py`
  - Files: `pipeline/src/pipeline/mask.py`, test
  - Deps: P1
- [x] **P4 STAC query and scene filter** | S | B | M
  - Accept: Earth Search query by boundary and window; cloud-cover filter; clear error when zero usable scenes; recorded-response test, no network in tests.
  - Verify: `uv run pytest pipeline/tests/test_stac.py`
  - Files: `pipeline/src/pipeline/stac.py`, test, recorded fixture
  - Deps: P1
- [x] **P5 Compositing and grid** | S | C | M
  - Accept: per-pixel median composite; coverage band (count of valid obs); reproject to EPSG:32630 10 m (contract C2) and clip to site buffer; fixture-raster tests for CRS, resolution, no unexpected nodata.
  - Verify: `uv run pytest pipeline/tests/test_composite.py`
  - Files: `pipeline/src/pipeline/composite.py`, `grid.py`, test
  - Deps: P3
- [x] **P6 Elevation ingestion** | S | C | M
  - Accept: Copernicus DEM (and LiDAR if L1 found it) resampled to grid; slope and TWI derived via P2.
  - Verify: `uv run pytest pipeline/tests/test_elevation.py`
  - Files: `pipeline/src/pipeline/elevation.py`, test, fixture
  - Deps: P2
- [x] **P7 COG writer, sidecar and idempotency** | S | - | M
  - Accept: writes valid COG with band names; sidecar validates against contract; same config rerun skips and yields identical hash; uploads to LocalStack S3.
  - Verify: `uv run pytest pipeline/tests/test_output.py`; `rio cogeo validate` on output.
  - Files: `pipeline/src/pipeline/output.py`, `store.py`, test
  - Deps: P4, P5, P6
- [ ] **P8 End-to-end run on tiny site (WALKING SKELETON, part 1)** | S | - | M
  - Accept: CLI runs STAC (recorded) -> composite COG on a tiny fixture site; integration test with LocalStack; a timed run on the pilot region finishes under 30 minutes (h confirm).
  - Verify: `uv run pytest tests -k pipeline_e2e`; `uv run peatwatch-pipeline run --site <geojson> --config pipeline/config.yaml`
  - Files: `tests/test_pipeline_e2e.py`, `pipeline/config.yaml`, CLI glue
  - Deps: P7

### Checkpoint 2
- [ ] One command produces a validated COG composite for the pilot region; reruns skip; empty period fails clearly; coverage >= 70% on `pipeline`.

---
## Stage 3: ML baselines, API and web on mocks

### ML (agent `ml`)
- [ ] **M1 Package skeleton, patch dataset, label rasterisation** | S | - | M
  - Accept: 256x256 patches from composites; labels rasterised to class ids per contract; nodata handled.
  - Verify: `uv run pytest ml/tests/test_dataset.py`
  - Files: `ml/pyproject.toml`, `ml/src/ml/{dataset,labels}.py`, test
  - Deps: CP2
- [ ] **M2 Spatial block split with buffer** | O | A with M3 | S
  - Accept: 5 km blocks, ~60/20/20, buffer between splits; test proves no patch overlaps across splits; demo site fully held out (D4).
  - Verify: `uv run pytest ml/tests/test_split.py`
  - Files: `ml/src/ml/split.py`, test
  - Deps: M1
- [ ] **M3 Metrics** | H | A with M2 | S
  - Accept: per-class IoU, F1, mean IoU, confusion matrix; known-input tests.
  - Verify: `uv run pytest ml/tests/test_metrics.py`
  - Files: `ml/src/ml/metrics.py`, test
  - Deps: M1
- [ ] **M4 NDVI-threshold baseline and eval harness** | S | - | M
  - Accept: rule-based classifier + harness producing metrics JSON on the test blocks.
  - Verify: `uv run pytest ml/tests/test_baseline_ndvi.py`
  - Files: `ml/src/ml/baseline_ndvi.py`, `evaluate.py`, test
  - Deps: M2, M3
- [ ] **M5 Random-forest baseline** | S | B (with M6) | S
  - Accept: per-pixel RF on same features, saved model, metrics on test blocks.
  - Verify: `uv run pytest ml/tests/test_baseline_rf.py`
  - Files: `ml/src/ml/baseline_rf.py`, test
  - Deps: M4
- [ ] **M6 Sliding-window inference and classification COG** | S | B | M
  - Accept: overlap-blended windows; uint8 classification COG with embedded colormap; blending test shows no edge seams; works with any model object (baseline first).
  - Verify: `uv run pytest ml/tests/test_inference.py`; `rio cogeo validate`
  - Files: `ml/src/ml/infer.py`, `cog.py`, test
  - Deps: M4
- [ ] **M7 Change detection and statistics** | S | - | M
  - Accept: transition matrix, change COG, hectares and percent per transition, CSV/JSON output; known-input test.
  - Verify: `uv run pytest ml/tests/test_change.py`
  - Files: `ml/src/ml/change.py`, `stats.py`, test
  - Deps: M6

### API (agent `api`)
- [ ] **A1 App skeleton, health and error format** | S | - | M
  - Accept: FastAPI app, `GET /health`, uniform error handler, settings from env, Dockerfile, OpenAPI served and matching `contracts/openapi.yaml` for implemented routes.
  - Verify: `uv run pytest api/tests/test_health.py`
  - Files: `api/pyproject.toml`, `api/src/api/{main,errors,settings}.py`, `api/Dockerfile`
  - Deps: CP1
- [ ] **A2 Models, Alembic migration, PostGIS test fixture** | S | - | M
  - Accept: tables per contract; `alembic upgrade head` on empty DB; test fixture starts real PostGIS.
  - Verify: `uv run pytest api/tests/test_db.py`
  - Files: `api/src/api/db/{models,session}.py`, `api/alembic/*`, test
  - Deps: A1
- [ ] **A3 Sites endpoints with boundary validation** | S | - | M
  - Accept: `POST /sites`, `GET /sites`, `GET /sites/{id}`; rejects invalid polygon, outside region, over max area; 400s use error format.
  - Verify: `uv run pytest api/tests/test_sites.py`
  - Files: `api/src/api/routes/sites.py`, `validation.py`, schemas, test
  - Deps: A2
- [ ] **A4 API key and rate limit** | S | - | S
  - Accept: key checked via header (hash stored); 401 without; 429 over limit; keys from env/Secrets Manager.
  - Verify: `uv run pytest api/tests/test_auth.py`
  - Files: `api/src/api/auth.py`, `ratelimit.py`, test
  - Deps: A3
- [ ] **A5 Runs endpoints and enqueue** | S | - | M
  - Accept: `POST /sites/{id}/runs` creates run and SQS message (LocalStack), returns run id; `GET /runs/{id}`.
  - Verify: `uv run pytest api/tests/test_runs.py`
  - Files: `api/src/api/routes/runs.py`, `queue.py`, test
  - Deps: A4
- [ ] **A6 Worker container** | S | - | M
  - Accept: worker consumes queue, updates run status, calls pipeline then inference (pluggable, mock in tests); failure writes error and status.
  - Verify: `uv run pytest api/tests/test_worker.py`
  - Files: `api/src/api/worker.py`, `api/Dockerfile.worker`, test
  - Deps: A5, P8 (real call wired after CP2; mock until then)
- [ ] **A7 Classifications, changes, stats and downloads** | S | - | M
  - Accept: list classifications by date; `POST /sites/{id}/changes`; `GET /changes/{id}/stats` JSON and CSV; change GeoTIFF download link.
  - Verify: `uv run pytest api/tests/test_changes.py`
  - Files: `api/src/api/routes/{classifications,changes}.py`, test
  - Deps: A6, M7 (mock until then)
- [ ] **A8 TiTiler service** | S | - | S
  - Accept: TiTiler in compose reads COGs from LocalStack; colormap renders classification; CORS set for the web origin.
  - Verify: `curl` a tile returns 200 PNG; `scripts/smoke_tiles.sh`
  - Files: `docker-compose.yml` (orchestrator edit), `infra/titiler/config`, script
  - Deps: A1, P7

### Web (agent `web`)
- [ ] **W1 Skeleton, typed API client and mock server** | S | A with API/ML tasks | M
  - Accept: Vite + React + TS strict; ESLint; Vitest; API client generated from `openapi.yaml`; MSW serves `contracts/fixtures`; Dockerfile.
  - Verify: `npm --prefix web run lint ; npm --prefix web run typecheck ; npm --prefix web test`
  - Files: `web/*` config, `web/src/api/*`, `web/src/mocks/*`
  - Deps: CP1
- [ ] **W2 Sites list and new site** | S | - | M
  - Accept: list with search, empty/loading/error states; upload GeoJSON with validation, preview, submit.
  - Verify: `npm --prefix web test -- sites`
  - Files: `web/src/pages/{Sites,NewSite}.tsx`, components, tests
  - Deps: W1
- [ ] **W3 Site map** | S | - | M
  - Accept: MapLibre base map (key-free source), boundary, classification layer via tile URL, date selector, opacity slider, always-visible colour-blind-safe legend, hover class name.
  - Verify: `npm --prefix web test -- map`
  - Files: `web/src/pages/SiteMap.tsx`, `components/{Legend,DateSelect}.tsx`, test
  - Deps: W2
- [ ] **W4 Change view** | S | - | M
  - Accept: pick two dates, side-by-side or swipe, change layer toggle, click transition for detail.
  - Verify: `npm --prefix web test -- change`
  - Files: `web/src/pages/Change.tsx`, components, test
  - Deps: W3
- [ ] **W5 Statistics panel** | S | - | M
  - Accept: area per class/transition in ha and %, sortable table, Recharts bar chart, CSV download.
  - Verify: `npm --prefix web test -- stats`
  - Files: `web/src/components/Stats*.tsx`, test
  - Deps: W4
- [ ] **W6 Run status and accessibility pass** | S | - | M
  - Accept: progress view auto-refresh, retry on failure; keyboard reachable, visible focus, labels; axe checks pass.
  - Verify: `npm --prefix web test -- run` and axe test
  - Files: `web/src/pages/RunStatus.tsx`, a11y test
  - Deps: W5

### Checkpoint 3 (gate)
- [ ] Walking skeleton works locally: upload boundary -> run (baseline "model") -> map -> change stats; `docker compose up` brings it all up; coverage >= 70%; you review it in the browser.

---
## Stage 4: Real model, E2E and infra

- [ ] **M8 U-Net training script** | S (escalate to O) | A with I-tasks | M
  - Accept: config-driven, seeded; `segmentation_models_pytorch` U-Net with adapted first layer; CE+Dice with class weights; augmentation without colour jitter; MLflow logging; overfit-on-8-patches test; resumable checkpoints.
  - Verify: `uv run pytest ml/tests/test_train.py`
  - Files: `ml/src/ml/train.py`, `model.py`, `configs/unet.yaml`, test
  - Deps: CP3
- [ ] **M9 Colab notebook and training run (human)** | H | - | S
  - Accept: `ml/notebooks/train.ipynb` pulls data, trains, saves model to Drive. (h) you run it and place the artifact under `artifacts/`.
  - Verify: artifact loads on CPU.
  - Files: notebook, `docs/runbooks/colab.md`
  - Deps: M8
- [ ] **M10 Evaluation report** | O | - | M
  - Accept: U-Net vs both baselines on test blocks and the hand-labelled set; per-class IoU/F1, confusion matrix, 3-5 visual tiles; honest limits section; leakage re-check.
  - Verify: report reviewed by you; numbers reproducible via `uv run peatwatch-ml evaluate`.
  - Files: `docs/model-card.md` (draft), `docs/eval/*.md`, figures
  - Deps: M9, L3
- [ ] **M11 Export and CPU parity** | S | - | S
  - Accept: ONNX or TorchScript export; CPU output matches PyTorch within tolerance; model row registered (version, metrics, git commit).
  - Verify: `uv run pytest ml/tests/test_export.py`
  - Files: `ml/src/ml/export.py`, test
  - Deps: M10
- [ ] **A9 Wire real pipeline and model into worker** | S | - | S
  - Accept: worker uses real `pipeline` and exported model; end-to-end run on the pilot region.
  - Verify: `uv run pytest tests -k full_e2e`
  - Files: worker config, integration test
  - Deps: M11
- [ ] **W7 Playwright E2E and usability test** | S | - | M
  - Accept: upload -> run -> view -> download flow passes in CI; (h) 5-minute test with a new user passes.
  - Verify: `npm --prefix web run e2e`
  - Files: `web/e2e/*`, `docs/usability.md`
  - Deps: A9
- [ ] **I1 Terraform core resources** | S | A with ML | M
  - Accept: providers, S3 state backend, dev/prod folders, buckets with lifecycle, SQS, Secrets Manager, least-privilege IAM; applies to LocalStack via `tflocal`. (h) approve installing Terraform and tflocal.
  - Verify: `terraform -chdir=infra validate`; `tflocal -chdir=infra apply -auto-approve`
  - Files: `infra/*.tf` (up to 5 files)
  - Deps: CP3
- [ ] **I2 AWS-only resources (validate only)** | S | - | M
  - Accept: ECS Fargate services, ALB, RDS PostGIS, ECR, CloudFront, Budgets, CloudWatch alarm on failed runs, defined and passing `validate` and `plan`; not applied to LocalStack.
  - Verify: `terraform -chdir=infra validate`; `plan` output saved.
  - Files: `infra/{ecs,rds,cdn,budget,alarms}.tf`
  - Deps: I1
- [ ] **I3 Terraform in CI and LocalStack smoke** | S | - | S
  - Accept: CI runs `validate` and `plan`; script applies subset to LocalStack and checks outputs. No real AWS credentials anywhere; OIDC role documented, not used.
  - Verify: CI green; `scripts/smoke_infra.sh`
  - Files: `.github/workflows/infra.yml` (orchestrator edit), script
  - Deps: I2
- [ ] **I4 Production images** | S | - | M
  - Accept: multi-stage, non-root images for api, worker, web; CI builds them; sizes recorded.
  - Verify: `docker build` for each in CI.
  - Files: Dockerfiles, `.dockerignore`
  - Deps: A9, W7

### Checkpoint 4
- [ ] U-Net beats both baselines on held-out blocks (or the honest result is documented and the best model chosen); full local stack runs the real model; CI including infra is green.

---
## Stage 5: Docs, demo and final review

- [ ] **D1 Architecture diagram and README** | S | A with D4 | M
  - Accept: Mermaid architecture diagram; README setup works on a clean clone in under 10 minutes (timed, h).
  - Verify: fresh clone follows README only.
  - Files: `README.md`, `docs/architecture.md`
  - Deps: CP4
- [ ] **D2 Decision log and model card** | S | - | S
  - Accept: ADRs indexed; model card finalised from M10 (data, metrics, limits, intended use).
  - Verify: reviewer pass.
  - Files: `docs/decisions/README.md`, `docs/model-card.md`
  - Deps: D1
- [ ] **D3 Demo recording (human)** | H | - | S
  - Accept: script written by agent; (h) you record the GIF/video; embedded in README.
  - Files: `docs/demo-script.md`, `docs/demo.gif`
  - Deps: W7
- [ ] **D4 Final reviews** | O (security), S (code, tests) | A with D1 | -
  - Accept: `code-reviewer`, `security-auditor`, `test-engineer` reports; critical/high issues fixed; no secrets in history (`git log` scan).
  - Verify: reports in `docs/reviews/`; full suite green.
  - Deps: CP4

### Checkpoint 5 (done)
- [ ] All 10 success criteria in `SPEC.md` met or explicitly documented as not met; tagged release; public repo ready.
