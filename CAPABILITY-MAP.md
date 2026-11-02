# Capability Map: PeatWatch

Status: **approved** (2026-11-02). Module ids are stable and must not be renamed.
Each module gets its own `SPEC-<module-id>.md` next to this file, written in build order.

| Module id | Responsibility | Depends on |
|---|---|---|
| `foundation` | Repo, docker-compose (PostGIS, LocalStack), CI skeleton, lint, type-check | none |
| `labels-spike` | Choose pilot region and demo site, check label feasibility and licences, fix the class list | none |
| `contracts` | COG and STAC layout, DB schema, OpenAPI, class list and colormap | `foundation` |
| `pipeline` | STAC ingest, cloud mask, composite, features, COG output | `contracts`, `labels-spike` |
| `ml` | Baselines, U-Net, spatial splits, evaluation, MLflow, inference, change detection | `pipeline`, `contracts` |
| `api` | FastAPI, PostGIS, SQS job queue and worker, TiTiler | `contracts` (mocks `ml` and `pipeline` until ready) |
| `web` | Map, change view, statistics, run status | `api` contract |
| `infra` | Terraform for AWS, applied to LocalStack, validated in CI | `api`, `pipeline` images |
| `docs-demo` | README, architecture diagram, model card, demo GIF | all |

Build order: `foundation` + `labels-spike` -> `contracts` -> `pipeline` (baseline end to end) -> `api` and `ml` in parallel -> `web` -> `infra` -> `docs-demo`
