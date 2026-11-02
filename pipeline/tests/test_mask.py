"""Tests for the SCL valid-pixel mask using synthetic arrays."""

from __future__ import annotations

import numpy as np
import pytest
from pipeline.mask import DEFAULT_EXCLUDE, valid_mask

KEPT = {2, 4, 5, 6, 7}


@pytest.mark.parametrize("cls", range(12))
def test_each_class_default(cls: int) -> None:
    scl = np.full((3, 3), cls, dtype=np.uint8)
    m = valid_mask(scl)
    assert m.dtype == np.bool_
    assert m.shape == (3, 3)
    assert bool(m.all()) == (cls in KEPT)
    assert bool((~m).all()) == (cls in DEFAULT_EXCLUDE)


def test_all_classes_in_one_array() -> None:
    scl = np.arange(12, dtype=np.uint8).reshape(3, 4)
    m = valid_mask(scl)
    assert {int(v) for v in scl[m]} == KEPT


def test_unclassified_parameter() -> None:
    scl = np.array([[7, 4]], dtype=np.uint8)
    assert valid_mask(scl).tolist() == [[True, True]]
    assert valid_mask(scl, exclude=DEFAULT_EXCLUDE | {7}).tolist() == [[False, True]]


def test_custom_exclude_only() -> None:
    scl = np.array([[0, 9]], dtype=np.uint8)
    assert valid_mask(scl, exclude=[9]).tolist() == [[True, False]]


def test_out_of_range_invalid() -> None:
    scl = np.array([[12, 255, 4]], dtype=np.uint8)
    assert valid_mask(scl).tolist() == [[False, False, True]]


def test_dilation_off_by_default_and_grows_cloud() -> None:
    scl = np.full((5, 5), 4, dtype=np.uint8)
    scl[2, 2] = 9
    assert int((~valid_mask(scl)).sum()) == 1
    m = valid_mask(scl, dilate_px=1)
    assert int((~m).sum()) == 9
    assert m[0, 0] and not m[1, 1]


def test_dilation_ignores_non_cloud_classes() -> None:
    scl = np.full((5, 5), 4, dtype=np.uint8)
    scl[2, 2] = 11  # snow is excluded but is not grown
    scl[0, 0] = 0
    m = valid_mask(scl, dilate_px=2)
    assert int((~m).sum()) == 2


def test_negative_dilation_rejected() -> None:
    with pytest.raises(ValueError):
        valid_mask(np.zeros((2, 2), dtype=np.uint8), dilate_px=-1)


def test_non_2d_rejected() -> None:
    with pytest.raises(ValueError):
        valid_mask(np.zeros(4, dtype=np.uint8))
