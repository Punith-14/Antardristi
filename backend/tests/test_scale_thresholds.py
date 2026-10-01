"""
The flood threshold follows the scale the analysis runs at.

Notebook 07 found the service applying a threshold measured on 10 m pixels to
200 m pixels that Earth Engine had averaged. These tests tie the shipped
per-scale thresholds to the measurement file, and check the rule reaches the
evidence record, the cache key and the catalogue.
"""

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

pytest.importorskip("ee")

from detection import sar  # noqa: E402

RESULTS = BACKEND.parent / "evaluation" / "spatial_results.json"


@pytest.fixture
def measured():
    if not RESULTS.exists():
        pytest.skip("evaluation/spatial_results.json not present")
    return json.loads(RESULTS.read_text(encoding="utf-8"))


@pytest.mark.parametrize("scale,label", [(100, "100 m"), (200, "200 m")])
def test_each_threshold_is_notebook_07s_train_chosen_value(measured, scale, label):
    chosen = measured["step1_best_threshold_at_scale"][label]
    assert sar.SCALE_RULES[scale]["db"] == chosen["db"]


@pytest.mark.parametrize("scale,label", [(100, "100 m"), (200, "200 m")])
def test_each_validation_is_the_test_split_score_at_that_scale(measured, scale, label):
    test = measured["step1_best_threshold_at_scale"][label]["test"]
    validation = sar.SCALE_RULES[scale]["validation"]
    for metric in ("iou", "precision", "recall"):
        assert validation[metric] == round(test["pooled"][metric], 3), metric
    assert str(test["chips"]) in validation["dataset"]
    assert f"{scale} m" in validation["dataset"]


def test_the_scale_thresholds_beat_minus_20_where_they_run(measured):
    """The reason for the change, kept checkable: at both coarse scales the
    train-chosen threshold beats -20 dB on the test split."""
    as_runs = measured["step1_as_it_runs_on_test"]
    best = measured["step1_best_threshold_at_scale"]
    for label in ("100 m", "200 m"):
        assert best[label]["test"]["pooled"]["iou"] > as_runs[label]["pooled"]["iou"]


def test_10_m_keeps_the_original_measured_rule():
    assert sar.SCALE_RULES[10]["db"] == sar.DEFAULT_DB == -20.0
    assert sar.SCALE_RULES[10]["validation"] is sar.VALIDATION


@pytest.mark.parametrize("scale,expected,exact", [
    (10, 10, True), (100, 100, True), (200, 200, True),
    (150, 200, False), (120, 100, False), (30, 10, False), (500, 200, False),
])
def test_unmeasured_scales_use_the_nearest_and_say_so(scale, expected, exact):
    rule = sar.rule_for_scale(scale)
    assert rule["measured_at_m"] == expected
    assert rule["exact"] is exact
    assert rule["db"] == sar.SCALE_RULES[expected]["db"]


def test_bad_scale_values_do_not_crash():
    for value in (None, "abc", 0, -5):
        assert sar.rule_for_scale(value)["measured_at_m"] in sar.SCALE_RULES


def test_the_rule_returns_a_copy_not_the_shared_dict():
    rule = sar.rule_for_scale(200)
    rule["validation"]["iou"] = 0.0
    assert sar.SCALE_RULES[200]["validation"]["iou"] != 0.0


# ------------------------------------------------- through the flood path

def run_at(monkeypatch, scale, info_extra):
    import flood_scenario as S
    from pipeline import analysis

    region = S.install(monkeypatch)
    real = S.fake_sar_detect

    def detect(*args, **kwargs):
        mask, composite, info = real(*args, **kwargs)
        info.update(info_extra)
        return mask, composite, info

    monkeypatch.setattr(sar, "detect_water", detect)
    return analysis.analyse_flood(region, dict(S.META), *S.POST,
                                  force_sensor="sentinel-1", scale=scale)


def test_the_method_text_names_the_scale_the_threshold_was_measured_at(monkeypatch):
    result = run_at(monkeypatch, 200, {"threshold": -18.5, "threshold_scale_m": 200,
                                        "threshold_scale_exact": True})
    method = next(e for e in result["evidence"] if e["quantity"] == "flood_extent")["method"]
    assert "-18.5 dB for 200 m analysis" in method


def test_an_unmeasured_scale_is_disclosed_in_the_result(monkeypatch):
    result = run_at(monkeypatch, 150, {"threshold_scale_m": 200,
                                        "threshold_scale_exact": False})
    notes = " ".join(result["unobserved"]["notes"])
    assert "measured at 200 m and applied at 150 m" in notes


# -------------------------------------------------------------- the catalogue

def test_the_catalogue_reports_the_scale_the_frontend_runs_at():
    pytest.importorskip("fastapi")
    import main

    flood = main.list_analyses()["flood"]
    assert flood["validation"] == sar.SCALE_RULES[200]["validation"]
    assert set(flood["validation_by_scale"]) == {"10 m", "100 m", "200 m"}
    assert flood["validation_by_scale"]["200 m"]["threshold_db"] == -18.5
