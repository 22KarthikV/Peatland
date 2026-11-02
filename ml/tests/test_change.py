from __future__ import annotations

from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
import rasterio
from ml.change import GridMismatchError, change_colormap, detect_change, write_stats
from ml.stats import stats_csv
from rasterio.transform import Affine, from_origin

T = from_origin(400000, 6000000, 10, 10)

# fmt: off
A = np.array([[1, 1, 2, 255], [1, 3, 0, 2], [255, 1, 1, 1]], dtype=np.uint8)
B = np.array([[1, 3, 2, 1], [3, 3, 0, 255], [2, 1, 3, 1]], dtype=np.uint8)
EXPECTED = np.array(
    [[17, 19, 34, 255], [19, 51, 0, 255], [255, 17, 19, 17]], dtype=np.uint8
)
# fmt: on


def _write(p: Path, a: npt.NDArray[np.uint8], transform: Affine = T) -> Path:
    with rasterio.open(
        p,
        "w",
        driver="GTiff",
        width=a.shape[1],
        height=a.shape[0],
        count=1,
        dtype="uint8",
        crs="EPSG:32630",
        transform=transform,
        nodata=255,
    ) as d:
        d.write(a, 1)
    return p


def test_known_input(tmp_path: Path) -> None:
    fa, fb = _write(tmp_path / "a.tif", A), _write(tmp_path / "b.tif", B)
    out = tmp_path / "c.tif"
    stats = detect_change(fa, fb, out, window_size=2)  # several windows

    with rasterio.open(out) as s:
        got = s.read(1)
        assert s.nodata == 255
        assert s.dtypes[0] == "uint8"
        assert s.descriptions[0] == "change"
        assert s.crs.to_string() == "EPSG:32630"
        cmap = s.colormap(1)
    assert np.array_equal(got, EXPECTED)
    ref = change_colormap()
    assert cmap[1 * 16 + 1] == (0xDD, 0xDD, 0xDD, 255)
    assert cmap[1 * 16 + 3] == ref[1 * 16 + 3] == (0xD5, 0x5E, 0x00, 255)
    assert cmap[255] == (0, 0, 0, 0)

    # 12 pixels, 3 nodata in either input -> 9 valid
    assert stats["total_ha"] == 0.09
    got_t = {
        (t["from_class"]["id"], t["to_class"]["id"]): (t["area_ha"], t["percent"])
        for t in stats["transitions"]
    }
    assert got_t == {
        (0, 0): (0.01, 11.11),
        (1, 1): (0.03, 33.33),
        (1, 3): (0.03, 33.33),
        (2, 2): (0.01, 11.11),
        (3, 3): (0.01, 11.11),
    }
    assert stats["units"] == "hectares"
    assert stats["pixel_area_ha"] == 0.01
    fa_areas = {a["class"]["id"]: a["area_ha"] for a in stats["from_class_areas"]}
    ta_areas = {a["class"]["id"]: a["area_ha"] for a in stats["to_class_areas"]}
    assert fa_areas == {0: 0.01, 1: 0.06, 2: 0.01, 3: 0.01}
    assert ta_areas == {0: 0.01, 1: 0.03, 2: 0.01, 3: 0.04}

    csv_text = stats_csv(stats)
    lines = csv_text.splitlines()
    assert lines[0] == "from_class_id,from_class,to_class_id,to_class,area_ha,percent"
    assert "1,bog,3,felled_or_burnt_bare,0.03,33.33" in lines
    write_stats(stats, tmp_path / "s.json", tmp_path / "s.csv")
    assert (tmp_path / "s.csv").read_text("utf-8") == csv_text


def test_grid_mismatch(tmp_path: Path) -> None:
    fa = _write(tmp_path / "a.tif", A)
    shifted = _write(tmp_path / "b.tif", B, from_origin(400010, 6000000, 10, 10))
    with pytest.raises(GridMismatchError):
        detect_change(fa, shifted, tmp_path / "c.tif")
    small = _write(tmp_path / "d.tif", B[:2])
    with pytest.raises(GridMismatchError):
        detect_change(fa, small, tmp_path / "c.tif")
