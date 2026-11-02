# Spec: PeatWatch (top-level)

Source: `PRD Peatland Condition Monitoring Platform.md`, revised after review. Module breakdown: `CAPABILITY-MAP.md`.
This spec fixes the project-wide decisions. Per-module detail goes in `SPEC-<module-id>.md`.

## Objective

Build a small end-to-end platform that classifies land condition (peatland and woodland) from Sentinel-2 imagery and elevation data, compares two dates, and shows the result on a web map with area statistics in hectares. It is a portfolio project that mirrors a Junior Software Engineer role (data pipeline, PyTorch model, web dashboard, cloud infrastructure), owned by one person.

**Users**
- Project analyst: views condition and change, exports numbers.
- Developer: reruns the pipeline for a new date or site, retrains, redeploys with few manual steps.

**Non-goals:** carbon credit calculation, regulatory-grade verification, multi-tenant auth or billing, state-of-the-art accuracy.

### Decisions changed from the PRD
| # | Decision | Reason |
|---|---|---|
| D1 | Cloud is **LocalStack only**. Terraform targets real AWS, is validated with `terraform validate` and `plan` in CI, and the LocalStack-compatible subset is applied locally with `tflocal`. | No AWS spend; still demonstrates IaC |
| D2 | AWS Batch/ECS are replaced locally by an **SQS queue plus a worker container**. | Batch/ECS/RDS/CloudFront are likely paid LocalStack features (verify current terms) |
| D3 | Training runs on **Google Colab**. Everything else, including CPU inference, runs locally. Local machine has no NVIDIA GPU. | Hardware check |
| D4 | Train and test on a **wider region** (target a few hundred km2). The demo site is a sub-area **held out from training**. | A single small site gives too few spatial blocks and proves no generalisation |
| D5 | Add a **hand-labelled validation set** (about 100 polygons) alongside the official label maps. | Otherwise we only measure agreement with another map |
| D6 | The **walking skeleton comes first**: NDVI-threshold baseline runs through pipeline, API and dashboard before the random forest and U-Net are built. | A working product even if the U-Net slips |
| D7 | Minimum **API key and rate limit** in the MVP. | `POST /sites` and `/runs` start jobs |
| D8 | Python is **3.13, pinned with `uv`**, not the machine default 3.15. | Colab runs 3.13, so training code and local code match; torch, rasterio, onnxruntime and the rest have 3.13 wheels, while onnxruntime has none for 3.15 |
| D9 | Sentinel-2 L2A has **12 bands**. Classification and change COGs are **`uint8` with an embedded colormap**; composites are `float32`. Source imagery from **AWS Earth Search** (no auth). | PRD corrections |
| D10 | Pilot region, demo site, date pair and class list are decided by the `labels-spike` module before `pipeline` starts. | Labels are the largest risk |
| D11 | Build with an **orchestrator plus parallel module agents** (see "Agent-Driven Development"). The folder becomes a git repo first; each parallel agent works in its own git worktree on its own branch. | Pace, with conflicts prevented by design |

## Tech Stack

Exact versions are pinned in lockfiles (`uv.lock`, `package-lock.json`) when each module is built.

| Layer | Choice |
|---|---|
| Language | Python 3.13 (pipeline, ml, api), TypeScript (web) |
| Packaging | `uv` workspace, one `pyproject.toml` per Python package |
| Raster | `rasterio`, `rioxarray`/`stackstac`, `pystac-client`, `geopandas`, `numpy`; Cloud-Optimized GeoTIFF; STAC |
| ML | PyTorch, `segmentation_models_pytorch`, `scikit-learn` (baseline), MLflow (local file store), ONNX or TorchScript export |
| API | FastAPI, Pydantic v2, SQLAlchemy, GeoAlchemy2, Alembic |
| Database | PostgreSQL + PostGIS (container) |
| Tiles | TiTiler |
| Web | React, TypeScript, Vite, MapLibre GL JS, TanStack Query, Recharts, Vitest, Playwright |
| Cloud (local) | LocalStack: S3, SQS, Secrets Manager, IAM, CloudWatch Logs |
| IaC | Terraform (+ `tflocal`) |
| CI | GitHub Actions |
| Quality | ruff, mypy, eslint, tsc, pytest |

## Commands

All run from the repo root `D:\Projects\Peatland`.

```
Setup:        uv sync --all-packages ; npm --prefix web ci
Stack up:     docker compose up -d          # postgis, localstack, api, worker, titiler, web
Stack down:   docker compose down
Lint:         uv run ruff check . ; uv run ruff format --check . ; npm --prefix web run lint
Type-check:   uv run mypy pipeline ml api ; npm --prefix web run typecheck
Test (py):    uv run pytest --cov=pipeline --cov=ml --cov=api --cov-fail-under=70
Test (web):   npm --prefix web test ; npm --prefix web run e2e
Migrate:      uv run alembic -c api/alembic.ini upgrade head
Pipeline:     uv run peatwatch-pipeline run --site <geojson> --config pipeline/config.yaml
Train (Colab): notebook in ml/notebooks/train.ipynb calls  python -m ml.train --config ml/configs/unet.yaml
Infer:        uv run peatwatch-ml infer --composite <cog> --model <artifact>
Terraform:    terraform -chdir=infra validate ; terraform -chdir=infra plan
Local apply:  tflocal -chdir=infra apply -auto-approve
```

Commands are confirmed and corrected as each module is built.

## Project Structure

Repo root is `D:\Projects\Peatland`.

```
pipeline/    ingestion, compositing, features (Python package + typer CLI)
ml/          datasets, splits, training, evaluation, inference, change detection
api/         FastAPI app, models, schemas, migrations, worker
web/         React dashboard
infra/       Terraform (envs: dev, prod) + LocalStack wiring
contracts/   COG/STAC layout docs, class list + colormap, OpenAPI snapshot, DB schema notes
docs/        architecture, decision log (ADRs), model card
tasks/       plan.md, todo.md, handoffs/<task-id>.md (agent state lives here)
.claude/     agents/<module-id>.md, settings.json (auto-compact env), worktrees/ (git-ignored)
tests/       cross-package integration tests (per-package unit tests live in each package)
.github/     workflows
docker-compose.yml
SPEC.md  CAPABILITY-MAP.md  SPEC-<module-id>.md
```

## Code Style

Typed Python, one clear job per module, small pure functions that are easy to test, no hidden global state. Conventional commits (`feat(pipeline): ...`).

```python
def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """Normalised difference vegetation index; NaN where both bands are zero."""
    denom = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(denom == 0, np.nan, (nir - red) / denom)
    return out.astype(np.float32)
```

- Python: ruff format and lint, mypy strict on new code, `snake_case`, Pydantic models for all boundaries.
- TypeScript: strict mode, function components, `camelCase`, no `any`.
- Comments explain why, not what. Errors are raised with a clear message, never swallowed.

## Testing Strategy

| Layer | What | Tool | Location |
|---|---|---|---|
| Unit | indices, cloud masking, compositing, window blending, API validation, metrics | pytest | `<pkg>/tests/` |
| Data | output CRS, resolution, band names, nodata on small fixture rasters | pytest | `pipeline/tests/` |
| Model | shapes and dtypes; overfit test (loss falls on 8 patches); metric functions on known inputs | pytest, PyTorch | `ml/tests/` |
| Integration | API against real PostGIS container; pipeline on a tiny sample site; queue to worker | pytest, docker compose | `tests/` |
| Frontend | components; upload-to-results flow | Vitest, Playwright | `web/` |
| Infra | `terraform validate` and `plan` | GitHub Actions | `.github/` |

Coverage target: at least 70% on pipeline, ml and api code (enforced in CI). Tests are written before or with the code, per module. Fixture rasters are tiny and committed; real imagery is never committed.

## Boundaries

- **Always:** run lint, type-check and tests before committing; validate all inputs at the API boundary; record data, code and model versions with every result; keep steps idempotent; update the spec when a decision changes.
- **Ask first:** adding dependencies; DB schema changes after `contracts` is approved; changing the class list; changing CI; any step that costs money or needs an account; installing system software (e.g. Terraform).
- **Never:** commit secrets, tokens or real imagery; use random patch splits (spatial blocks only); tune on test blocks; remove or skip failing tests without approval; claim regulatory-grade accuracy; hardcode AWS credentials.

## Success Criteria

Specific and testable:

1. `docker compose up` brings the stack up; `GET /health` returns 200.
2. One command takes a boundary GeoJSON to a composite COG for the pilot region and year, in under 30 minutes, and rerunning it with the same config produces identical output and skips finished steps.
3. An empty or heavily clouded period fails with a clear error, not silently.
4. The U-Net beats both the NDVI-threshold baseline and the random forest on mean IoU on held-out spatial test blocks; per-class IoU, F1 and confusion matrix are saved; 3 to 5 test tiles are visually checked.
5. Accuracy is also reported against the hand-labelled validation set.
6. All MVP endpoints pass integration tests against PostGIS; API responses under 500 ms excluding jobs.
7. Dashboard map loads in about 3 seconds locally; the 5-minute acceptance test passes (new user uploads a boundary, waits for the run, and finds the hectares of woodland gained between two dates without help).
8. CSV statistics and GeoTIFF change map download correctly.
9. Coverage at least 70% on pipeline, ml and api; CI green on every merge; `terraform validate` and `plan` pass; the LocalStack apply succeeds.
10. Public repo with README (setup in under 10 minutes), architecture diagram, ADR log, model card, and a demo GIF or video.

**Cut order if scope must shrink:** infra extras, then dashboard polish, then the U-Net (keep the random forest). The walking skeleton and the evaluation are protected.

## Agent-Driven Development

Verified against current Claude Code docs (sub-agents, context window, env vars, worktrees). Items marked **[test]** are not confirmed by the docs and are checked in `foundation` before we rely on them.

### Roles
- **Orchestrator** (this main session): owns `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`, `contracts/`, shared files, and all merges. It spawns agents, reviews their reports and diffs, and merges branches. It does not write module code itself.
- **Module agents** (one per module id, defined in `.claude/agents/<module-id>.md`): `pipeline`, `ml`, `api`, `web`, `infra`, `docs-demo`. Each gets a narrow system prompt, a restricted tool set, and a path allow-list.
- **Support agents** (already available): `test-engineer`, `code-reviewer`, `security-auditor`. The orchestrator calls them for review, not for building.

### Context rules
- Each agent has **its own context window** and returns only a short final report to the orchestrator. Long work stays out of the orchestrator's context.
- **One task per agent run.** A fresh agent per task keeps context small; compaction is the safety net, not the plan.
- **State lives in files, not memory:** `tasks/todo.md` (status), `tasks/handoffs/<task-id>.md` (what changed, how verified, what is next). A new agent reads the spec section and handoff, not the whole history.
- **Auto-compact at 25%:** set `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25` in the `env` block of project `.claude/settings.json`. Per the docs it applies to the main session and subagents, and it can only lower the default threshold. **[test]** the docs do not say which window the 25% is measured against, and do not confirm that `settings.json` env entries apply to this variable. Verify with `/context` during `foundation`. Subagent frontmatter `autoCompactWindow` has a 100,000-token minimum, so it cannot express 25% of a 200k window; the env variable is the control.
- Before a compaction, the agent writes its progress to its handoff file. **[test]** a `PreCompact` hook can prompt this; its input schema is not confirmed.

### Parallelism rules
A task may run in parallel with another only if **all** hold:
1. Disjoint file sets: no shared path, and neither task edits `contracts/`, lockfiles (`uv.lock`, `package-lock.json`), `pyproject.toml` roots, `docker-compose.yml`, `.github/` or `SPEC.md`. Those are orchestrator-only and edited serially.
2. Neither task needs the other's output (dependency order in `CAPABILITY-MAP.md` is respected). Cross-module work codes against `contracts/` and mocks.
3. Each runs in its **own git worktree** (`isolation: worktree`) on its own branch, based on the current `main`.
4. Each task has acceptance criteria and a verify command that runs inside its own module.

Allowed concurrency: at most **2 module agents at once** while on the Claude Pro plan (usage allowance and merge review load; the platform limit is far higher). We raise it to 3 only after measuring real usage in the first parallel stage. New dependencies are requested in the handoff and added by the orchestrator, not by the agent.

### Model and effort routing (usage budget)
Goal: spend the Claude Pro allowance where quality needs it. Usage on Pro is limited and, as I understand it, shared between claude.ai and Claude Code; exact limits are not in this spec. `foundation` includes a task to read real usage (usage tool or `/usage`) so the numbers below can be tuned.

An agent cannot switch its own model mid-run. "Intelligent" routing means the **orchestrator picks a model and effort per task** from the rules below, writes them in a `Model / Effort` column in `tasks/todo.md`, and passes them when it spawns the agent. Each agent definition also carries a sensible default in its frontmatter (`model`, `effort`).

| Tier | Model | Effort | Use for |
|---|---|---|---|
| Light | Haiku 5.5 | low | Scaffolding, config files, fixtures, formatting and lint fixes, README and docstring drafts, renaming, simple searches |
| Standard (default) | Sonnet 5.5 | medium | Module implementation (pipeline, api, web, infra), writing tests, routine reviews |
| Heavy | Opus 5.5 | high | `contracts` design, label and leakage analysis, evaluation design for `ml`, security review, debugging that Sonnet failed twice |
| Not used | Fable 5.1 | n/a | Only if you ask for it; likely too costly for Pro |

Rules:
1. **Start low, escalate on evidence.** A task that fails its verify step twice moves up one tier. A task that was easy moves down next time.
2. **The orchestrator stays on Sonnet.** It reads reports, not code.
3. **Opus tasks are rare and short:** a written brief, one deliverable, no exploratory browsing.
4. **Cheap context habits:** one task per agent, specs loaded by section, agents read handoff files rather than long histories, reports kept under about 200 words.
5. **Work in bursts.** Plan around the rolling usage window: queue heavy tasks for when allowance is fresh, and stop spawning when it runs low rather than starting work that cannot finish.
6. **Free compute stays free:** model training uses Colab, not agent tokens; Docker, tests and linters run locally.

### Merge protocol
1. Agent finishes: tests, lint and type-check pass inside its worktree; it writes the handoff file and returns a summary.
2. Orchestrator reviews the diff (with `code-reviewer` for non-trivial changes), confirms only allowed paths changed, then merges the branch into `main` with a conventional commit referencing the task id.
3. Orchestrator runs the full suite on `main`. If it fails, the merge is reverted and the task reopens.
4. Docs are silent on how worktree branches are reported back, so the agent always states its branch name and worktree path in its report. **[test]**

### Parallel windows (from the build order)
| Stage | Runs in parallel | Serial gate before it |
|---|---|---|
| 0 | `foundation` and `labels-spike` | none |
| 1 | `contracts` (orchestrator) | stage 0 |
| 2 | `pipeline` internals split by file (indices, cloud mask, compositing, STAC query) | `contracts` approved |
| 3 | `api`, `ml` baselines, `web` against mocked API | `pipeline` walking skeleton merged |
| 4 | `ml` U-Net (Colab, human-run), `web` polish, `infra` | stage 3 merged |
| 5 | `docs-demo` | everything merged |

Human-only steps (agents cannot do them): running Colab training and copying back the model artifact; approving installs; confirming the demo site.

### Guardrails
- Each module agent's `tools` and path scope are restricted in its definition. **[test]** a `PreToolUse` hook that blocks writes outside the agent's directories if the definition alone does not enforce it.
- Agents run with normal permission prompts (`default` or `acceptEdits`). We do not use `bypassPermissions`.
- Set `maxTurns` on each agent so a stuck run ends and reports.
- Spawn depth stays at 1: module agents do not spawn further agents.

## Open Questions

Resolved by `labels-spike` (before `pipeline`):
- [ ] Pilot region and held-out demo site.
- [ ] Which label datasets are usable, and under what licence; final class list (3 to 4 classes).
- [ ] Demo date pair that shows real change (felling, planting, fire or rewetting).

Resolved during build:
- [ ] Does `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25` work from `settings.json`, and what window is the 25% measured against?
- [ ] Do several Agent calls in one message run in parallel, and how are worktree branches reported back?
- [ ] Is the free LocalStack tier enough for the services we use under its current terms?
- [ ] Does Colab provide enough GPU time for the chosen patch size and model?
- [ ] Is Scottish LiDAR available for the region, or do we use Copernicus DEM only?
