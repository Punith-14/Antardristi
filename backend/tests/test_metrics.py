"""Evaluation metrics. Pure numpy, no Earth Engine, no network."""

import sys
from pathlib import Path

import numpy as np
import pytest

EVALUATION = Path(__file__).resolve().parent.parent.parent / "evaluation"
if str(EVALUATION) not in sys.path:
    sys.path.insert(0, str(EVALUATION))

from metrics import aggregate, confusion, evaluate, format_table  # noqa: E402
from thresholds import apply_threshold, clamped_otsu, otsu  # noqa: E402


# ------------------------------------------------------------ confusion

def test_perfect_prediction():
    truth = np.array([[1, 1], [0, 0]])
    scores = evaluate(truth.astype(bool), truth)
    assert scores["iou"] == 1.0
    assert scores["precision"] == 1.0
    assert scores["recall"] == 1.0


def test_nodata_is_excluded_from_every_metric():
    """Sen1Floods11 chips carry large unlabelled regions. Counting them as
    'not water' flatters any metric that has true negatives in it."""
    truth = np.array([[1, -1], [-1, 0]])
    pred = np.array([[1, 1], [1, 0]], dtype=bool)

    counts = confusion(pred, truth)
    assert counts["valid_pixels"] == 2
    assert counts["tp"] == 1
    assert counts["tn"] == 1
    assert counts["fp"] == 0


def test_predicting_nothing_scores_zero_iou_but_high_accuracy():
    """Why accuracy is useless here: water is a small minority of pixels, so a
    model predicting no water at all looks excellent on accuracy."""
    truth = np.zeros((10, 10), dtype=np.int8)
    truth[0, :] = 1                       # 10% water
    pred = np.zeros((10, 10), dtype=bool)

    scores = evaluate(pred, truth)
    assert scores["iou"] == 0.0
    assert scores["accuracy"] == 0.9
    assert scores["water_prevalence"] == pytest.approx(0.1)


def test_over_detection_hurts_precision_not_recall():
    truth = np.zeros((10, 10), dtype=np.int8)
    truth[0, :] = 1
    pred = np.ones((10, 10), dtype=bool)

    scores = evaluate(pred, truth)
    assert scores["recall"] == 1.0
    assert scores["precision"] == pytest.approx(0.1)


# ------------------------------------------------------------ aggregation

def test_micro_pools_pixels_and_macro_averages_chips():
    """They disagree for good reasons, and the gap is itself a finding: ours
    were 0.441 and 0.245, meaning performance depends on how much water there
    is."""
    big = evaluate(np.ones((10, 10), dtype=bool), np.ones((10, 10), dtype=np.int8))
    small = evaluate(np.zeros((2, 2), dtype=bool), np.ones((2, 2), dtype=np.int8))

    result = aggregate([big, small])
    assert result["micro"]["iou"] > result["macro"]["iou"]
    assert result["macro"]["iou"] == 0.5
    assert result["chips"] == 2


def test_aggregate_of_nothing_is_empty():
    assert aggregate([]) == {}


def test_table_renders_without_crashing():
    scores = evaluate(np.ones((4, 4), dtype=bool), np.ones((4, 4), dtype=np.int8))
    table = format_table({"fixed -17 dB": aggregate([scores])}, "Test")
    assert "fixed -17 dB" in table
    assert "IoU" in table


# -------------------------------------------------------------- thresholds

def test_otsu_finds_the_split_between_two_populations():
    rng = np.random.default_rng(0)
    values = np.concatenate([rng.normal(-20, 1, 5000), rng.normal(-8, 1, 5000)])
    assert -20 < otsu(values) < -8


def test_otsu_returns_none_when_it_cannot_split():
    assert otsu([]) is None
    assert otsu([1.0]) is None


def test_clamping_is_reported_not_silent():
    """Knowing the bound overruled the data matters: on Kerala, Otsu returned
    -10.99 and was clamped, which is why the extent was less reliable."""
    rng = np.random.default_rng(1)
    values = rng.normal(-5, 0.5, 4000)          # nothing water-like

    threshold, source, raw = clamped_otsu(values)
    assert source == "otsu_clamped"
    assert raw is not None
    assert threshold != raw


def test_water_is_below_the_threshold_not_above():
    """Water is dark in SAR: smooth surfaces reflect the pulse away."""
    image = np.array([[-25.0, -5.0], [-18.0, 0.0]])
    mask = apply_threshold(image, -17.0)
    assert mask.tolist() == [[True, False], [True, False]]


def test_non_finite_pixels_are_never_called_water():
    image = np.array([[np.nan, -25.0], [np.inf, -30.0]])
    mask = apply_threshold(image, -17.0)
    assert mask.tolist() == [[False, True], [False, True]]


def test_a_permissive_threshold_over_detects():
    """The -12 dB clamp gave precision 0.30 on real chips. This is the shape of
    that failure in miniature."""
    rng = np.random.default_rng(2)
    truth = (rng.random((100, 100)) < 0.15).astype(np.int8)
    image = np.where(truth == 1, rng.normal(-20, 2, (100, 100)),
                     rng.normal(-9, 2.5, (100, 100)))

    strict = evaluate(apply_threshold(image, -17.0), truth)
    loose = evaluate(apply_threshold(image, -12.0), truth)

    assert loose["recall"] > strict["recall"]
    assert loose["precision"] < strict["precision"]
    assert strict["iou"] > loose["iou"]
