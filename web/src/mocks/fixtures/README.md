# Fixtures (contract C4)

JSON files here are response bodies for the web and api mocks, for the Glen Trool demo site
(`glen-trool`) and the date pair 2025-03-19 / 2025-04-08, model `unet-1.0.0`. Ids are consistent
across files. `contracts/tests/test_fixtures.py` validates each against `openapi.yaml` (or
`queue.schema.json`).

| File | Operation | Status |
|---|---|---|
| `health.200.json` | `GET /health` | 200 |
| `sites.create.201.json` | `POST /sites` | 201 |
| `sites.list.200.json` | `GET /sites` | 200 |
| `sites.get.200.json` | `GET /sites/{site_id}` | 200 |
| `runs.create.202.json` | `POST /sites/{site_id}/runs` | 202 (queued) |
| `runs.create.200.json` | `POST /sites/{site_id}/runs` | 200 (identical active run) |
| `runs.get.200.json` | `GET /runs/{run_id}` | 200 (succeeded) |
| `runs.get.failed.200.json` | `GET /runs/{run_id}` | 200 (failed, `no_usable_scenes`) |
| `classifications.list.200.json` | `GET /sites/{site_id}/classifications` | 200 |
| `changes.create.200.json` | `POST /sites/{site_id}/changes` | 200 (change exists) |
| `changes.create.202.json` | `POST /sites/{site_id}/changes` | 202 (change run queued) |
| `changes.stats.200.json` | `GET /changes/{change_id}/stats` | 200 JSON |
| `changes.stats.200.csv` | `GET /changes/{change_id}/stats` | 200 `text/csv` |
| `error.401.json` | any authenticated endpoint | 401 |
| `error.404.json` | endpoints with a path id | 404 |
| `error.406.json` | `GET /changes/{change_id}/stats` | 406 |
| `error.409.json` | `POST /sites` | 409 |
| `error.422.validation_error.json` | any | 422 |
| `error.422.boundary_too_large.json` | `POST /sites` | 422 |
| `error.422.classification_missing.json` | `POST /sites/{site_id}/changes` | 422 |
| `error.429.json` | any authenticated endpoint | 429 (header `Retry-After: 12`) |
| `queue.pipeline.json` | SQS `peatwatch-jobs` | pipeline job message |
| `queue.change.json` | SQS `peatwatch-jobs` | change job message |

Headers (`Location`, `Retry-After`, `Content-Disposition`) are not stored in the files; see
`openapi.yaml`.

## COG fixtures to be generated later (by pipeline)

Tiny rasters (about 64 x 64 pixels, EPSG:32630, 10 m, written by pipeline code with the C2
profile) for api, worker and web tests. Names only; not in the repo yet:

- `composite-glen-trool-w20250319-20250319.tif` (+ `.json` sidecar)
- `composite-glen-trool-w20250408-20250408.tif` (+ `.json` sidecar)
- `classification-glen-trool-w20250319-20250319-unet-1.0.0.tif` (+ `.json` sidecar)
- `classification-glen-trool-w20250408-20250408-unet-1.0.0.tif` (+ `.json` sidecar)
- `change-glen-trool-w20250319-20250319__w20250408-20250408-unet-1.0.0.tif` (+ `.json` sidecar)
- `boundary-glen-trool.geojson` (matches the boundary in `sites.get.200.json`)
