"""
Dual-polarisation fusion rules and the missing-recall diagnostic.

These run against a synthetic scene rather than real chips, so the logic is
verified before anyone spends an hour of Colab time discovering a sign error.
The scene encodes the scattering physics the rules depend on - if the physics
in the comments is wrong, these tests are wrong too, which is the point of
writing the expected values out by hand.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
if str(EVALUATION) not in sys.path:
    sys.path.insert(0, str(EVALUATION))

from diagnostics import ErrorProfile, interpret  # noqa: E402
from metrics import evaluate  # noqa: E402
from thresholds import (  # noqa: E402
    DUAL_METHODS,
    dual_fixed_vh,
    dual_fixed_vv,
    dual_intersection,
    dual_mean,
    dual_open_water_or_double_bounce,
    dual_union,
)


# Backscatter in dB for five surfaces, in the order a Sentinel-1 scene would
# actually show them. The VV-VH gap is what the fusion rules key on.
#
#   surface              VV     VH    gap   why
#   open water          -22    -28      6   specular, pulse reflects away
#   flooded vegetation   -6    -16     10   double bounce, keeps polarisation
#   dry vegetation       -9    -15      6   volume scattering depolarises
#   built-up             -3    -14     11   double bounce too - the confounder
#   bare soil           -12    -20      8   rough surface scattering
SURFACES = {
    "open_water": (-22.0, -28.0),
    "flooded_vegetation": (-6.0, -16.0),
    "dry_vegetation": (-9.0, -15.0),
    "built_up": (-3.0, -14.0),
    "bare_soil": (-12.0, -20.0),
}


def scene():
    """One row per surface, in the order above."""
    names = list(SURFACES)
    vv = np.array([[SURFACES[n][0]] * 4 for n in names])
    vh = np.array([[SURFACES[n][1]] * 4 for n in names])
    return vv, vh, names


def row_of(names, surface):
    return names.index(surface)


# ------------------------------------------------------- single polarisation

def test_vv_only_finds_open_water_and_nothing_else():
    """The method that ships today. Open water is the only surface dark enough
    to clear -17 dB, which is exactly why recall is 0.511."""
    vv, vh, names = scene()
    mask, info = dual_fixed_vv(-17.0)(vv, vh)

    assert mask[row_of(names, "open_water")].all()
    for surface in ("flooded_vegetation", "dry_vegetation", "built_up", "bare_soil"):
        assert not mask[row_of(names, surface)].any(), surface
    assert info["source"] == "vv_only"


def test_vh_threshold_must_sit_lower_than_the_vv_one():
    """Cross-polarised return is weaker over the same surface, so a VH rule
    reusing the VV threshold would call everything water."""
    vv, vh, names = scene()

    sensible, _ = dual_fixed_vh(-22.0)(vv, vh)
    assert sensible[row_of(names, "open_water")].all()
    assert not sensible[row_of(names, "dry_vegetation")].any()

    borrowed, _ = dual_fixed_vh(-17.0)(vv, vh)
    assert borrowed[row_of(names, "bare_soil")].any(), (
        "a VH rule at the VV threshold should over-detect - if it does not, "
        "the synthetic VH values are unrealistically high"
    )


# -------------------------------------------------------------------- fusion

def test_union_never_detects_less_than_either_band_alone():
    vv, vh, _ = scene()
    union, _ = dual_union(-17.0, -22.0)(vv, vh)
    vv_only, _ = dual_fixed_vv(-17.0)(vv, vh)
    vh_only, _ = dual_fixed_vh(-22.0)(vv, vh)

    assert (union >= vv_only).all()
    assert (union >= vh_only).all()


def test_intersection_never_detects_more_than_either_band_alone():
    vv, vh, _ = scene()
    both, _ = dual_intersection(-17.0, -22.0)(vv, vh)
    vv_only, _ = dual_fixed_vv(-17.0)(vv, vh)
    vh_only, _ = dual_fixed_vh(-22.0)(vv, vh)

    assert (both <= vv_only).all()
    assert (both <= vh_only).all()


def test_union_raises_recall_and_intersection_raises_precision():
    """The whole reason to test fusion: they move the two metrics in opposite
    directions, and recall is the one we are short of."""
    vv = np.array([[-22.0, -19.0, -9.0, -3.0]])
    vh = np.array([[-28.0, -30.0, -15.0, -14.0]])
    #               water   water   land   land
    truth = np.array([[1, 1, 0, 0]])

    # A VV threshold that catches only the first pixel.
    strict_vv = -20.0
    base, _ = dual_fixed_vv(strict_vv)(vv, vh)
    union, _ = dual_union(strict_vv, -25.0)(vv, vh)

    assert evaluate(union, truth)["recall"] > evaluate(base, truth)["recall"]


def test_mean_sits_between_the_two_bands():
    vv, vh, names = scene()
    mask, info = dual_mean(-19.5)(vv, vh)

    # open water mean is (-22 + -28) / 2 = -25, below -19.5
    assert mask[row_of(names, "open_water")].all()
    # bare soil mean is (-12 + -20) / 2 = -16, above -19.5
    assert not mask[row_of(names, "bare_soil")].any()
    assert info["source"] == "mean"


# ------------------------------------------------------------ double bounce

def test_double_bounce_arm_catches_flooded_vegetation():
    """The hypothesis under test: water under a canopy is BRIGHT, so a darkness
    threshold cannot see it, but its return stays co-polarised."""
    vv, vh, names = scene()
    mask, _ = dual_open_water_or_double_bounce(-17.0, 9.0)(vv, vh)

    assert mask[row_of(names, "open_water")].all(), "open water arm broken"
    assert mask[row_of(names, "flooded_vegetation")].all(), (
        "flooded vegetation has a 10 dB gap and should trigger the "
        "double-bounce arm"
    )


def test_double_bounce_arm_leaves_dry_vegetation_alone():
    """Volume scattering in a dry canopy depolarises, narrowing the gap. If
    this fails the rule is just a brightness detector."""
    vv, vh, names = scene()
    mask, _ = dual_open_water_or_double_bounce(-17.0, 9.0)(vv, vh)

    assert not mask[row_of(names, "dry_vegetation")].any()
    assert not mask[row_of(names, "bare_soil")].any()


def test_double_bounce_arm_also_catches_built_up_and_that_is_the_known_cost():
    """Towns double-bounce exactly like flooded forest. This test exists so the
    failure mode is recorded rather than discovered in a demo."""
    vv, vh, names = scene()
    mask, _ = dual_open_water_or_double_bounce(-17.0, 9.0)(vv, vh)

    assert mask[row_of(names, "built_up")].all(), (
        "built-up should trigger the double-bounce arm - if it stops doing so, "
        "the precision caveat in thresholds.py needs rewriting"
    )


def test_double_bounce_arms_do_not_overlap():
    """Open water and double bounce must stay separable, or the diagnostics
    cannot attribute recovered pixels to either mechanism."""
    vv, vh, _ = scene()
    rule = dual_open_water_or_double_bounce(-17.0, 9.0)
    combined, _ = rule(vv, vh)

    open_water = vv < -17.0
    double_bounce = (vv >= -17.0) & ((vv - vh) > 9.0)

    assert not (open_water & double_bounce).any()
    assert (combined == (open_water | double_bounce)).all()


# -------------------------------------------------------------- missing data

@pytest.mark.parametrize("name", list(DUAL_METHODS))
def test_no_method_calls_missing_data_water(name):
    """Non-finite pixels appear at chip edges and in the radar shadow. Calling
    them water inflates extent with pixels no sensor observed."""
    vv = np.array([[np.nan, -22.0], [np.inf, -3.0]])
    vh = np.array([[-28.0, np.nan], [-28.0, -14.0]])

    mask, _ = DUAL_METHODS[name](vv, vh)
    assert not mask[0, 0], f"{name} called NaN water"
    assert not mask[1, 0], f"{name} called inf water"


def test_every_method_is_scored_over_the_same_pixels():
    """The reason every rule demands both bands even when it reads one.

    If a VH rule could score pixels where VV is missing, it would be graded
    over a larger set than the VV rule, and the comparison table would be
    putting numbers side by side that were never measured on the same ground.
    """
    vv = np.array([[np.nan, -22.0, -6.0, -3.0]])
    vh = np.array([[-28.0, np.nan, -16.0, -14.0]])

    masks = {name: method(vv, vh)[0] for name, method in DUAL_METHODS.items()}
    unusable = ~np.isfinite(vv) | ~np.isfinite(vh)

    for name, mask in masks.items():
        assert not (mask & unusable).any(), (
            f"{name} scored a pixel where one band is missing"
        )


@pytest.mark.parametrize("name", list(DUAL_METHODS))
def test_every_method_returns_a_boolean_mask_of_the_right_shape(name):
    vv, vh, _ = scene()
    mask, info = DUAL_METHODS[name](vv, vh)

    assert mask.shape == vv.shape
    assert mask.dtype == bool
    assert "threshold" in info and "source" in info


# --------------------------------------------------------------- diagnostics

def test_missed_water_is_binned_by_brightness():
    """Three missed pixels, one per bin, placed by hand."""
    #                marginal  moderate  bright
    vv = np.array([[-15.0, -12.0, -6.0]])
    vh = np.array([[-21.0, -20.0, -16.0]])
    truth = np.array([[1, 1, 1]])
    pred = np.array([[False, False, False]])

    profile = ErrorProfile(threshold=-17.0, bright_db=-10.0, marginal_width=3.0)
    profile.update(vv, vh, truth, pred)
    missed = profile.summary()["missed_water"]

    assert missed["bins"] == {"marginal": 1, "moderate": 1, "bright": 1}
    assert missed["total_pixels"] == 3


def test_only_bright_pixels_with_a_wide_gap_count_as_double_bounce():
    """Bright alone is not enough. A bright pixel with a narrow gap is a dry
    canopy, not flooded vegetation."""
    #              bright + wide gap (10)   bright + narrow gap (6)
    vv = np.array([[-6.0, -9.0]])
    vh = np.array([[-16.0, -15.0]])
    truth = np.array([[1, 1]])
    pred = np.array([[False, False]])

    profile = ErrorProfile(threshold=-17.0, double_bounce_gap=9.0)
    profile.update(vv, vh, truth, pred)
    missed = profile.summary()["missed_water"]

    assert missed["bins"]["bright"] == 2
    assert missed["bright_and_double_bounce_pixels"] == 1


def test_nodata_and_non_finite_pixels_are_excluded():
    vv = np.array([[-22.0, -22.0, np.nan]])
    vh = np.array([[-28.0, -28.0, -28.0]])
    truth = np.array([[1, -1, 1]])
    pred = np.array([[False, False, False]])

    profile = ErrorProfile(threshold=-17.0)
    profile.update(vv, vh, truth, pred)

    assert profile.summary()["missed_water"]["total_pixels"] == 1


def test_outcomes_are_separated_correctly():
    vv = np.array([[-22.0, -6.0, -22.0]])
    vh = np.array([[-28.0, -16.0, -28.0]])
    truth = np.array([[1, 1, 0]])
    pred = np.array([[True, False, True]])
    #                  tp     fn     fp

    profile = ErrorProfile(threshold=-17.0)
    profile.update(vv, vh, truth, pred)
    outcomes = profile.summary()["by_outcome"]

    assert outcomes["true_positive"]["pixels"] == 1
    assert outcomes["false_negative"]["pixels"] == 1
    assert outcomes["false_positive"]["pixels"] == 1


def test_pooled_median_survives_being_accumulated_in_pieces():
    """446 chips are pooled as histograms rather than held as arrays. The
    pooled median must match what a single pass would give."""
    rng = np.random.default_rng(0)
    values = rng.normal(-20.0, 2.0, size=(10, 200))

    whole = ErrorProfile(threshold=-17.0)
    whole.update(values, values - 6.0, np.ones_like(values, dtype=int),
                 np.ones_like(values, dtype=bool))

    pieces = ErrorProfile(threshold=-17.0)
    for chunk in np.array_split(values, 5, axis=0):
        pieces.update(chunk, chunk - 6.0, np.ones_like(chunk, dtype=int),
                      np.ones_like(chunk, dtype=bool))

    a = whole.summary()["by_outcome"]["true_positive"]["vv_median_db"]
    b = pieces.summary()["by_outcome"]["true_positive"]["vv_median_db"]
    assert a == b
    assert abs(a - np.median(values)) < 0.5  # within one bin width


def test_empty_profile_does_not_divide_by_zero():
    summary = ErrorProfile().summary()
    assert summary["missed_water"]["total_pixels"] == 0
    assert interpret(summary) == ["No missed water pixels to analyse."]


# ------------------------------------------------------------ interpretation

def test_a_small_double_bounce_share_is_reported_as_a_negative_result():
    """The most likely outcome, and the one it would be tempting to bury."""
    vv = np.array([[-15.0] * 99 + [-6.0]])
    vh = np.array([[-21.0] * 99 + [-16.0]])
    truth = np.ones((1, 100), dtype=int)
    pred = np.zeros((1, 100), dtype=bool)

    profile = ErrorProfile(threshold=-17.0)
    profile.update(vv, vh, truth, pred)
    text = " ".join(interpret(profile.summary()))

    assert "NOT the main cause" in text
    assert "negative result" in text


def test_a_large_double_bounce_share_recommends_the_fusion_rule_with_a_warning():
    vv = np.array([[-6.0] * 100])
    vh = np.array([[-16.0] * 100])
    truth = np.ones((1, 100), dtype=int)
    pred = np.zeros((1, 100), dtype=bool)

    profile = ErrorProfile(threshold=-17.0)
    profile.update(vv, vh, truth, pred)
    text = " ".join(interpret(profile.summary()))

    assert "worth adding" in text
    assert "built-up" in text


def test_the_label_caveat_is_always_stated():
    """Whatever the numbers say, the ground truth was drawn optically. That
    limit applies to every reading of this experiment."""
    vv = np.array([[-15.0]])
    vh = np.array([[-21.0]])
    profile = ErrorProfile(threshold=-17.0)
    profile.update(vv, vh, np.array([[1]]), np.array([[False]]))

    text = " ".join(interpret(profile.summary()))
    assert "Sen1Floods11 labels" in text
    assert "false positive" in text
