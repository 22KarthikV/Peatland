"""Config-driven, seeded, resumable U-Net training (``python -m ml.train --config ...``).

Data
----
Each ``data.sources`` entry is a composite COG (``contracts/cog-layout.md``, 18 bands) plus
labels on the same grid: a uint8 GeoTIFF of class ids (255 = unlabelled) or a vector file
(GeoJSON / GPKG, ``class`` column) rasterised with ``ml.labels.rasterize_to_composite``.
All sources must share one projected CRS. Relative paths resolve against the working
directory (run from the repo root).

Leakage
-------
Patches of *all* sources are split together with ``ml.split.split_bounds`` (block grid,
buffer, hold-out boxes from ``data.split``; same defaults as the baselines' evaluation).
Training reads ``train`` patches only, validation ``val`` patches only; ``test`` patches
(including every patch in a hold-out box) are never opened. The band normaliser and the
class weights are fitted on the ``train`` patches only. ``split.json`` in the output
directory records the split for the evaluation leakage re-check.

Training
--------
Loss = ``ce_weight * CE(class weights) + dice_weight * soft Dice``; unlabelled (255) and
invalid (any band non-finite) pixels are ignored. Class weights ``"auto"`` are inverse pixel
frequency over the train split (``total / (K_present * count)``, capped at
``class_weight_max``, 0 for classes absent from train). Augmentation is geometric only:
random horizontal / vertical flips and 90-degree rotations (no colour or band jitter, which
would corrupt the spectral indices). AdamW with a stateless per-epoch cosine LR
(``cosine_lr``); AMP on CUDA.

Determinism and resume
----------------------
Shuffling and augmentation draw from generators derived from ``(seed, epoch)``
(augmentation additionally from the sample index), so an interrupted run resumed from
``last.pt`` reproduces the uninterrupted run on the same device. ``last.pt`` (written
atomically every epoch) holds model, optimiser, scheduler, AMP scaler, history, the MLflow
run id and a digest of the split; resuming with a different split raises. ``best.pt`` is
the model file with the best validation mean IoU (train loss if there is no val split) and
loads with ``ml.model.load_predictor``.

MLflow
------
Params (flattened config, split counts, class weights), per-epoch metrics and the
``split.json`` / config artifacts go to ``mlflow.tracking_uri`` (default: a SQLite DB in the
output directory, artifacts beside it, so a Colab run on Drive keeps its tracking data).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import random
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
import rasterio
import torch
import torch.nn.functional as F
import yaml
from torch import nn
from torch.utils.data import DataLoader, Dataset

from ml.dataset import LABEL_NODATA, BandNormalizer, PatchDataset
from ml.metrics import NUM_CLASSES, segmentation_metrics
from ml.model import ModelConfig, build_unet, model_payload, normalizer_from_dict, save_model
from ml.split import DEMO_BBOX_WGS84, Bounds, SplitResult, dataset_patch_bounds, split_bounds

log = logging.getLogger("ml.train")

CHECKPOINT_FORMAT = "peatwatch-unet-ckpt/1"
LAST = "last.pt"
BEST = "best.pt"


# --------------------------------------------------------------------------- config


def _kwargs(cls: type, d: dict[str, Any] | None, section: str) -> dict[str, Any]:
    d = dict(d or {})
    unknown = set(d) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"unknown keys in {section!r}: {sorted(unknown)}")
    return d


@dataclass(frozen=True)
class SourceConfig:
    composite: str
    labels: str
    label_class_column: str = "class"


@dataclass(frozen=True)
class SplitConfig:
    block_size: float = 5000.0
    buffer: float = 1000.0
    fractions: tuple[float, float, float] = (0.6, 0.2, 0.2)
    seed: int = 0
    holdout_bboxes_wgs84: tuple[Bounds, ...] = (DEMO_BBOX_WGS84,)


@dataclass(frozen=True)
class DataConfig:
    sources: tuple[SourceConfig, ...]
    patch_size: int = 256
    stride: int | None = None
    min_labelled_fraction: float = 0.05
    split: SplitConfig = field(default_factory=SplitConfig)


@dataclass(frozen=True)
class AugmentConfig:
    hflip: bool = True
    vflip: bool = True
    rot90: bool = True


@dataclass(frozen=True)
class LoopConfig:
    epochs: int = 50
    batch_size: int = 8
    lr: float = 1e-3
    weight_decay: float = 1e-4
    num_workers: int = 0
    device: str = "auto"
    amp: bool = True
    class_weights: Literal["auto"] | tuple[float, ...] | None = "auto"
    class_weight_max: float = 50.0
    ce_weight: float = 1.0
    dice_weight: float = 1.0
    augment: AugmentConfig = field(default_factory=AugmentConfig)


@dataclass(frozen=True)
class OutputConfig:
    dir: str = "artifacts/unet"
    resume: bool = True


@dataclass(frozen=True)
class MlflowConfig:
    enabled: bool = True
    tracking_uri: str | None = None
    experiment: str = "peatwatch-unet"
    run_name: str | None = None


@dataclass(frozen=True)
class TrainConfig:
    data: DataConfig
    seed: int = 42
    model: ModelConfig = field(default_factory=ModelConfig)
    train: LoopConfig = field(default_factory=LoopConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    mlflow: MlflowConfig = field(default_factory=MlflowConfig)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["model"] = self.model.to_dict()
        out: dict[str, Any] = json.loads(json.dumps(d))  # tuples -> lists
        return out


def config_from_dict(raw: dict[str, Any]) -> TrainConfig:
    """Build a ``TrainConfig`` from parsed YAML; unknown keys raise ``ValueError``."""
    top = _kwargs(TrainConfig, raw, "root")
    if "data" not in top:
        raise ValueError("config needs a 'data' section")
    data = _kwargs(DataConfig, top.pop("data"), "data")
    sources = tuple(
        SourceConfig(**_kwargs(SourceConfig, s, "data.sources")) for s in data.pop("sources", [])
    )
    if not sources:
        raise ValueError("data.sources must list at least one composite/labels pair")
    split = _kwargs(SplitConfig, data.pop("split", None), "data.split")
    if "fractions" in split:
        split["fractions"] = tuple(float(f) for f in split["fractions"])
    if "holdout_bboxes_wgs84" in split:
        split["holdout_bboxes_wgs84"] = tuple(
            tuple(float(v) for v in b) for b in split["holdout_bboxes_wgs84"] or ()
        )
    loop = _kwargs(LoopConfig, top.pop("train", None), "train")
    aug = AugmentConfig(**_kwargs(AugmentConfig, loop.pop("augment", None), "train.augment"))
    cw = loop.get("class_weights", "auto")
    if cw is not None and cw != "auto":
        loop["class_weights"] = tuple(float(w) for w in cw)
    return TrainConfig(
        data=DataConfig(sources=sources, split=SplitConfig(**split), **data),
        model=ModelConfig.from_dict(top.pop("model", None) or {}),
        train=LoopConfig(augment=aug, **loop),
        output=OutputConfig(**_kwargs(OutputConfig, top.pop("output", None), "output")),
        mlflow=MlflowConfig(**_kwargs(MlflowConfig, top.pop("mlflow", None), "mlflow")),
        **top,
    )


def load_config(path: Path | str) -> TrainConfig:
    return config_from_dict(yaml.safe_load(Path(path).read_text("utf-8")))


def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = json.dumps(v) if isinstance(v, list) else str(v)
    return out


# --------------------------------------------------------------------------- seeding


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed % 2**32)
    torch.manual_seed(seed)


def resolve_device(name: str) -> torch.device:
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(name)


# --------------------------------------------------------------------------- data


def load_label_raster(source: SourceConfig) -> npt.NDArray[np.uint8]:
    """Labels on the composite grid: a uint8 GeoTIFF or a rasterised vector file."""
    lp = Path(source.labels)
    if lp.suffix.lower() in {".tif", ".tiff"}:
        with rasterio.open(source.composite) as comp, rasterio.open(lp) as lab:
            if (lab.height, lab.width) != (
                comp.height,
                comp.width,
            ) or lab.transform != comp.transform:
                raise ValueError(f"label raster {lp} is not on the grid of {source.composite}")
            labels: npt.NDArray[np.uint8] = lab.read(1).astype(np.uint8)
            return labels
    import geopandas as gpd

    from ml.labels import rasterize_to_composite

    return rasterize_to_composite(
        gpd.read_file(lp), source.composite, class_column=source.label_class_column
    )


Item = tuple[int, int]  # (source index, patch index)


@dataclass
class PreparedData:
    """Raw datasets per source plus the split and the train / val patch lists."""

    raw: list[PatchDataset]
    split: SplitResult
    offsets: list[int]  # first global patch index of each source
    train_items: list[Item]
    val_items: list[Item]

    def digest(self) -> str:
        payload = json.dumps({"train": self.train_items, "val": self.val_items})
        return hashlib.sha256(payload.encode()).hexdigest()

    def summary(self) -> dict[str, Any]:
        return {
            "counts": self.split.counts(),
            "train_patches_used": len(self.train_items),
            "val_patches_used": len(self.val_items),
            "block_size": self.split.block_size,
            "buffer": self.split.buffer,
            "seed": self.split.seed,
            "sources": [
                {
                    "composite": str(ds.path),
                    "patch_split": self.split.patch_split[o : o + len(ds)],
                    "origins": [list(rc) for rc in ds.origins],
                }
                for ds, o in zip(self.raw, self.offsets, strict=True)
            ],
            "block_split": [[c, r, s] for (c, r), s in sorted(self.split.block_split.items())],
            "train_items": [list(i) for i in self.train_items],
            "val_items": [list(i) for i in self.val_items],
        }


def _labelled_fraction(ds: PatchDataset, i: int) -> float:
    assert ds.labels is not None
    r, c = ds.origins[i]
    lab = ds.labels[r : r + ds.size, c : c + ds.size]
    return float((lab != LABEL_NODATA).sum()) / float(ds.size * ds.size)


def prepare_data(cfg: DataConfig) -> PreparedData:
    """Open sources, split all patches together, and pick the train / val patches."""
    raw: list[PatchDataset] = []
    for s in cfg.sources:
        raw.append(
            PatchDataset(
                s.composite,
                size=cfg.patch_size,
                stride=cfg.stride,
                edge="drop",
                labels=load_label_raster(s),
            )
        )
    crs = raw[0].crs
    if any(ds.crs != crs for ds in raw[1:]):
        raise ValueError("all sources must share one CRS")
    bounds: list[Bounds] = []
    offsets: list[int] = []
    for ds in raw:
        offsets.append(len(bounds))
        bounds.extend(dataset_patch_bounds(ds))
    sc = cfg.split
    split = split_bounds(
        bounds,
        crs,
        block_size=sc.block_size,
        buffer=sc.buffer,
        fractions=sc.fractions,
        seed=sc.seed,
        holdout_bboxes_wgs84=sc.holdout_bboxes_wgs84,
    )
    train: list[Item] = []
    val: list[Item] = []
    for si, (ds, off) in enumerate(zip(raw, offsets, strict=True)):
        for pi in range(len(ds)):
            which = split.patch_split[off + pi]
            frac = _labelled_fraction(ds, pi)
            if which == "train" and frac >= cfg.min_labelled_fraction and frac > 0:
                train.append((si, pi))
            elif which == "val" and frac > 0:
                val.append((si, pi))
    if not train:
        raise ValueError("no labelled train patches after the split")
    return PreparedData(raw, split, offsets, train, val)


def fit_train_statistics(
    prepared: PreparedData, n_classes: int = NUM_CLASSES
) -> tuple[BandNormalizer, npt.NDArray[np.int64]]:
    """Streaming nan-aware band mean/std and labelled-valid class pixel counts (train only)."""
    n = s1 = s2 = None
    counts = np.zeros(n_classes, dtype=np.int64)
    for si, pi in prepared.train_items:
        p = prepared.raw[si][pi]
        x = p.array.reshape(p.array.shape[0], -1).astype(np.float64)
        fin = np.isfinite(x)
        xz = np.where(fin, x, 0.0)
        if n is None:
            n, s1, s2 = fin.sum(1), xz.sum(1), (xz * xz).sum(1)
        else:
            assert s1 is not None and s2 is not None
            n, s1, s2 = n + fin.sum(1), s1 + xz.sum(1), s2 + (xz * xz).sum(1)
        assert p.label is not None
        lab = p.label[p.valid]
        counts += np.bincount(lab[lab < n_classes], minlength=n_classes)[:n_classes]
    assert n is not None and s1 is not None and s2 is not None
    safe = np.maximum(n, 1)
    mean = s1 / safe
    std = np.sqrt(np.maximum(s2 / safe - mean**2, 0.0))
    mean[n == 0], std[n == 0] = 0.0, 1.0
    return BandNormalizer(mean.tolist(), std.tolist()), counts


def auto_class_weights(counts: npt.NDArray[np.int64], cap: float) -> npt.NDArray[np.float32]:
    """Inverse frequency ``total / (K_present * count)``, capped; 0 for absent classes."""
    present = counts > 0
    w = np.zeros(len(counts), dtype=np.float64)
    if present.any():
        w[present] = counts.sum() / (present.sum() * counts[present])
    return np.minimum(w, cap).astype(np.float32)


def augment_pair(
    x: npt.NDArray[np.float32],
    y: npt.NDArray[np.int64],
    rng: np.random.Generator,
    cfg: AugmentConfig,
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.int64]]:
    """Joint random flips / rot90 of (C, H, W) image and (H, W) label. Geometry only."""
    if cfg.hflip and rng.random() < 0.5:
        x, y = x[:, :, ::-1], y[:, ::-1]
    if cfg.vflip and rng.random() < 0.5:
        x, y = x[:, ::-1, :], y[::-1, :]
    if cfg.rot90:
        k = int(rng.integers(4))
        if k:
            x, y = np.rot90(x, k, axes=(1, 2)), np.rot90(y, k, axes=(0, 1))
    return np.ascontiguousarray(x), np.ascontiguousarray(y)


class TorchPatches(Dataset[tuple[torch.Tensor, torch.Tensor]]):
    """Normalised patches (NaN -> 0) and int64 labels (255 where unlabelled or invalid)."""

    def __init__(
        self,
        datasets: Sequence[PatchDataset],
        items: Sequence[Item],
        *,
        augment: AugmentConfig | None = None,
        seed: int = 0,
    ) -> None:
        self.datasets = list(datasets)
        self.items = list(items)
        self.augment = augment
        self.seed = seed
        self.epoch = 0  # set before each epoch; workers are re-created per epoch

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, k: int) -> tuple[torch.Tensor, torch.Tensor]:
        si, pi = self.items[k]
        p = self.datasets[si][pi]
        assert p.label is not None
        y = p.label.astype(np.int64)
        y[~p.valid] = LABEL_NODATA
        x = p.array
        if self.augment is not None:
            rng = np.random.default_rng([self.seed, self.epoch, k])
            x, y = augment_pair(x, y, rng, self.augment)
        return torch.from_numpy(np.ascontiguousarray(x)), torch.from_numpy(y)


def _normalised_datasets(
    prepared: PreparedData, normalizer: BandNormalizer, stride: int | None
) -> list[PatchDataset]:
    """Same patches as ``prepared.raw``, normalised with NaN -> 0 (the model's input)."""
    out = []
    for ds in prepared.raw:
        nd = PatchDataset(
            ds.path,
            size=ds.size,
            stride=stride,
            edge=ds.edge,
            nan_policy="zero",
            normalize=normalizer,
            labels=ds.labels,
        )
        if nd.origins != ds.origins:
            raise AssertionError("normalised dataset patches differ from the split patches")
        out.append(nd)
    return out


# --------------------------------------------------------------------------- loss


def dice_loss(
    logits: torch.Tensor, target: torch.Tensor, ignore_index: int = LABEL_NODATA, eps: float = 1.0
) -> torch.Tensor:
    """Soft multi-class Dice over labelled pixels, averaged over classes present in target."""
    k = logits.shape[1]
    mask = (target != ignore_index).unsqueeze(1).to(logits.dtype)
    probs = torch.softmax(logits, dim=1) * mask
    onehot = F.one_hot(torch.where(target == ignore_index, 0, target), k)
    onehot = onehot.permute(0, 3, 1, 2).to(logits.dtype) * mask
    dims = (0, 2, 3)
    inter = (probs * onehot).sum(dims)
    card = (probs + onehot).sum(dims)
    present = onehot.sum(dims) > 0
    if not bool(present.any()):
        return logits.sum() * 0.0
    dice = (2.0 * inter + eps) / (card + eps)
    return 1.0 - dice[present].mean()


class SegLoss(nn.Module):
    """``ce_weight * weighted CE + dice_weight * Dice`` ignoring label 255."""

    def __init__(
        self, class_weights: torch.Tensor | None, ce_weight: float = 1.0, dice_weight: float = 1.0
    ) -> None:
        super().__init__()
        self.register_buffer("class_weights", class_weights)
        self.ce_weight = ce_weight
        self.dice_weight = dice_weight

    def forward(self, logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        logits = logits.float()
        if not bool((target != LABEL_NODATA).any()):
            return logits.sum() * 0.0
        w = self.class_weights
        ce = F.cross_entropy(
            logits,
            target,
            weight=w if isinstance(w, torch.Tensor) else None,
            ignore_index=LABEL_NODATA,
        )
        return self.ce_weight * ce + self.dice_weight * dice_loss(logits, target)


# --------------------------------------------------------------------------- mlflow


class _Tracker:
    """Thin MLflow wrapper; a no-op when disabled."""

    def __init__(self, cfg: MlflowConfig, out_dir: Path) -> None:
        self.cfg = cfg
        self.out_dir = out_dir
        self.run_id: str | None = None

    @contextmanager
    def run(self, run_id: str | None) -> Iterator[_Tracker]:
        if not self.cfg.enabled:
            yield self
            return
        import mlflow

        uri = (
            self.cfg.tracking_uri
            or "sqlite:///" + (self.out_dir / "mlflow.db").resolve().as_posix()
        )
        mlflow.set_tracking_uri(uri)
        client = mlflow.MlflowClient()
        exp = client.get_experiment_by_name(self.cfg.experiment)
        if exp is None:
            exp_id = client.create_experiment(
                self.cfg.experiment,
                artifact_location=(self.out_dir / "mlartifacts").resolve().as_uri(),
            )
        else:
            exp_id = exp.experiment_id
        with mlflow.start_run(
            run_id=run_id, experiment_id=None if run_id else exp_id, run_name=self.cfg.run_name
        ) as r:
            self.run_id = r.info.run_id
            yield self

    def params(self, params: dict[str, str]) -> None:
        if self.cfg.enabled:
            import mlflow

            mlflow.log_params({k: v[:500] for k, v in params.items()})

    def metrics(self, metrics: dict[str, float], step: int) -> None:
        if self.cfg.enabled:
            import mlflow

            mlflow.log_metrics({k: v for k, v in metrics.items() if math.isfinite(v)}, step=step)

    def artifact(self, path: Path) -> None:
        if self.cfg.enabled:
            import mlflow

            mlflow.log_artifact(str(path))


# --------------------------------------------------------------------------- loop


@dataclass
class TrainResult:
    out_dir: Path
    last_path: Path
    best_path: Path
    history: list[dict[str, float]]
    best_score: float
    run_id: str | None


def _train_epoch(
    net: nn.Module,
    loader: DataLoader[tuple[torch.Tensor, torch.Tensor]],
    loss_fn: SegLoss,
    opt: torch.optim.Optimizer,
    scaler: torch.amp.GradScaler,
    device: torch.device,
    use_amp: bool,
) -> float:
    net.train()
    total, n = 0.0, 0
    for x, y in loader:
        x, y = x.to(device, non_blocking=True), y.to(device, non_blocking=True)
        opt.zero_grad(set_to_none=True)
        with torch.autocast(device_type=device.type, enabled=use_amp):
            logits = net(x)
        loss = loss_fn(logits, y)
        scaler.scale(loss).backward()
        scaler.step(opt)
        scaler.update()
        total += float(loss.detach()) * x.shape[0]
        n += x.shape[0]
    return total / max(n, 1)


@torch.no_grad()
def _validate(
    net: nn.Module,
    loader: DataLoader[tuple[torch.Tensor, torch.Tensor]],
    loss_fn: SegLoss,
    device: torch.device,
    use_amp: bool,
    n_classes: int,
) -> dict[str, float]:
    net.eval()
    total, n = 0.0, 0
    cm = np.zeros((n_classes, n_classes), dtype=np.int64)
    for x, y in loader:
        x, y = x.to(device), y.to(device)
        with torch.autocast(device_type=device.type, enabled=use_amp):
            logits = net(x)
        total += float(loss_fn(logits, y)) * x.shape[0]
        n += x.shape[0]
        pred = logits.argmax(1).cpu().numpy().astype(np.uint8)
        cm += segmentation_metrics(
            y.cpu().numpy().astype(np.uint8), pred, num_classes=n_classes
        ).confusion
    tp = np.diag(cm).astype(float)
    denom = cm.sum(0) + cm.sum(1) - tp
    iou = np.where(denom > 0, tp / np.maximum(denom, 1), np.nan)
    out = {"val_loss": total / max(n, 1)}
    out["val_miou"] = float(np.nanmean(iou)) if np.isfinite(iou).any() else float("nan")
    for c in range(n_classes):
        out[f"val_iou_{c}"] = float(iou[c])
    return out


def cosine_lr(base: float, epoch: int, epochs: int) -> float:
    """Cosine decay from ``base`` (epoch 0) towards 0; stateless, so resume needs no state."""
    return base * 0.5 * (1.0 + math.cos(math.pi * epoch / max(epochs, 1)))


def _atomic_save(obj: dict[str, Any], path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    torch.save(obj, tmp)
    tmp.replace(path)


def train(cfg: TrainConfig, *, resume: bool | None = None) -> TrainResult:
    """Run (or resume) training as configured; returns paths and per-epoch history."""
    resume = cfg.output.resume if resume is None else resume
    out_dir = Path(cfg.output.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    last_path, best_path = out_dir / LAST, out_dir / BEST
    seed_everything(cfg.seed)
    device = resolve_device(cfg.train.device)
    use_amp = cfg.train.amp and device.type == "cuda"
    n_classes = cfg.model.classes

    prepared = prepare_data(cfg.data)
    digest = prepared.digest()
    ckpt: dict[str, Any] | None = None
    if resume and last_path.exists():
        ckpt = torch.load(last_path, map_location="cpu", weights_only=True)
        assert ckpt is not None
        if ckpt.get("format") != CHECKPOINT_FORMAT:
            raise ValueError(f"{last_path} is not a {CHECKPOINT_FORMAT} checkpoint")
        if ckpt["split_digest"] != digest:
            raise ValueError("checkpoint was trained on a different split; refusing to resume")
        log.info("resuming from %s at epoch %d", last_path, ckpt["epoch"])

    if ckpt is not None:
        normalizer = normalizer_from_dict(ckpt["model"]["normalizer"])
        class_counts = np.asarray(ckpt["class_counts"], dtype=np.int64)
    else:
        normalizer, class_counts = fit_train_statistics(prepared, n_classes)
    if cfg.train.class_weights == "auto":
        weights: npt.NDArray[np.float32] | None = auto_class_weights(
            class_counts, cfg.train.class_weight_max
        )
    elif cfg.train.class_weights is None:
        weights = None
    else:
        weights = np.asarray(cfg.train.class_weights, dtype=np.float32)
        if weights.shape != (n_classes,):
            raise ValueError(f"class_weights needs {n_classes} values")

    split_path = out_dir / "split.json"
    split_path.write_text(json.dumps(prepared.summary()) + "\n", "utf-8")
    config_path = out_dir / "config.json"
    config_path.write_text(json.dumps(cfg.to_dict(), indent=2) + "\n", "utf-8")

    norm_ds = _normalised_datasets(prepared, normalizer, cfg.data.stride)
    train_set = TorchPatches(
        norm_ds, prepared.train_items, augment=cfg.train.augment, seed=cfg.seed
    )
    val_set = TorchPatches(norm_ds, prepared.val_items)

    net = build_unet(cfg.model).to(device)
    loss_fn = SegLoss(
        None if weights is None else torch.from_numpy(weights),
        cfg.train.ce_weight,
        cfg.train.dice_weight,
    ).to(device)
    opt = torch.optim.AdamW(net.parameters(), lr=cfg.train.lr, weight_decay=cfg.train.weight_decay)
    scaler = torch.amp.GradScaler(device.type, enabled=use_amp)

    start_epoch, history, best_score, run_id = 0, [], -math.inf, None
    if ckpt is not None:
        net.load_state_dict(ckpt["model"]["state_dict"])
        opt.load_state_dict(ckpt["optimizer"])
        scaler.load_state_dict(ckpt["scaler"])
        start_epoch = int(ckpt["epoch"]) + 1
        history = [dict(h) for h in ckpt["history"]]
        best_score = float(ckpt["best_score"])
        run_id = ckpt.get("mlflow_run_id") or None

    tracker = _Tracker(cfg.mlflow, out_dir)
    pin = device.type == "cuda"
    with tracker.run(run_id):
        if ckpt is None:
            tracker.params(
                {
                    **_flatten(cfg.to_dict()),
                    "device": str(device),
                    "n_train_patches": str(len(prepared.train_items)),
                    "n_val_patches": str(len(prepared.val_items)),
                    "class_weights": json.dumps(None if weights is None else weights.tolist()),
                    "class_counts_train": json.dumps(class_counts.tolist()),
                    "split_digest": digest,
                }
            )
            tracker.artifact(split_path)
            tracker.artifact(config_path)
        for epoch in range(start_epoch, cfg.train.epochs):
            torch.manual_seed(cfg.seed * 1_000_003 + epoch)
            train_set.epoch = epoch
            gen = torch.Generator().manual_seed(cfg.seed * 1_000_003 + epoch)
            loader = DataLoader(
                train_set,
                batch_size=cfg.train.batch_size,
                shuffle=True,
                generator=gen,
                num_workers=cfg.train.num_workers,
                drop_last=len(train_set) > cfg.train.batch_size,
                pin_memory=pin,
            )
            lr = cosine_lr(cfg.train.lr, epoch, cfg.train.epochs)
            for group in opt.param_groups:
                group["lr"] = lr
            row: dict[str, float] = {"epoch": float(epoch), "lr": lr}
            row["train_loss"] = _train_epoch(net, loader, loss_fn, opt, scaler, device, use_amp)
            if len(val_set):
                vloader = DataLoader(
                    val_set,
                    batch_size=cfg.train.batch_size,
                    num_workers=cfg.train.num_workers,
                    pin_memory=pin,
                )
                row.update(_validate(net, vloader, loss_fn, device, use_amp, n_classes))
                score = row["val_miou"]
            else:
                score = -row["train_loss"]
            history.append(row)
            improved = math.isfinite(score) and score > best_score
            if improved:
                best_score = score
                save_model(best_path, net, normalizer, cfg.model, epoch=epoch, score=score)
            _atomic_save(
                {
                    "format": CHECKPOINT_FORMAT,
                    "epoch": epoch,
                    "model": model_payload(net, normalizer, cfg.model),
                    "optimizer": opt.state_dict(),
                    "scaler": scaler.state_dict(),
                    "history": history,
                    "best_score": best_score,
                    "class_counts": class_counts.tolist(),
                    "split_digest": digest,
                    "mlflow_run_id": tracker.run_id or "",
                    "config": cfg.to_dict(),
                },
                last_path,
            )
            tracker.metrics(
                {k: v for k, v in row.items() if k != "epoch"} | {"best_score": best_score}, epoch
            )
            log.info("epoch %d %s", epoch, {k: round(v, 4) for k, v in row.items()})
        if not best_path.exists():  # e.g. only non-finite scores
            save_model(
                best_path, net, normalizer, cfg.model, epoch=len(history) - 1, score=best_score
            )
    return TrainResult(out_dir, last_path, best_path, history, best_score, tracker.run_id)


# --------------------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ml.train", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--config", required=True, type=Path, help="YAML config (ml/configs/unet.yaml)"
    )
    parser.add_argument("--out-dir", help="override output.dir")
    parser.add_argument("--epochs", type=int, help="override train.epochs")
    parser.add_argument("--no-resume", action="store_true", help="ignore an existing last.pt")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raw = yaml.safe_load(args.config.read_text("utf-8"))
    if args.out_dir:
        raw.setdefault("output", {})["dir"] = args.out_dir
    if args.epochs is not None:
        raw.setdefault("train", {})["epochs"] = args.epochs
    result = train(config_from_dict(raw), resume=False if args.no_resume else None)
    print(json.dumps({"best": str(result.best_path), "best_score": result.best_score}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
