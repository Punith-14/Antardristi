"""
The numpy mirror of the optical flood rule, which notebook 05 scores.

What the notebook measures is only worth anything if it is the rule that
ships, scored on the same pixels as radar. These tests hold both.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
EVALUATION = BACKEND.parent / "evaluation"
for path in (BACKEND, EVALUATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import optical as eval_optical  # noqa: E402  (evaluation/optical.py)
from metrics import evaluate  # noqa: E402


def test_the_scored_threshold_is_the_shipped_threshold():
    """Two implementations of one method. If they drift, the notebook measures
    a rule nobody runs."""
    pytest.importorskip("ee")
    from detection import optical as shipped

    assert eval_optical.SHIPPED_MNDWI == shipped.THRESHOLD


def test_the_band_indices_are_green_and_swir1():
    assert eval_optical.S2HAND_BANDS[eval_optical.GREEN] == "B3"
    assert eval_optical.S2HAND_BANDS[eval_optical.SWIR1] == "B11"
    assert len(eval_optical.S2HAND_BANDS) == 13


def test_mndwi_is_the_normalised_difference():
    assert eval_optical.mndwi(np.array([3.0]), np.array([1.0]))[0] == pytest.approx(0.5)
    assert eval_optical.mndwi(np.array([1.0]), np.array([3.0]))[0] == pytest.approx(-0.5)


def test_no_data_is_nan_not_zero():
    """0 is a real MNDWI value - a confident 'not water'. A pixel with no data
    must not be scored as one."""
    index = eval_optical.mndwi(np.array([0.0, np.nan]), np.array([0.0, 1.0]))
    assert np.isnan(index).all()


def test_the_rule_never_calls_missing_data_water():
    rule = eval_optical.fixed_mndwi()
    pred, _ = rule(np.array([np.nan, 0.0, 900.0]), np.array([1.0, 0.0, 100.0]))
    assert pred.tolist() == [False, False, True]


def test_both_sensors_are_scored_on_the_same_pixels():
    """Otherwise each sensor is excused from the ground the other found hard,
    and the comparison measures the excuses."""
    truth = np.array([1, 1, 0, -1, 0])
    vv = np.array([-25.0, np.nan, -5.0, -25.0, -5.0])      # radar missing pixel 1
    green = np.array([900.0, 900.0, np.nan, 900.0, 100.0])  # optical missing pixel 2

    valid = eval_optical.common_valid(truth, vv, green)
    assert valid.tolist() == [True, False, False, False, True]

    scored = eval_optical.restrict(truth, valid)
    assert scored.tolist() == [1, -1, -1, -1, 0]


def test_restricted_labels_are_skipped_by_the_metric():
    truth = np.array([1, 1, 0, 0])
    pred = np.array([True, False, False, True])
    valid = np.array([True, False, True, False])

    full = evaluate(pred, truth)
    common = evaluate(pred, eval_optical.restrict(truth, valid))
    assert common["iou"] == 1.0
    assert full["iou"] < 1.0


def test_the_caveat_names_the_circularity():
    caveat = eval_optical.LABEL_CAVEAT
    assert "MNDWI" in caveat
    assert "biased upward" in caveat
    assert "cloud" in caveat.lower()
    assert "Level-1C" in caveat
