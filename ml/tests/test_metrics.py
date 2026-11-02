"""Known-input tests for per-class IoU/F1, means, confusion matrix and nodata masking."""

import numpy as np
import pytest
from ml.metrics import confusion_matrix, segmentation_metrics


def test_hand_computed_six_pixels() -> None:
    # Pairs (truth, pred): (0,0) (1,1) (1,2) (2,2) (2,2) (3,0)
    y_true = np.array([0, 1, 1, 2, 2, 3], dtype=np.uint8)
    y_pred = np.array([0, 1, 2, 2, 2, 0], dtype=np.uint8)
    m = segmentation_metrics(y_true, y_pred)

    np.testing.assert_array_equal(
        m.confusion,
        np.array([[1, 0, 0, 0], [0, 1, 1, 0], [0, 0, 2, 0], [1, 0, 0, 0]]),
    )
    # tp = (1, 1, 2, 0); fp = (1, 0, 1, 0); fn = (0, 1, 0, 1)
    np.testing.assert_allclose(m.iou, [0.5, 0.5, 2 / 3, 0.0])
    np.testing.assert_allclose(m.f1, [2 / 3, 2 / 3, 0.8, 0.0])
    assert m.mean_iou == pytest.approx(5 / 12)
    assert m.mean_f1 == pytest.approx(8 / 15)


def test_absent_class_is_nan_and_excluded_from_mean() -> None:
    # Classes 2 and 3 occur in neither truth nor prediction.
    y_true = np.array([0, 0, 1, 1], dtype=np.uint8)
    y_pred = np.array([0, 1, 1, 1], dtype=np.uint8)
    m = segmentation_metrics(y_true, y_pred)

    # tp = (1, 2); fp = (0, 1); fn = (1, 0)
    assert m.iou[0] == pytest.approx(0.5)
    assert m.iou[1] == pytest.approx(2 / 3)
    assert np.isnan(m.iou[2]) and np.isnan(m.iou[3])
    assert np.isnan(m.f1[2]) and np.isnan(m.f1[3])
    assert m.mean_iou == pytest.approx(7 / 12)  # mean of the two defined classes only
    assert m.mean_f1 == pytest.approx(11 / 15)


def test_nodata_pixels_are_ignored() -> None:
    # Pixel 1: prediction is nodata. Pixel 2: truth is nodata. Only pixels 0 and 3 count.
    y_true = np.array([0, 1, 255, 2], dtype=np.uint8)
    y_pred = np.array([0, 255, 1, 2], dtype=np.uint8)
    m = segmentation_metrics(y_true, y_pred)

    np.testing.assert_array_equal(m.confusion.sum(), 2)
    assert m.confusion[0, 0] == 1 and m.confusion[2, 2] == 1
    assert m.iou[0] == pytest.approx(1.0)
    assert m.iou[2] == pytest.approx(1.0)
    assert np.isnan(m.iou[1]) and np.isnan(m.iou[3])
    assert m.mean_iou == pytest.approx(1.0)


def test_nodata_value_does_not_change_scores() -> None:
    # Adding nodata-masked pixels with arbitrary valid-looking predictions must not matter.
    base_t = np.array([0, 1, 1, 2], dtype=np.uint8)
    base_p = np.array([0, 1, 2, 2], dtype=np.uint8)
    extra_t = np.array([255, 255, 3], dtype=np.uint8)
    extra_p = np.array([1, 3, 255], dtype=np.uint8)
    a = segmentation_metrics(base_t, base_p)
    b = segmentation_metrics(np.concatenate([base_t, extra_t]), np.concatenate([base_p, extra_p]))
    np.testing.assert_array_equal(a.confusion, b.confusion)
    assert a.mean_iou == pytest.approx(b.mean_iou)


def test_perfect_prediction_scores_one() -> None:
    y = np.array([[0, 1], [2, 3]], dtype=np.uint8)
    m = segmentation_metrics(y, y)
    np.testing.assert_allclose(m.iou, [1.0, 1.0, 1.0, 1.0])
    np.testing.assert_allclose(m.f1, [1.0, 1.0, 1.0, 1.0])
    assert m.mean_iou == pytest.approx(1.0)
    np.testing.assert_array_equal(m.confusion, np.diag([1, 1, 1, 1]))


def test_two_dimensional_input_matches_flat() -> None:
    t = np.array([[0, 1, 2], [3, 3, 1]], dtype=np.uint8)
    p = np.array([[0, 2, 2], [3, 1, 1]], dtype=np.uint8)
    flat = segmentation_metrics(t.ravel(), p.ravel())
    grid = segmentation_metrics(t, p)
    np.testing.assert_array_equal(flat.confusion, grid.confusion)
    assert flat.mean_iou == pytest.approx(grid.mean_iou)


def test_all_nodata_gives_nan_means() -> None:
    y = np.full((2, 2), 255, dtype=np.uint8)
    m = segmentation_metrics(y, np.zeros((2, 2), dtype=np.uint8))
    assert m.confusion.sum() == 0
    assert np.isnan(m.iou).all() and np.isnan(m.f1).all()
    assert np.isnan(m.mean_iou) and np.isnan(m.mean_f1)


def test_confusion_matrix_num_classes_and_dtype() -> None:
    cm = confusion_matrix(np.array([0, 4]), np.array([0, 4]), num_classes=5)
    assert cm.shape == (5, 5)
    assert cm.dtype == np.int64
    assert cm[0, 0] == 1 and cm[4, 4] == 1


def test_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="shape mismatch"):
        segmentation_metrics(np.zeros((2, 2), np.uint8), np.zeros((4,), np.uint8))


def test_out_of_range_class_raises() -> None:
    with pytest.raises(ValueError, match="class ids"):
        confusion_matrix(np.array([0, 4], np.uint8), np.array([0, 0], np.uint8))
    with pytest.raises(ValueError, match="class ids"):
        confusion_matrix(np.array([0, 1], np.int64), np.array([0, -1], np.int64))


def test_float_input_raises() -> None:
    with pytest.raises(TypeError):
        segmentation_metrics(np.array([0.0, 1.0]), np.array([0.0, 1.0]))
