"""scripts/check_artifact.py loads a saved model on CPU and predicts a dummy window."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytest.importorskip("torch")
pytest.importorskip("segmentation_models_pytorch")

from ml.dataset import BandNormalizer  # noqa: E402
from ml.model import ModelConfig, build_unet, save_model  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_artifact.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_artifact", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tiny_model(path: Path) -> Path:
    cfg = ModelConfig(
        encoder_name="resnet18", encoder_depth=3, decoder_channels=(32, 16, 8), encoder_weights=None
    )
    norm = BandNormalizer([0.0] * 18, [1.0] * 18)
    return save_model(path, build_unet(cfg), norm, cfg)


def test_check_passes_on_a_saved_model(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_script()
    best = _tiny_model(tmp_path / "best.pt")
    assert mod.main([str(best), "--size", "32"]) == 0
    assert "loads on CPU" in capsys.readouterr().out
    info = mod.check(best, 32)
    assert info["classes"] == 4 and info["masked_corner_is_nan"] is True


def test_check_fails_clearly_on_bad_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_script()
    assert mod.main([str(tmp_path / "missing.pt")]) == 2
    junk = tmp_path / "junk.pt"
    junk.write_bytes(b"not a model")
    assert mod.main([str(junk)]) == 1
    err = capsys.readouterr().err
    assert "not found" in err and "FAIL" in err
