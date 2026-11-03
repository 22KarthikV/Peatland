"""The Colab notebook is valid nbformat 4 JSON, has no outputs, and its code cells parse."""

import json
from pathlib import Path

import pytest

NB = Path(__file__).resolve().parents[1] / "notebooks" / "train.ipynb"


@pytest.fixture(scope="module")
def nb() -> dict:  # type: ignore[type-arg]
    return json.loads(NB.read_text("utf-8"))  # type: ignore[no-any-return]


def test_structure(nb: dict) -> None:  # type: ignore[type-arg]
    assert nb["nbformat"] == 4 and nb["nbformat_minor"] >= 5
    assert nb["cells"], "no cells"
    ids = [c["id"] for c in nb["cells"]]
    assert len(ids) == len(set(ids))
    for c in nb["cells"]:
        assert c["cell_type"] in {"markdown", "code"}
        assert isinstance(c["source"], list) and all(isinstance(s, str) for s in c["source"])
        if c["cell_type"] == "code":
            assert c["outputs"] == [] and c["execution_count"] is None


def test_code_cells_compile(nb: dict) -> None:  # type: ignore[type-arg]
    for i, c in enumerate(nb["cells"]):
        if c["cell_type"] == "code":
            compile("".join(c["source"]), f"cell{i}", "exec")


def test_notebook_matches_the_runbook_contract(nb: dict) -> None:  # type: ignore[type-arg]
    text = "\n".join("".join(c["source"]) for c in nb["cells"])
    for needle in (
        "https://github.com/22KarthikV/Peatland.git",
        "pip install",
        "-e ./ml",
        "segmentation-models-pytorch",
        'cfg["model"]["encoder_weights"] = "imagenet"',
        '"device": "cuda"',
        "ml.train",
        "check_artifact.py",
        "best.pt",
        "split.json",
        "config.json",
        "drive.mount",
    ):
        assert needle in text, needle
    assert "uv sync" not in "".join(
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"
    )
