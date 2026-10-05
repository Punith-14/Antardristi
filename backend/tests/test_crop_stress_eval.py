"""
Item 7: the crop-stress measurement (evaluation/crop_stress.py, notebook 11).

Kept as future work: notebook 11 was removed when the measurement was set
aside. The VCI reading, POD/FAR scoring, decision rule and reference lists
stay tested so the work can be picked up again.
"""


import numpy as np
import pytest

# Importing the green-cover tests also puts evaluation/ on sys.path.
from tests.test_green_cover_eval import BACKEND, _constants

import crop_stress as CS  # noqa: E402



def test_thresholds_are_the_drought_manuals():
    assert (CS.SEVERE, CS.MODERATE) == (40, 60)
    assert [CS.category(v) for v in (12.0, 39.9, 40.0, 59.9, 60.0, 95.0)] == \
        ["severe", "severe", "moderate", "moderate", "normal", "normal"]
    assert CS.category(float("nan")) == "unknown"


def test_the_worse_of_two_and_a_missing_index():
    out = CS.worse_of_two([30, np.nan, 70, np.nan], [50, 45, np.nan, np.nan])
    assert out[:3].tolist() == [30, 45, 70] and np.isnan(out[3])
    assert CS.flagged([39.9, 40, np.nan]).tolist() == [True, False, False]


def test_pod_and_far():
    c = CS.contingency(np.array([True] * 7 + [False] * 3), np.array([True, False, False, False]))
    assert (c["hits"], c["misses"], c["false_alarms"], c["correct_negatives"]) == (7, 3, 1, 3)
    assert c["pod"] == pytest.approx(0.7) and c["far"] == pytest.approx(1 / 8)
    assert CS.contingency(np.array([], bool), np.array([], bool))["pod"] == 0.0


def test_reference_lists_are_clean():
    declared = {(d["state"], d["district"]) for d in CS.DECLARED_2015}
    undeclared = {(d["state"], d["district"]) for d in CS.NOT_DECLARED_2015}
    assert len(declared) == len(CS.DECLARED_2015) == 31
    assert not declared & undeclared
    declared_states = {s for s, share in CS.DECLARED_SHARE_2015.items() if share > 0}
    assert not {s for s, _ in undeclared} & declared_states, "a 'not declared' district sits in a declaring state"
    assert all(d.get("source") for d in CS.DECLARED_2015)
    assert {"Bathinda", "Sirsa", "Hisar", "Mansa"}.isdisjoint({d for _, d in undeclared}), "whitefly, not drought"


def test_history_never_includes_the_year_being_judged():
    found = _constants(BACKEND / "detection" / "seasonal.py", "KHARIF_MONTHS")
    assert found["KHARIF_MONTHS"] == (8, 9, 10)
    text = (BACKEND / "detection" / "seasonal.py").read_text(encoding="utf-8")
    assert "if y != year" in text
    assert "not_water(year, month)" in text, "a flood must not read as drought"


def test_spearman_and_state_level():
    assert CS.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert CS.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert np.isnan(CS.spearman([1, 2], [1, 2]))
    rows = [{"state": "Orissa", "crop_share": 0.5, "combined": 20.0},
            {"state": "Orissa", "crop_share": 0.5, "combined": 70.0},
            {"state": "Orissa", "crop_share": 0.01, "combined": 10.0},
            {"state": "Punjab", "crop_share": 0.8, "combined": 80.0},
            {"state": "Andhra Pradesh", "crop_share": 0.8, "combined": 10.0}]
    shares = CS.state_shares(rows)
    assert shares == {"Orissa": 0.5, "Punjab": 0.0, "Andhra Pradesh": 1.0}
    pairs = CS.state_pairs(shares, gaul_names={"Odisha": ["Orissa"]})
    assert ("Odisha", 0.90, 0.5) in pairs and ("Punjab", 0.0, 0.0) in pairs
    assert not any(p[0] in ("Andhra Pradesh", "Telangana") for p in pairs)


def test_decide():
    good = {"pod": 0.8, "far": 0.1, "accuracy": 0.85, "hits": 24, "misses": 6}
    assert CS.decide(good, 0.1, 0.0)["ship"]
    for score, normal, unresolved, why in [
        ({**good, "pod": 0.6}, 0.1, 0.0, "POD"),
        ({**good, "far": 0.4}, 0.1, 0.0, "FAR"),
        (good, 0.3, 0.0, "2019"),
        (good, 0.1, 0.25, "unmatched"),
    ]:
        d = CS.decide(score, normal, unresolved)
        assert not d["ship"] and any(r.startswith("FAIL") and why in r for r in d["reasons"]), d
    block = CS.validation_block(good, 0.1, {"ship": True})
    assert block["reliability"] == "moderate" and "24 of 30" in block["caveat"]
    assert CS.validation_block(good, 0.1, {"ship": False})["reliability"] == "unvalidated"
