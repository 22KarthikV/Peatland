import warnings

import numpy as np
import pytest
from pipeline.features import ndmi, ndvi, ndwi, slope_degrees, twi_proxy


def a(*v: float) -> np.ndarray:
    return np.array(v, dtype=np.float32)


@pytest.mark.parametrize(
    ("fn", "x", "y", "expected"),
    [
        (ndvi, 0.5, 0.1, 0.4 / 0.6),
        (ndwi, 0.3, 0.1, 0.2 / 0.4),
        (ndmi, 0.4, 0.2, 0.2 / 0.6),
    ],
)
def test_indices_known_values(fn, x, y, expected):
    out = fn(a(x), a(y))
    assert out.dtype == np.float32
    assert out[0] == pytest.approx(expected, rel=1e-6)


def test_index_nan_safe_and_zero_denominator():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = ndvi(a(0.0, np.nan, 0.5, 0.2), a(0.0, 0.1, np.nan, -0.2))
    assert np.isnan(out).all()


def test_index_range_and_sign():
    assert ndvi(a(1.0), a(0.0))[0] == 1.0
    assert ndwi(a(0.0), a(1.0))[0] == -1.0


def test_slope_known_values():
    x = np.arange(5, dtype=np.float32) * 10.0
    plane = np.tile(x, (5, 1))  # rise 10 per 10 m cell -> 45 degrees
    assert slope_degrees(plane, 10.0) == pytest.approx(np.full((5, 5), 45.0), abs=1e-4)
    assert slope_degrees(np.full((4, 4), 7.0), 30.0).max() == 0.0
    assert slope_degrees(plane, 10.0).dtype == np.float32


def test_slope_nan_and_bad_input():
    z = np.zeros((5, 5), dtype=np.float32)
    z[2, 2] = np.nan
    s = slope_degrees(z, 10.0)
    assert np.isnan(s[2, 1]) and np.isnan(s[1, 2]) and not np.isnan(s[0, 0])
    with pytest.raises(ValueError):
        slope_degrees(z, 0)


def test_twi_flat_exact_and_hollow_wetter_than_ridge():
    flat = twi_proxy(np.zeros((5, 5), dtype=np.float32), 30.0)
    expected = np.log(30.0 / np.tan(np.radians(0.1)))
    assert flat == pytest.approx(np.full((5, 5), expected), rel=1e-5)
    assert flat.dtype == np.float32

    bowl = np.full((5, 5), 10.0, dtype=np.float32)
    bowl[2, 2] = 8.0
    ridge = np.full((5, 5), 10.0, dtype=np.float32)
    ridge[2, 2] = 12.0
    assert twi_proxy(bowl, 30.0)[2, 2] > twi_proxy(ridge, 30.0)[2, 2]


def test_twi_steeper_is_drier_and_nan_propagates():
    x = np.arange(5, dtype=np.float32)
    gentle = twi_proxy(np.tile(x * 1.0, (5, 1)), 10.0)
    steep = twi_proxy(np.tile(x * 8.0, (5, 1)), 10.0)
    assert gentle[2, 2] > steep[2, 2]
    z = np.zeros((5, 5), dtype=np.float32)
    z[2, 2] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert np.isnan(twi_proxy(z, 10.0)[2, 2])
