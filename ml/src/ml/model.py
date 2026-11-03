"""U-Net segmentation model and a NumPy-facing predictor for evaluation and inference.

The network is a ``segmentation_models_pytorch`` U-Net with ``in_channels=18`` (the composite
bands of ``contracts/bands.yaml``) and one output channel per class of
``contracts/classes.yaml`` (channel index == class id). ``smp`` adapts the encoder's first
convolution to 18 input channels; with ``encoder_weights="imagenet"`` it re-uses the RGB
filters cyclically and rescales them, with ``encoder_weights=None`` it is randomly initialised
(tests and offline use never download weights).

Preprocessing contract (training and inference must match):

1. per-band standardisation with the ``BandNormalizer`` fitted on the *train* split only,
2. non-finite values (cloud / nodata NaN) replaced by 0.0 after normalisation (= band mean).

``UNetPredictor`` takes **raw** composite arrays (like ``ml.baseline_rf``: build the
``PatchDataset`` with ``normalize=None`` and ``nan_policy="keep"``), applies the steps above
itself, and is both an ``ml.evaluate`` predictor and an ``ml.infer.ProbaModel``. Pixels with
any non-finite band are NaN in every probability channel (-> nodata 255).

Model files (``save_model`` / ``load_predictor``) are plain ``torch.save`` dicts of tensors
and primitives, loaded with ``weights_only=True``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import segmentation_models_pytorch as smp  # type: ignore[import-untyped]
import torch
import torch.nn.functional as F
from torch import nn

from ml.dataset import BandNormalizer, Patch
from ml.metrics import LABEL_NODATA, NUM_CLASSES

N_BANDS = 18
MODEL_FORMAT = "peatwatch-unet/1"


@dataclass(frozen=True)
class ModelConfig:
    """U-Net architecture. ``decoder_channels`` needs one entry per encoder stage."""

    encoder_name: str = "resnet34"
    encoder_weights: str | None = None
    encoder_depth: int = 5
    decoder_channels: tuple[int, ...] = (256, 128, 64, 32, 16)
    in_channels: int = N_BANDS
    classes: int = NUM_CLASSES

    def __post_init__(self) -> None:
        object.__setattr__(self, "decoder_channels", tuple(int(c) for c in self.decoder_channels))
        if len(self.decoder_channels) != self.encoder_depth:
            raise ValueError(
                f"decoder_channels needs {self.encoder_depth} entries, "
                f"got {len(self.decoder_channels)}"
            )

    @property
    def size_multiple(self) -> int:
        """Input height and width must be multiples of this."""
        return int(2**self.encoder_depth)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["decoder_channels"] = list(self.decoder_channels)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelConfig:
        unknown = set(d) - {f for f in cls.__dataclass_fields__}
        if unknown:
            raise ValueError(f"unknown model config keys: {sorted(unknown)}")
        kw = dict(d)
        if "decoder_channels" in kw:
            kw["decoder_channels"] = tuple(kw["decoder_channels"])
        return cls(**kw)


def build_unet(cfg: ModelConfig) -> nn.Module:
    """Build the U-Net (logits output, no activation)."""
    net: nn.Module = smp.Unet(
        encoder_name=cfg.encoder_name,
        encoder_weights=cfg.encoder_weights,
        encoder_depth=cfg.encoder_depth,
        decoder_channels=list(cfg.decoder_channels),
        in_channels=cfg.in_channels,
        classes=cfg.classes,
    )
    return net


def preprocess(
    window: npt.NDArray[np.float32], normalizer: BandNormalizer
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.bool_]]:
    """Raw (C, h, w) -> (normalised with NaN->0, invalid mask (h, w))."""
    x = np.asarray(window, dtype=np.float32)
    invalid = ~np.isfinite(x).all(axis=0)
    xn = np.nan_to_num(normalizer(x), nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
    return xn, invalid


@dataclass
class UNetPredictor:
    """Wraps a trained U-Net: ``predict_proba`` (``ProbaModel``) and ``__call__`` (evaluate)."""

    net: nn.Module
    normalizer: BandNormalizer
    config: ModelConfig = field(default_factory=ModelConfig)
    device: torch.device = field(default_factory=lambda: torch.device("cpu"))

    def __post_init__(self) -> None:
        self.net.to(self.device).eval()

    @torch.no_grad()
    def predict_proba(self, window: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        """(C, h, w) raw window -> (K, h, w) softmax probabilities, NaN where unusable."""
        if window.ndim != 3 or window.shape[0] != self.config.in_channels:
            raise ValueError(f"expected ({self.config.in_channels}, h, w), got {window.shape}")
        xn, invalid = preprocess(window, self.normalizer)
        _, h, w = xn.shape
        m = self.config.size_multiple
        t = torch.from_numpy(xn)[None].to(self.device)
        ph, pw = (-h) % m, (-w) % m
        if ph or pw:
            t = F.pad(t, (0, pw, 0, ph), mode="replicate")
        logits = self.net(t)[0, :, :h, :w]
        proba: npt.NDArray[np.float32] = (
            torch.softmax(logits.float(), dim=0).cpu().numpy().astype(np.float32)
        )
        proba[:, invalid] = np.nan
        return proba

    def __call__(self, patch: Patch) -> npt.NDArray[np.uint8]:
        """``ml.evaluate`` predictor: (H, W) uint8 class ids, 255 where unclassifiable."""
        proba = self.predict_proba(patch.array)
        out = np.argmax(np.nan_to_num(proba, nan=-1.0), axis=0).astype(np.uint8)
        out[np.isnan(proba).any(axis=0)] = LABEL_NODATA
        out[~patch.valid] = LABEL_NODATA
        return out


def normalizer_to_dict(n: BandNormalizer) -> dict[str, list[float]]:
    return {"mean": n.mean.ravel().tolist(), "std": n.std.ravel().tolist()}


def normalizer_from_dict(d: dict[str, Sequence[float]]) -> BandNormalizer:
    return BandNormalizer(d["mean"], d["std"])


def model_payload(
    net: nn.Module, normalizer: BandNormalizer, cfg: ModelConfig, **extra: Any
) -> dict[str, Any]:
    """The dict stored in a model file (also embedded in training checkpoints)."""
    return {
        "format": MODEL_FORMAT,
        "model_config": cfg.to_dict(),
        "normalizer": normalizer_to_dict(normalizer),
        "state_dict": {k: v.detach().cpu() for k, v in net.state_dict().items()},
        **extra,
    }


def save_model(
    path: Path | str, net: nn.Module, normalizer: BandNormalizer, cfg: ModelConfig, **extra: Any
) -> Path:
    """Write a self-contained model file (architecture, weights, normaliser)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    torch.save(model_payload(net, normalizer, cfg, **extra), tmp)
    tmp.replace(out)
    return out


def predictor_from_payload(
    payload: dict[str, Any], device: torch.device | str = "cpu"
) -> UNetPredictor:
    if payload.get("format") != MODEL_FORMAT:
        raise ValueError(f"not a {MODEL_FORMAT} model file (format={payload.get('format')!r})")
    # Weights come from the file: never download pretrained encoder weights here.
    cfg = ModelConfig.from_dict({**payload["model_config"], "encoder_weights": None})
    net = build_unet(cfg)
    net.load_state_dict(payload["state_dict"])
    return UNetPredictor(
        net, normalizer_from_dict(payload["normalizer"]), cfg, torch.device(device)
    )


def load_predictor(path: Path | str, device: torch.device | str = "cpu") -> UNetPredictor:
    """Load a model file written by ``save_model`` (or a training checkpoint) for inference."""
    payload = torch.load(Path(path), map_location="cpu", weights_only=True)
    if isinstance(payload.get("model"), dict):  # training checkpoint (ml.train last.pt)
        payload = payload["model"]
    return predictor_from_payload(payload, device)
