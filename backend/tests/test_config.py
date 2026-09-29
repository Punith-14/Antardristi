"""
Configuration consistency.

These are the tests that stop the project quietly becoming dishonest. If someone
changes a threshold without re-validating, or adds an analysis without a
reliability figure, the suite fails.
"""

import json
from pathlib import Path

import pytest

from core import paths

from detection import sar
from detection.surface import ANALYSES, RELIABILITY_ORDER, catalogue

VALIDATION_FILE = paths.RESULTS / "surface_validation.json"


# ------------------------------------------------------------ every analysis

def test_every_analysis_declares_the_essentials():
    for name, config in ANALYSES.items():
        for key in ("label", "index", "threshold", "direction", "quantity",
                    "domain", "question"):
            assert key in config, f"{name} is missing {key}"


def test_direction_is_above_or_below():
    for name, config in ANALYSES.items():
        assert config["direction"] in ("above", "below"), name


def test_quantities_are_unique():
    """Two analyses sharing a quantity name would collide in the evidence
    record and in the report's subject detection."""
    quantities = [c["quantity"] for c in ANALYSES.values()]
    assert len(quantities) == len(set(quantities))


# ------------------------------------------------------------- validation

def test_validated_analyses_carry_full_scores():
    for name, config in ANALYSES.items():
        validation = config.get("validation")
        if validation is None:
            continue
        for key in ("iou", "precision", "recall", "reliability", "caveat"):
            assert key in validation, f"{name} validation is missing {key}"
        assert 0 <= validation["iou"] <= 1
        assert validation["reliability"] in RELIABILITY_ORDER


def test_reliability_matches_the_measured_score():
    """A method cannot claim to be good while scoring 0.057."""
    for name, config in ANALYSES.items():
        validation = config.get("validation")
        if not validation:
            continue

        iou, reliability = validation["iou"], validation["reliability"]
        if reliability == "good":
            assert iou >= 0.7, f"{name} claims good at IoU {iou}"
        elif reliability == "moderate":
            assert 0.4 <= iou < 0.7, f"{name} claims moderate at IoU {iou}"
        elif reliability == "poor":
            assert iou < 0.5, f"{name} claims poor at IoU {iou}"


def test_failing_methods_say_so_plainly():
    """A caveat on a broken method must be unambiguous. 'Somewhat limited' is
    not good enough when IoU is 0.057."""
    for name, config in ANALYSES.items():
        validation = config.get("validation")
        if not validation or validation["reliability"] != "poor":
            continue
        caveat = validation["caveat"].lower()
        assert any(
            phrase in caveat
            for phrase in ("does not work", "not be relied on", "only usable")
        ), f"{name} understates its failure"


def test_crop_stress_is_honestly_unvalidated():
    """No ground truth exists for moisture stress. Inventing a proxy would
    misrepresent the confidence available."""
    assert "validation" not in ANALYSES["crop_stress"]


def test_thresholds_match_the_validation_run():
    """If someone edits a threshold, they must re-run validate_surface.py.
    Skipped when the results file is absent."""
    if not VALIDATION_FILE.exists():
        pytest.skip("surface_validation.json not present")

    results = json.loads(VALIDATION_FILE.read_text(encoding="utf-8"))["analyses"]
    for name, config in ANALYSES.items():
        measured = results.get(name)
        if not measured or measured.get("validated") is False:
            continue
        assert config["threshold"] == pytest.approx(
            measured["measured_threshold"]
        ), f"{name} threshold no longer matches the validation run"


# -------------------------------------------------------------- catalogue

def test_catalogue_puts_reliable_methods_first():
    """A platform listing a 0.888 method and a 0.057 method as equal options is
    misleading by layout alone."""
    entries = catalogue()
    order = [RELIABILITY_ORDER[e["reliability"]] for e in entries]
    assert order == sorted(order)
    assert entries[0]["type"] == "vegetation_health"
    assert entries[-1]["type"] == "crop_stress"


def test_catalogue_recommends_only_what_works():
    for entry in catalogue():
        if entry["reliability"] in ("poor", "unvalidated"):
            assert not entry["recommended"], entry["type"]
        else:
            assert entry["recommended"], entry["type"]


def test_catalogue_exposes_the_numbers_not_just_a_label():
    for entry in catalogue():
        if entry["validated"]:
            assert entry["iou"] is not None
            assert entry["caveat"]


# ------------------------------------------------------------------- sar

def test_flood_threshold_is_the_measured_one():
    """-20 dB on the mean of VV and VH, chosen by sweeping both bands over 441
    hand-labelled chips. Otsu, the textbook choice, scored 0.186 against
    0.489."""
    assert sar.DEFAULT_DB == -20.0
    assert sar.FALLBACK_DB == sar.DEFAULT_DB
    assert sar.POLARISATIONS == ("VV", "VH")


def test_otsu_bounds_are_physically_plausible_for_water():
    assert sar.WATER_DB_MIN < sar.WATER_DB_MAX < 0
    assert sar.WATER_DB_MIN <= sar.DEFAULT_DB <= sar.WATER_DB_MAX


def test_the_shipped_rule_beats_the_one_it_replaced_on_every_metric():
    """The whole justification for fusing the bands. VV-only scored IoU 0.441,
    precision 0.764, recall 0.511; if any of these three slips below that, the
    change has stopped paying for itself and should be reverted, not defended.
    """
    previous = {"iou": 0.441, "precision": 0.763, "recall": 0.511}
    for metric, was in previous.items():
        assert sar.VALIDATION[metric] >= was, (
            f"{metric} regressed against the VV-only rule: "
            f"{sar.VALIDATION[metric]} < {was}"
        )


def test_validation_numbers_live_in_exactly_one_place():
    """main.py restated these once and they went stale - it was still claiming
    the VV-only figures after the threshold moved, on the endpoint the frontend
    shows users as our accuracy claim.

    Checked by reading the source rather than importing the app, so this runs
    without FastAPI installed and cannot be quietly skipped.
    """
    from pathlib import Path

    source = (paths.BACKEND / "main.py").read_text(
        encoding="utf-8"
    )

    for metric in ("iou", "precision", "recall"):
        literal = f'"{metric}": {sar.VALIDATION[metric]}'
        assert literal not in source, (
            f"main.py carries its own copy of {metric}. Read sar.VALIDATION "
            "instead - two copies drift."
        )

    assert "sar.VALIDATION" in source


def test_the_accuracy_caveat_is_arithmetic_not_prose():
    """It once read "around half of true flooding is expected to be missed" -
    correct at recall 0.511, wrong at 0.574, and nothing failed when it went
    stale. Any word that restates a number can rot; the number cannot.
    """
    from pathlib import Path

    source = (paths.BACKEND / "pipeline" / "analysis.py").read_text(
        encoding="utf-8"
    )
    caveat = source.split("This detection method scores")[1][:600]

    for word in ("a quarter", "around half", "roughly half", "a third"):
        assert word not in caveat.lower(), (
            f"the accuracy caveat says {word!r} instead of computing it - "
            "it will be wrong the next time the model is retuned"
        )


def test_the_single_pol_fallback_moves_the_threshold_too():
    """Dropping VH is not just dropping a band: the VV-only optimum sits 3 dB
    higher. A fallback that kept -20 dB would run VV at a threshold measured for
    the fused band and quietly under-detect."""
    assert sar.VV_ONLY_DB != sar.DEFAULT_DB
    assert sar.VV_ONLY_DB > sar.DEFAULT_DB


def test_the_fallback_does_not_claim_the_better_rules_accuracy():
    """If these ever match, the weaker path is reporting the stronger path's
    numbers into a user-facing evidence record."""
    assert sar.VV_ONLY_VALIDATION["iou"] < sar.VALIDATION["iou"]
    assert sar.VV_ONLY_VALIDATION["recall"] < sar.VALIDATION["recall"]
    assert sar.VV_ONLY_VALIDATION["dataset"] != sar.VALIDATION["dataset"]


def test_the_evaluation_mirror_matches_what_ships():
    """Earth Engine cannot run numpy, so backend/detection/sar.py and
    evaluation/thresholds.py are two implementations of one method. If they
    drift, the evaluation measures something other than what users get - and it
    drifts silently, because both halves keep working.
    """
    import sys
    from pathlib import Path

    evaluation = paths.EVALUATION
    if str(evaluation) not in sys.path:
        sys.path.insert(0, str(evaluation))

    import thresholds

    assert thresholds.SHIPPED_DB == sar.DEFAULT_DB, (
        "the evaluation harness is scoring a different threshold than the one "
        "in production"
    )


def test_sar_states_its_recall_ceiling():
    """Recall 0.574 is a measured limit of single-date thresholding, not a
    tuning gap. That distinction belongs with the code, not only in a report."""
    assert "brighter" in sar.RECALL_CEILING_NOTE
    assert any("0.574" in c for c in sar.KNOWN_CONFUSIONS)


def test_sar_ships_its_known_confusions():
    """Failure modes travel with the result rather than living in a limitations
    section nobody reads."""
    text = " ".join(sar.KNOWN_CONFUSIONS).lower()
    assert "shadow" in text
    assert "vegetation" in text
