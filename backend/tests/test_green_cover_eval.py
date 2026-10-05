"""
Item 6: the green-cover measurement (evaluation/green_cover.py, notebook 10).

Kept as future work: notebook 10 was removed when the measurement was set
aside (it needs more Earth Engine compute than the free tier gives). The
split, scoring, decision rule and district-name matching stay tested so the
work can be picked up again.
"""

import ast
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
EVALUATION = BACKEND.parent / "evaluation"
if str(EVALUATION) not in sys.path:
    sys.path.insert(0, str(EVALUATION))

import districts as D  # noqa: E402
import green_cover as G  # noqa: E402


def _constants(path, *names):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in names:
                value = node.value
                if isinstance(value, ast.BinOp):          # LIST_A + ["x"]
                    left = out[value.left.id]
                    out[node.targets[0].id] = left + ast.literal_eval(value.right)
                else:
                    out[node.targets[0].id] = ast.literal_eval(value)
    return out


# ------------------------------------------------------------- the split

def test_train_and_test_never_share_a_state():
    assert G.states_are_disjoint()
    assert len(G.by_role(G.TRAIN)) >= 7 and len(G.by_role(G.TEST)) >= 6
    crop = [d for d in G.by_role(G.TEST) if d["landscape"] == "cropland"]
    assert len(crop) >= 3, "the Punjab-type failure must be tested on unseen cropland"
    bad = G.DISTRICTS + [{"state": "Punjab", "district": "Patiala", "role": G.TEST}]
    assert not G.states_are_disjoint(bad)


def test_the_notebook_samples_the_features_the_backend_builds():
    found = _constants(BACKEND / "detection" / "seasonal.py", "GREEN_FEATURES_A", "GREEN_FEATURES_B",
                       "BASELINE_THRESHOLD")
    assert found["GREEN_FEATURES_A"] == G.FEATURES_A
    assert found["GREEN_FEATURES_B"] == G.FEATURES_B
    assert found["BASELINE_THRESHOLD"] == 0.4


# ------------------------------------------------------------- scoring

def test_counts_and_scores():
    c = G.counts([1, 1, 0, 0, 1], [1, 0, 1, 0, 1])
    assert c == {"tp": 2, "fp": 1, "fn": 1, "tn": 1}
    s = G.scores(c)
    assert s["iou"] == pytest.approx(0.5) and s["precision"] == pytest.approx(2 / 3)
    assert G.scores({"tp": 0, "fp": 0, "fn": 0, "tn": 9})["iou"] == 0.0
    assert G.pooled([c, c])["tp"] == 4


def test_bootstrap_range_sits_above_zero_only_for_a_real_gain():
    base = {f"d{i}": {"tp": 10, "fp": 30, "fn": 5, "tn": 100} for i in range(6)}
    better = {k: {"tp": 14, "fp": 5, "fn": 1, "tn": 125} for k in base}
    lo, hi = G.paired_bootstrap_gain(base, better)
    assert 0 < lo <= hi
    lo, hi = G.paired_bootstrap_gain(base, dict(base))
    assert lo == hi == 0


def _s(iou, precision=0.8):
    return {"iou": iou, "precision": precision, "recall": 0.8}


def test_decide_ships_only_when_every_part_passes():
    ok = G.decide(_s(0.40), _s(0.62), (0.05, 0.3), _s(0.05, 0.1), _s(0.40, 0.7), _s(0.35), _s(0.5))
    assert ok["ship"], ok["reasons"]
    assert all(r.startswith("PASS") for r in ok["reasons"])


@pytest.mark.parametrize("args, why", [
    ((_s(0.40), _s(0.54), (0.05, 0.3), _s(0.05), _s(0.4), _s(0.3), _s(0.5)), "held-out IoU"),
    ((_s(0.50), _s(0.58), (0.01, 0.2), _s(0.05), _s(0.4), _s(0.3), _s(0.5)), "gain over"),
    ((_s(0.40), _s(0.62), (-0.01, 0.3), _s(0.05), _s(0.4), _s(0.3), _s(0.5)), "95% range"),
    ((_s(0.40), _s(0.62), (0.05, 0.3), _s(0.30), _s(0.20), _s(0.3), _s(0.5)), "cropland IoU"),
    ((_s(0.40), _s(0.62), (0.05, 0.3), _s(0.05), _s(0.40, 0.45), _s(0.3), _s(0.5)), "cropland precision"),
    ((_s(0.40), _s(0.62), (0.05, 0.3), _s(0.05), _s(0.40), _s(0.5), _s(0.45)), "Dynamic World"),
])
def test_decide_refuses_each_failure(args, why):
    decision = G.decide(*args)
    assert not decision["ship"]
    assert any(r.startswith("FAIL") and why in r for r in decision["reasons"]), decision["reasons"]


def test_height_has_to_earn_its_place():
    yes, no = {"ship": True}, {"ship": False}
    assert G.choose(yes, 0.60, yes, 0.61) == "A"
    assert G.choose(yes, 0.60, yes, 0.63) == "B"
    assert G.choose(no, 0.50, yes, 0.58) == "B"
    assert G.choose(yes, 0.60, no, 0.70) == "A"
    assert G.choose(no, 0.5, None, None) is None


def test_validation_block_reads_like_the_others():
    block = G.validation_block(_s(0.63, 0.7), {"Karnal": _s(0.31), "Bastar": _s(0.9)},
                               {"Karnal": "cropland"}, "RF", {"ship": True})
    assert block["reliability"] == "good" and block["iou"] == 0.63
    assert "Karnal" in block["caveat"] and "cropland" in block["caveat"]
    assert G.plain({"x": np.float64("nan"), "y": np.int64(3), "z": np.bool_(True)}) == {"x": None, "y": 3, "z": True}


# ------------------------------------------------------------- district names

TABLE = [("Karnataka", "Bijapur"), ("Chhattisgarh", "Bijapur"), ("Karnataka", "Gulbarga"),
         ("Andhra Pradesh", "Mahbubnagar"), ("Andhra Pradesh", "Rangareddi"),
         ("Maharashtra", "Aurangabad"), ("Bihar", "Aurangabad"), ("Orissa", "Cuttack"),
         ("Uttaranchal", "Dehra Dun"), ("West Bengal", "Barddhaman")]


def test_names_resolve_within_the_right_state():
    assert D.resolve("Karnataka", "Vijayapura", TABLE, ["Bijapur"]) == ("Karnataka", "Bijapur")
    assert D.resolve("Chhattisgarh", "Bijapur", TABLE) == ("Chhattisgarh", "Bijapur")
    assert D.resolve("Maharashtra", "Aurangabad", TABLE) == ("Maharashtra", "Aurangabad")
    assert D.resolve("Telangana", "Mahbubnagar", TABLE) == ("Andhra Pradesh", "Mahbubnagar")
    assert D.resolve("Telangana", "Ranga Reddy", TABLE, ["Rangareddy", "Rangareddi"]) == ("Andhra Pradesh", "Rangareddi")
    assert D.resolve("Odisha", "Cuttack", TABLE) == ("Orissa", "Cuttack")
    assert D.resolve("Uttarakhand", "Dehradun", TABLE) == ("Uttaranchal", "Dehra Dun")


def test_a_miss_is_reported_never_guessed():
    assert D.resolve("Karnataka", "Kalaburagi", TABLE) is None, "no alias: must not guess Gulbarga"
    assert D.resolve("Kerala", "Bijapur", TABLE) is None, "right name, wrong state"
    matched, missing = D.resolve_all([{"state": "Karnataka", "district": "Kalaburagi", "aliases": ["Gulbarga"]},
                                      {"state": "Kerala", "district": "Idukki"}], TABLE)
    assert matched == {("Karnataka", "Kalaburagi"): ("Karnataka", "Gulbarga")}
    assert missing == [("Kerala", "Idukki")]
    assert D.suggestions("Karnataka", "Bijapura", TABLE)[0] == "Bijapur"
