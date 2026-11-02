# Implementation Plan: PeatWatch

Source of truth: `SPEC.md` (approved) and `CAPABILITY-MAP.md`. Task list: `tasks/todo.md` (default target; no external tracker).
Task ids use the module prefix: F foundation, L labels-spike, C contracts, P pipeline, M ml, A api, W web, I infra, D docs-demo.

## Overview

Deliver the PeatWatch MVP: Sentinel-2 + elevation pipeline, a segmentation model with two baselines, a FastAPI + PostGIS backend with a job queue, a MapLibre dashboard, and Terraform for AWS exercised on LocalStack. Built by an orchestrator session that spawns module agents (max 2 at once) in separate git worktrees, with the model tier chosen per task.

## Architecture decisions (from SPEC.md, restated for the plan)

- Walking skeleton first: NDVI-threshold "model" runs pipeline -> API -> dashboard before RF and U-Net exist. The U-Net later drops in behind the same inference interface.
- Contracts are written once, early, by the orchestrator on Opus. Every module codes against `contracts/` and mocks, which is what makes parallel work safe.
- Orchestrator-only files (never edited by module agents): `contracts/`, `SPEC.md`, `tasks/*.md`, lockfiles, root `pyproject.toml`, `docker-compose.yml`, `.github/`.
- Module agents own exactly one directory each (`pipeline/`, `ml/`, `api/`, `web/`, `infra/`, `docs/`).

## Dependency graph

```
F1 git/uv ──┬─ F2 compose ─┬─ F3 CI ─────────────────────────────┐
            │              └─ F4 agents ─ F5 harness test        │
L1 ─ L2 (site, classes, dates; human confirms) ─ L3 labels       │
            └────────────── C1 classes ─ C2 COG/STAC ─ C3 DB/API ─ C4 queue+mocks
                                                │
                       P1 ─┬─ P2 ─ P6 ─┐        │
                           ├─ P3 ─ P5 ─┼─ P7 ─ P8 (SKELETON: baseline end to end)
                           └─ P4 ──────┘
         ┌─────────────────────────────┴───────────────┐
   A1..A8 (api, mocks until P8)         M1..M7 (ml, baselines, inference, change)   W1..W7 (web on mocks)
                                                │
                                   M8 ─ M9(Colab, human) ─ M10 eval ─ M11 export
                                                │
                                   I1..I4 (infra)   ->   D1..D4 (docs, final reviews)
```

## Stages and parallel windows

Rules from the spec apply: tasks run together only with disjoint files, no output dependency, own worktree. Maximum 2 module agents at once. Orchestrator does contracts and all merges.

| Stage | Tasks | Can run together | Gate to enter |
|---|---|---|---|
| 0 | F1 -> F2, F3, F4 -> F5; L1 -> L2 -> L3 | `foundation` chain and `labels-spike` chain (2 agents) | none |
| 1 | C1 -> C2 -> C3 -> C4 | serial (orchestrator, Opus) | CP0, L2 confirmed by you |
| 2 | P1; then P2, P3, P4; then P5, P6; then P7, P8 | P2 ‖ P3 ‖ P4 (2 at a time); P5 ‖ P6 | CP1 contracts approved |
| 3 | A1-A8, M1-M7, W1-W7 | api ‖ ml; web on mocks after A1 | CP2 skeleton merged (P8) for ml; api and web can start on mocks after CP1 |
| 4 | M8-M11, W polish, I1-I4 | ml (Colab, human) ‖ infra ‖ web | CP3 |
| 5 | D1-D4 | docs ‖ final reviews | CP4 |

Note: `api` and `web` do not strictly wait for the pipeline skeleton because they code against mocks. To protect the allowance, we still start them one at a time unless usage numbers (F5) show headroom.

## Model and effort routing (summary)

Column `Model/Effort` in `todo.md`: `H` = Haiku 5.5 low, `S` = Sonnet 5.5 medium, `S-lo` = Sonnet low, `O` = Opus 5.5 high. Default is `S`. A task that fails its verify step twice escalates one tier. Rules and rationale: `SPEC.md`, "Model and effort routing".

## Human gates

1. Confirm pilot region, class list and demo date pair (end of L2).
2. Approve contracts (CP1).
3. Run Colab training and copy the model artifact back (M9).
4. Approve installs: `uv`, Terraform, `tflocal`, any new dependency.
5. Create the GitHub remote and approve CI/OIDC-related settings (nothing deploys to real AWS).
6. Run the 5-minute usability test with someone who has not seen the tool (W7) and record the demo (D3).

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Labels unusable at 10 m or licence-restricted | High | L1-L2 first; fall back to a land-cover product plus NFI woodland; hand-labelled set (L3) |
| No real change at the demo date pair | High | L2 must find a documented event (felling, planting, fire, rewetting); fall back to a synthetic-change test for the pipeline and an honest note |
| Pro allowance runs out mid-stage | High | Routing table, 2-agent cap, small tasks, handoff files, bursts; F5 measures real usage |
| Parallel merge conflicts | Medium | Disjoint-file rule, worktrees, orchestrator-only shared files, full suite on `main` after each merge |
| LocalStack free tier lacks a service | Medium | F2 checks terms and services; SQS worker replaces Batch; validate-only for AWS-only resources (I2) |
| Auto-compact / worktree behaviour differs from docs | Medium | F5 tests each uncertain item before we rely on it |
| Colab session limits | Medium | Small model and patches; checkpoint to Drive; resumable training (M8) |
| Python 3.13 vs machine default 3.15 | Low | F1 pins 3.13 via `uv` (matches Colab); F2 checks Linux wheels and the `python:3.13` image; keep `ml` compatible with Colab's pandas 2.2.x |

## Open questions

- Pilot region, demo site, date pair, class list (resolved by L2).
- Is LiDAR available for the region, or Copernicus DEM only (P6)?
- Free base-map tile source for the dashboard that needs no API key (W3).
- Actual Pro usage limits and which models Claude Code can use on Pro (F5).
