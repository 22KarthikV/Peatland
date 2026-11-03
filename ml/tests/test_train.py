"""U-Net training on tiny synthetic composites (CPU, no network, no pretrained weights)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

torch = pytest.importorskip("torch")
pytest.importorskip("segmentation_models_pytorch")

from ml.infer import predict_cog  # noqa: E402
from ml.model import ModelConfig, load_predictor  # noqa: E402
from ml.split import dataset_patch_bounds  # noqa: E402
from ml.train import (  # noqa: E402
    AugmentConfig,
    LoopConfig,
    SegLoss,
    TrainConfig,
    augment_pair,
    auto_class_weights,
    config_from_dict,
    dice_loss,
    fit_train_statistics,
    load_config,
    prepare_data,
    train,
)

REPO = Path(__file__).resolve().parents[2]
ORIGIN = (400000.0, 6100000.0)
TINY_MODEL: dict[str, Any] = {
    "encoder_name": "resnet18",
    "encoder_weights": None,
    "encoder_depth": 3,
    "decoder_channels": [32, 16, 8],
}


def _truth(h: int, w: int) -> np.ndarray:
    r, c = np.mgrid[0:h, 0:w]
    out: np.ndarray = (((r // 8) + (c // 8)) % 4).astype(np.uint8)
    return out


def _write_scene(tmp: Path, h: int, w: int, seed: int = 0) -> tuple[Path, Path]:
    truth = _truth(h, w)
    rng = np.random.default_rng(seed)
    data = rng.normal(0, 0.3, (18, h, w)).astype(np.float32)
    for cls in range(4):
        data[cls, truth == cls] += 1.0  # class signal in band == class id
    data[:, 5, 5] = np.nan  # one cloud pixel
    labels = truth.copy()
    labels[: h // 4, : w // 4][::3, ::3] = 255  # a few unlabelled pixels
    profile = {
        "driver": "GTiff",
        "height": h,
        "width": w,
        "crs": "EPSG:32630",
        "transform": from_origin(*ORIGIN, 10, 10),
    }
    comp, lab = tmp / "comp.tif", tmp / "labels.tif"
    with rasterio.open(comp, "w", count=18, dtype="float32", nodata=np.nan, **profile) as dst:
        dst.write(data)
    with rasterio.open(lab, "w", count=1, dtype="uint8", nodata=255, **profile) as dst:
        dst.write(labels, 1)
    return comp, lab


def _cfg(comp: Path, lab: Path, out: Path, **over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "seed": 7,
        "data": {
            "sources": [{"composite": str(comp), "labels": str(lab)}],
            "patch_size": 32,
            "min_labelled_fraction": 0.0,
            "split": {
                "block_size": 1.0e6,
                "buffer": 0.0,
                "fractions": [1.0, 0.0, 0.0],
                "holdout_bboxes_wgs84": [],
            },
        },
        "model": dict(TINY_MODEL),
        "train": {
            "epochs": 2,
            "batch_size": 4,
            "lr": 0.01,
            "weight_decay": 0.0,
            "num_workers": 0,
            "device": "cpu",
            "augment": {"hflip": False, "vflip": False, "rot90": False},
        },
        "output": {"dir": str(out)},
        "mlflow": {"enabled": False},
    }
    for section, values in over.items():
        if isinstance(values, dict):
            cfg[section] = {**cfg.get(section, {}), **values}
        else:
            cfg[section] = values
    return cfg


@pytest.fixture
def scene(tmp_path: Path) -> tuple[Path, Path]:
    return _write_scene(tmp_path, 64, 128)  # 2 x 4 = 8 patches of 32 px


def test_overfit_eight_patches(scene: tuple[Path, Path], tmp_path: Path) -> None:
    comp, lab = scene
    cfg = config_from_dict(_cfg(comp, lab, tmp_path / "run", train={"epochs": 40, "batch_size": 8}))
    result = train(cfg)
    losses = [h["train_loss"] for h in result.history]
    assert len(losses) == 40
    assert losses[-1] < 0.25 * losses[0], losses
    assert result.best_path.exists() and result.last_path.exists()

    model = load_predictor(result.best_path)
    prepared = prepare_data(cfg.data)
    hits = total = 0
    for p in prepared.raw[0]:
        pred = model(p)
        assert p.label is not None
        ok = (pred != 255) & (p.label != 255)
        hits += int((pred[ok] == p.label[ok]).sum())
        total += int(ok.sum())
    assert hits / total > 0.9


def _state(path: Path) -> dict[str, Any]:
    ckpt = torch.load(path, map_location="cpu", weights_only=True)
    return ckpt  # type: ignore[no-any-return]


def test_resume_matches_uninterrupted_run(scene: tuple[Path, Path], tmp_path: Path) -> None:
    comp, lab = scene
    aug = {"hflip": True, "vflip": True, "rot90": True}
    straight = train(
        config_from_dict(_cfg(comp, lab, tmp_path / "a", train={"epochs": 3, "augment": aug}))
    )

    out_b = tmp_path / "b"
    mlf = {"enabled": True}
    first = train(
        config_from_dict(_cfg(comp, lab, out_b, train={"epochs": 1, "augment": aug}, mlflow=mlf))
    )
    assert len(first.history) == 1
    # Same config but 3 epochs: picks up last.pt (epoch 0) and runs epochs 1..2.
    # The cosine LR is a function of (epoch, epochs), so extending the run is consistent.
    resumed = train(
        config_from_dict(_cfg(comp, lab, out_b, train={"epochs": 3, "augment": aug}, mlflow=mlf))
    )
    assert [h["epoch"] for h in resumed.history] == [0.0, 1.0, 2.0]
    assert resumed.run_id == first.run_id

    a = _state(straight.last_path)["model"]["state_dict"]
    b = _state(resumed.last_path)["model"]["state_dict"]
    assert a.keys() == b.keys()
    for k in a:
        assert torch.allclose(a[k].float(), b[k].float(), atol=1e-5), k
    np.testing.assert_allclose(
        [h["train_loss"] for h in straight.history],
        [h["train_loss"] for h in resumed.history],
        rtol=1e-5,
    )

    import mlflow

    assert resumed.run_id is not None
    client = mlflow.MlflowClient()
    steps = [m.step for m in client.get_metric_history(resumed.run_id, "train_loss")]
    assert sorted(steps) == [0, 1, 2]

    # Finished run: resuming again is a no-op.
    again = train(
        config_from_dict(
            _cfg(comp, lab, out_b, train={"epochs": 3, "augment": aug}, mlflow={"enabled": False})
        )
    )
    assert len(again.history) == 3


def test_resume_refuses_different_split(scene: tuple[Path, Path], tmp_path: Path) -> None:
    comp, lab = scene
    out = tmp_path / "run"
    train(config_from_dict(_cfg(comp, lab, out, train={"epochs": 1})))
    other = _cfg(comp, lab, out, train={"epochs": 2})
    other["data"]["split"] = {**other["data"]["split"], "fractions": [0.5, 0.5, 0.0]}
    other["data"]["split"]["block_size"] = 320.0
    with pytest.raises(ValueError, match="different split"):
        train(config_from_dict(other))


def test_no_leakage_into_training(tmp_path: Path) -> None:
    comp, lab = _write_scene(tmp_path, 64, 128, seed=3)  # 4 x 8 patches of 16 px
    with rasterio.open(comp) as src:
        left, top = src.transform @ (0, 0)
        right, bottom = src.transform @ (32, 64)  # left quarter (2 patch columns)
        holdout = transform_bounds(src.crs, "EPSG:4326", left, bottom, right, top)
    raw = _cfg(comp, lab, tmp_path / "run")
    raw["data"]["patch_size"] = 16
    raw["data"]["split"] = {
        "block_size": 160.0,
        "buffer": 0.0,
        "fractions": [0.5, 0.25, 0.25],
        "seed": 1,
        "holdout_bboxes_wgs84": [list(holdout)],
    }
    cfg = config_from_dict(raw)
    prepared = prepare_data(cfg.data)
    split = prepared.split
    assert split.indices("test") and split.indices("val") and split.indices("train")

    train_idx = {pi for _, pi in prepared.train_items}
    val_idx = {pi for _, pi in prepared.val_items}
    assert train_idx == set(split.indices("train"))
    assert val_idx == set(split.indices("val"))
    assert not (train_idx | val_idx) & set(split.indices("test"))
    # Patches inside the hold-out box are test, never train/val.
    bounds = dataset_patch_bounds(prepared.raw[0])
    inside = [i for i, b in enumerate(bounds) if b[2] <= right and b[0] >= left]
    assert inside and all(split.patch_split[i] == "test" for i in inside)

    # Normaliser is fitted on train patches only.
    norm, counts = fit_train_statistics(prepared)
    ds = prepared.raw[0]
    train_px = np.concatenate([ds[i].array.reshape(18, -1) for i in sorted(train_idx)], axis=1)
    all_px = np.concatenate([p.array.reshape(18, -1) for p in ds], axis=1)
    np.testing.assert_allclose(norm.mean.ravel(), np.nanmean(train_px, axis=1), atol=1e-5)
    assert not np.allclose(norm.mean.ravel(), np.nanmean(all_px, axis=1), atol=1e-5)
    train_labels = np.concatenate(
        [ds[i].label[ds[i].valid] for i in sorted(train_idx)]  # type: ignore[index]
    )
    expected = np.bincount(train_labels[train_labels < 4], minlength=4)
    assert counts.tolist() == expected.tolist()

    # The training run records the same split for the evaluation leakage re-check.
    result = train(config_from_dict({**raw, "train": {**raw["train"], "epochs": 1}}))
    import json

    saved = json.loads((result.out_dir / "split.json").read_text("utf-8"))
    assert saved["sources"][0]["patch_split"] == split.patch_split
    assert {tuple(i) for i in saved["train_items"]} == set(prepared.train_items)
    assert "val_miou" in result.history[0]


def test_predictor_odd_window_and_predict_cog(scene: tuple[Path, Path], tmp_path: Path) -> None:
    comp, lab = scene
    result = train(config_from_dict(_cfg(comp, lab, tmp_path / "run", train={"epochs": 1})))
    model = load_predictor(result.last_path)  # training checkpoints load too
    with rasterio.open(comp) as src:
        window = src.read(window=((0, 21), (0, 27))).astype(np.float32)
    proba = model.predict_proba(window)
    assert proba.shape == (4, 21, 27)
    assert np.isnan(proba[:, 5, 5]).all()
    finite = np.isfinite(proba).all(axis=0)
    np.testing.assert_allclose(proba[:, finite].sum(axis=0), 1.0, atol=1e-5)

    out = predict_cog(comp, tmp_path / "cls.tif", model, window_size=48, overlap=16)
    with rasterio.open(out) as src:
        cls = src.read(1)
    assert cls.shape == (64, 128)
    assert cls[5, 5] == 255
    assert set(np.unique(cls)) <= {0, 1, 2, 3, 255}


def test_losses() -> None:
    target = torch.tensor([[[0, 1], [2, 255]]])
    perfect = torch.full((1, 4, 2, 2), -20.0)
    for (r, c), k in {(0, 0): 0, (0, 1): 1, (1, 0): 2, (1, 1): 3}.items():
        perfect[0, k, r, c] = 20.0
    assert float(dice_loss(perfect, target)) < 1e-3
    assert float(dice_loss(-perfect, target)) > 0.5  # eps=1 smoothing on 3 pixels
    loss = SegLoss(torch.ones(4))
    assert float(loss(perfect, target)) < 1e-3
    all_ignored = torch.full((1, 2, 2), 255)
    zero = loss(perfect.requires_grad_(), all_ignored)
    assert float(zero) == 0.0
    zero.backward()  # still differentiable


def test_auto_class_weights() -> None:
    w = auto_class_weights(np.array([300, 100, 0, 1]), cap=50.0)
    assert w[2] == 0.0
    assert w[0] == pytest.approx(401 / (3 * 300))
    assert w[1] == pytest.approx(401 / (3 * 100))
    assert w[3] == 50.0  # capped


def test_augment_is_geometric_and_aligned() -> None:
    rng = np.random.default_rng(0)
    y = rng.integers(0, 4, (8, 8)).astype(np.int64)
    x = np.stack([y.astype(np.float32), y.astype(np.float32) * 2]).astype(np.float32)
    seen = set()
    for _ in range(20):
        xa, ya = augment_pair(x, y, rng, AugmentConfig())
        np.testing.assert_array_equal(xa[0], ya)  # label follows the image
        np.testing.assert_array_equal(xa[1], ya * 2)  # band values unchanged (no jitter)
        assert sorted(xa.ravel()) == sorted(x.ravel())
        seen.add(ya.tobytes())
    assert len(seen) > 1
    off = AugmentConfig(hflip=False, vflip=False, rot90=False)
    xa, ya = augment_pair(x, y, rng, off)
    np.testing.assert_array_equal(xa, x)


def test_cli_main(scene: tuple[Path, Path], tmp_path: Path, capsys: Any) -> None:
    import json

    import yaml
    from ml.train import main

    comp, lab = scene
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(_cfg(comp, lab, tmp_path / "ignored")), "utf-8")
    out = tmp_path / "cli"
    assert main(["--config", str(cfg_path), "--out-dir", str(out), "--epochs", "1"]) == 0
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert Path(printed["best"]) == out / "best.pt" and (out / "best.pt").exists()
    assert not (tmp_path / "ignored").exists()


def test_shipped_config_parses_and_rejects_unknown_keys() -> None:
    cfg = load_config(REPO / "ml" / "configs" / "unet.yaml")
    assert isinstance(cfg, TrainConfig)
    assert cfg.model == ModelConfig()  # shipped config = documented defaults
    assert cfg.model.in_channels == 18 and cfg.model.classes == 4
    assert cfg.train.class_weights == "auto"
    assert isinstance(cfg.train, LoopConfig)
    with pytest.raises(ValueError, match="unknown keys"):
        config_from_dict({"data": {"sources": [{"composite": "a", "labels": "b"}]}, "oops": 1})
    with pytest.raises(ValueError, match="decoder_channels"):
        ModelConfig(encoder_depth=3)
