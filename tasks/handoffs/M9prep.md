# M9prep handoff

**Branch / worktree:** `worktree-agent-a68f7c2c98f0075a3` (D:\Projects\Peatland\.claude\worktrees\agent-a68f7c2c98f0075a3). Not merged, not pushed.

**Changed:**
- `ml/src/ml/build_data.py` + thin `scripts/build_training_data.py`: builds composites (existing pipeline, offline S3 stub, no LocalStack), downloads and caches NFI Scotland 2025 (ArcGIS FeatureServer, paged) and CPM2016 (NatureScot WFS, or `--cpm-source zip`), maps to the 4 classes (255 where sources disagree / uncertain / in the buffered Glen Trool box), rasterises with `ml.labels` on the exact composite grid, writes `data/composites`, `data/labels`, `data/MANIFEST.json` (sources, licences, fetch date, sha256, caveats). Flags: `--dry-run --region --bbox/--name --window --skip-composite --skip-labels --refresh --nfi-path --cpm-path --cpm-source`.
- `ml/notebooks/train.ipynb` (no outputs), `docs/runbooks/colab.md`, `docs/labels/training-labels.md`, `scripts/check_artifact.py`.
- Tests (synthetic, no network): `ml/tests/test_build_data.py`, `test_check_artifact.py`, `test_notebook.py`, plus a regression test in `test_split.py`.
- `ml/src/ml/labels.py` bugfix: `geom.make_valid()` raised AttributeError on invalid real polygons (existing bug); now `shapely.make_valid(geom)`. Test added.
- `ml/src/ml/split.py` fix in `assign_blocks`: val/test quotas now count only blocks that hold patches. Before, the ~24 forced Glen Trool blocks (empty when training elsewhere) used up the whole test quota, giving 0 test patches. Existing split tests unchanged and pass.

**Training bbox (default, two regions, both from `REGIONS` in build_data.py):**
- `galloway-south` (-4.52,54.90,-4.20,55.03): about 309 km2, 2077x1487 px, mostly MGRS tile UVF.
- `galloway-west` (-4.80,55.05,-4.60,55.25): about 300 km2, 1330x2257 px, tile UUG.
- Both at least 2.2 km from the Glen Trool box [-4.55,55.05,-4.30,55.30] (required 1 km buffer). Two regions because bog is only 3 to 4 percent of each; one region would leave too few bog pixels. Each region sits within one S2 tile except a seam sliver; Galloway is mixed forestry, hill and peat, matching the target classes.
- Measured class fractions from the real vectors (other / bog / woodland / felled-bare / unlabelled): south 46.0 / 3.2 / 22.4 / 18.5 / 9.8; west 28.8 / 3.9 / 34.2 / 18.4 / 14.7.

**Verified:**
- Live fetch of NFI and CPM services worked with real field names (IFT_IOA/CATEGORY; `importance`); build of label rasters on the two real grids gave the fractions above. Composites and the full pipeline NOT run (orchestrator's job).
- Local end-to-end on a synthetic 1400x1400 grid: build_data labels -> `prepare_data` -> 1-epoch CPU train -> `scripts/check_artifact.py` all passed.
- `--help` works for both scripts. Gates: ruff format+check clean, mypy clean (55 files), pytest 474 passed, 7 skipped.

**Decisions / gotchas:**
- Split problem and fix. With 256 px patches and 5 km blocks, only about a quarter of patches lie wholly in one block, and the 1 km buffer drops more. After the `assign_blocks` fix, preview (seed 0): 256 px stride 128 gives train 61 / val 10 / test 20 patches of 294 (203 dropped); 128 px stride 64 gives train 295 / val 206 / test 181 of 1328. So the Colab notebook sets `patch_size: 128`, `stride: 64` (variables at the top). `ml/configs/unet.yaml` is untouched (256/null). Orchestrator decision: keep 128 for the Colab run, or change `block_size`/`buffer` instead.
- Block assignment is a seeded random draw over few blocks (about 54), so val/test composition is lumpy. Bog is the risk: check the sanity cell that train has all four classes and that val/test have some bog. If not, change `split.seed` in the config or add a region.
- Season shift: spring 2025 composite versus NFI 2025 and CPM2016 vintage labels.
- NFI circularity: woodland and felled classes come from NFI polygons, so test scores for them partly measure agreement with NFI, not ground truth. Bog comes from CPM2016 (modelled, not field survey). Recorded in `MANIFEST.json` caveats and `docs/labels/training-labels.md`.
- Licences: NFI and CPM are OGL; no UKCEH LCM used; Muirburn map not used.
- Colab: `pip install -e ./ml` + `segmentation-models-pytorch` only. Do not `uv sync --extra train` (pins CPU torch). Python gotcha: Colab Python may be older than 3.13; notebook retries with `--ignore-requires-python` then `--no-deps`. numpy gotcha: if pip upgrades numpy the cell says RESTART NEEDED.
- NatureScot returns 403 to non-browser user agents; the downloader sets a browser-like UA.
- Runtime and size figures in the runbook are estimates, not measured.

**Dependency or contract requests:** None. No new dependencies. (`geopandas`, `shapely`, `rasterio` already used by ml.)

**Next (what you do by hand, in order, git-bash from the repo root):**
1. `export PATH="$PATH:/c/Users/karth/AppData/Roaming/Python/Python315/Scripts"; export UV_PYTHON_INSTALL_DIR='D:\Tools\uv\python'; export UV_CACHE_DIR='D:\Tools\uv\cache'`
2. Merge this branch to main, then `uv sync --all-packages`.
3. `uv run python scripts/build_training_data.py --dry-run`, then `uv run python scripts/build_training_data.py` (long: Sentinel-2 download). Rerun is safe (cached).
4. `cd data && uv run python -m zipfile -c ../peatwatch_data.zip composites labels MANIFEST.json && cd ..`
5. Upload `peatwatch_data.zip` to Drive `MyDrive/peatwatch/`.
6. `git push origin main`.
7. Open `ml/notebooks/train.ipynb` in Colab, T4 runtime, run all cells; read the sanity cell output.
8. Download `best.pt`, `split.json`, `config.json` into `artifacts/unet/`.
9. `uv run python scripts/check_artifact.py artifacts/unet/best.pt` (exit 0 = OK).
Full detail: `docs/runbooks/colab.md`.
