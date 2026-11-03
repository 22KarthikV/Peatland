# Runbook: train the U-Net on Colab (M9)

Goal: produce `artifacts/unet/best.pt` (+ `split.json`, `config.json`) from the Galloway data using a free Colab T4. Commands are for Windows git-bash from the repo root.

Numbers marked (estimate) have not been measured.

## 0. One-time setup

```bash
export PATH="$PATH:/c/Users/karth/AppData/Roaming/Python/Python315/Scripts"
export UV_PYTHON_INSTALL_DIR='D:\Tools\uv\python'
export UV_CACHE_DIR='D:\Tools\uv\cache'
uv sync --all-packages
```

## 1. Build the data (local, needs internet)

Two regions, about 300 km2 each, spring 2025, outside the Glen Trool demo box (see `docs/labels/training-labels.md`).

```bash
uv run python scripts/build_training_data.py --dry-run     # prints regions, grids, URLs, nothing downloaded
uv run python scripts/build_training_data.py               # composites + labels + MANIFEST
```

- It fetches Sentinel-2 via the pipeline (Planetary Computer, no key), NatureScot CPM2016 and Forestry Commission NFI Scotland 2025 (cached in `data/raw/`; reruns are cheap, `--refresh` refetches).
- Time (estimate): 20 to 60 minutes per region, mostly the Sentinel-2 download. Disk (estimate): 2 to 4 GB.
- Outputs: `data/composites/<region>__<start>-<end>.tif`, `data/labels/<region>.tif`, `data/MANIFEST.json`.
- If a label download fails the message names the URL and the flag for a manual file: `--nfi-path`, `--cpm-path`.
- `--skip-composite` / `--skip-labels` redo one half.

Equivalent pipeline command for one composite (needs LocalStack; the script avoids that with an offline stub):
`uv run peatwatch-pipeline run data/pipeline/<name>.yaml`.

## 2. Zip and upload

```bash
cd data
uv run python -m zipfile -c ../peatwatch_data.zip composites labels MANIFEST.json
cd ..
```

The archive must contain `composites/`, `labels/` and `MANIFEST.json` at its top level. Run the zip from inside `data/` as shown (the notebook unzips to `/content/data` and expects no extra `data/` level). Upload to Google Drive at `MyDrive/peatwatch/peatwatch_data.zip` (size: estimate 1 to 3 GB; the free 15 GB Drive is enough).

## 3. Push the code

Colab clones https://github.com/22KarthikV/Peatland.git, branch `main`. M8 (`ml.train`) and `scripts/check_artifact.py` must be on that branch:

```bash
git push origin main
```

## 4. Run the notebook

1. Open `ml/notebooks/train.ipynb` in Colab (colab.research.google.com > GitHub tab, or upload the file).
2. Runtime > Change runtime type > **T4 GPU**.
3. Run cells top to bottom. They: mount Drive, clone the repo, `pip install -e ./ml` plus `segmentation-models-pytorch` (never `uv sync` of the train extra: it pins CPU torch wheels), unzip the data to `/content/data`, write `/content/unet_colab.yaml` (ImageNet weights, `device: cuda`, output on Drive), print split sizes and class counts, then run `python -m ml.train --config /content/unet_colab.yaml`.
4. Check the sanity cell before training: train has all four classes, test is not empty. It stops with an assertion otherwise.

Cost (estimate): about 300 train patches of 128 px, 50 epochs, ResNet34 on a T4: 10 to 30 minutes, under 6 GB GPU memory with batch size 8. Colab free tier gives roughly 12 hours per session but may disconnect earlier.

## 5. Resume after a disconnect

Everything is written to `MyDrive/peatwatch/unet/` (checkpoints, `last.pt`, `best.pt`, `split.json`, `config.json`) and `MyDrive/peatwatch/train.log`. Reconnect, rerun sections 1 to 5 of the notebook, then the train cell: training continues from `last.pt` (`output.resume: true`). It refuses to resume if the split changed (different data or config).

## 6. Bring the artifact home

The last notebook cell downloads `unet_artifacts.zip`; or copy `peatwatch/unet/` from drive.google.com. Place the three files:

```bash
mkdir -p artifacts/unet
unzip unet_artifacts.zip -d /tmp/unet_artifacts
cp /tmp/unet_artifacts/{best.pt,split.json,config.json} artifacts/unet/
```

`artifacts/` is gitignored. Keep `MANIFEST.json` and `train.log` with it.

## 7. Verify on CPU (the API worker will do this)

```bash
uv run python scripts/check_artifact.py artifacts/unet/best.pt
```

Loads via `ml.model.load_predictor` on CPU, predicts one 18-band window with a NaN corner and checks shape and probabilities. Exit 0 = OK, 1 = failed to load/predict, 2 = file missing. The notebook runs the same check with CUDA hidden.

## Troubleshooting

- `RESTART NEEDED` after install: Runtime > Restart session, rerun from section 2.
- CUDA out of memory: set `BATCH_SIZE = 4` in section 1.
- pip cannot resolve (Colab Python older than 3.13): the cell retries with `--ignore-requires-python` and then `--no-deps`.
- Class missing from train: do not train. Rebuild with different regions, see `docs/labels/training-labels.md`.
- Honest caveat: labels are weak (CPM2016 + NFI), and woodland/felled labels come from NFI, which the model could partly learn from. See the caveats in `MANIFEST.json`.
