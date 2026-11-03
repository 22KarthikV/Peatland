"""Check that a trained U-Net file loads on CPU and predicts one dummy window.

    uv run python scripts/check_artifact.py artifacts/unet/best.pt

Exit code 0 means the artifact is usable by the CPU inference path (M10/M11).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from ml.model import N_BANDS, load_predictor


def check(path: Path, size: int = 64) -> dict[str, object]:
    """Load ``path`` on CPU and predict a random ``(18, size, size)`` window."""
    predictor = load_predictor(path, "cpu")
    rng = np.random.default_rng(0)
    window = rng.normal(0.1, 0.05, size=(N_BANDS, size, size)).astype(np.float32)
    window[:, :4, :4] = np.nan  # a masked corner, as in a cloudy composite
    proba = predictor.predict_proba(window)
    ok_shape = proba.shape[1:] == (size, size)
    finite = np.isfinite(proba[:, size // 2, size // 2]).all()
    sums_to_one = bool(np.isclose(proba[:, size // 2, size // 2].sum(), 1.0, atol=1e-3))
    if not (ok_shape and finite and sums_to_one):
        raise RuntimeError("prediction has the wrong shape or invalid probabilities")
    return {
        "classes": int(proba.shape[0]),
        "proba_shape": tuple(proba.shape),
        "masked_corner_is_nan": bool(np.isnan(proba[:, 0, 0]).all()),
        "centre_class": int(np.argmax(proba[:, size // 2, size // 2])),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("path", type=Path, help="best.pt (or last.pt) from ml.train")
    ap.add_argument("--size", type=int, default=64, help="dummy window edge in pixels")
    args = ap.parse_args(argv)
    if not args.path.is_file():
        print(f"error: {args.path} not found", file=sys.stderr)
        return 2
    try:
        info = check(args.path, args.size)
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {args.path} loads on CPU and predicts: {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
