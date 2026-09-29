"""
The before/after swipe, from the backend's side.

The frontend draws two tile layers and clips the top one. That only works if
the backend actually emits a baseline layer, and only means anything if the
baseline was measured the same way as the flood layer - same threshold, same
relative orbit. A baseline computed any other way would show the user
differences that are threshold drift or viewing geometry, dressed up as water.

Earth Engine is not available here, so these check the parts that do not need
it: the guard clauses, and the source-level invariants that keep the comparison
honest.
"""

import re
from pathlib import Path

import pytest

import analysis

BACKEND = Path(__file__).resolve().parent.parent
SOURCE = (BACKEND / "analysis.py").read_text(encoding="utf-8")


# ------------------------------------------------------------- guard clauses

def test_no_tiles_without_a_flood_mask():
    """Every other key is optional; this one is the point of the call."""
    assert analysis.tile_urls({}) is None
    assert analysis.tile_urls({"_internal": {}}) is None
    assert analysis.tile_urls({"_internal": {"flood_mask": None}}) is None


def test_a_missing_internal_block_does_not_raise():
    """tile_urls runs on a cached payload too, and a cache entry that lost its
    internal block must degrade to "no map" rather than take the response with
    it."""
    assert analysis.tile_urls({"_internal": None}) is None
    assert analysis.tile_urls({"region": {"name": "Kerala"}}) is None


# --------------------------------------------------- the comparison contract

def test_the_baseline_uses_the_same_threshold_as_the_flood_window():
    """If the baseline re-fits its own threshold, the swipe shows threshold
    drift as though it were water. The post threshold has to be passed in."""
    call = SOURCE.split("pre_mask, _, pre_info = sar.detect_water(")[1][:400]
    assert 'threshold_db=post_info["threshold"]' in call


def test_the_baseline_uses_the_same_relative_orbit():
    """Backscatter depends on viewing geometry. Comparing an ascending pre
    against a descending post produces differences that are geometry, not
    flooding."""
    call = SOURCE.split("pre_mask, _, pre_info = sar.detect_water(")[1][:400]
    assert "relative_orbit=orbit" in call


def test_the_baseline_mask_survives_to_the_response():
    """It is computed inside a try block. If it stays scoped there, the swipe
    silently never appears and nothing fails."""
    assert '"baseline_mask": baseline_mask' in SOURCE
    assert re.search(r"^\s*baseline_mask = None\s*$", SOURCE, re.MULTILINE), (
        "baseline_mask must be initialised before the try block, or a window "
        "with no imagery raises NameError instead of skipping the swipe"
    )


def test_permanent_water_is_excluded_from_the_baseline_too():
    """Both layers must subtract permanent water, or the swipe shows rivers
    and reservoirs appearing out of nowhere."""
    assert "baseline_mask = pre_mask.And(permanent.Not())" in SOURCE


# --------------------------------------------------------------- readability

def test_the_two_layers_are_different_hues_not_two_blues():
    """The swipe has to be readable at a glance, including for anyone who
    cannot separate two shades of the same colour."""
    flood = analysis.FLOOD_PALETTE.lstrip("#")
    baseline = analysis.BASELINE_PALETTE.lstrip("#")
    assert flood != baseline

    def rgb(value):
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))

    # Compare perceived brightness rather than raw channel distance: two
    # colours can differ numerically and still look identical on a map.
    def luminance(value):
        r, g, b = rgb(value)
        return 0.299 * r + 0.587 * g + 0.114 * b

    assert abs(luminance(flood) - luminance(baseline)) > 40, (
        "the baseline and flood layers are too close in brightness to tell "
        "apart when the divider is not moving"
    )


@pytest.mark.parametrize("name", ["flood_tiles", "baseline_tiles", "backscatter_tiles"])
def test_the_documented_keys_are_the_ones_emitted(name):
    """tile_urls' docstring is what the frontend is written against."""
    assert name in analysis.tile_urls.__doc__
    assert f'"{name}"' in SOURCE


def test_the_contract_matches_what_the_backend_emits(contract):
    """The contract is the worked example every workstream builds against. It
    described a PNG-based shape long after the API moved to tile templates."""
    artifacts = contract["artifacts"]

    assert "flood_tiles" in artifacts
    assert "baseline_tiles" in artifacts, (
        "the contract is in comparison mode, so it must carry the baseline "
        "layer the swipe needs"
    )
    for key in ("pre_image_url", "post_image_url", "overlay_image_url"):
        assert key not in artifacts, f"{key} is the old PNG shape, no longer emitted"


def test_the_contract_carries_both_periods_for_the_swipe_labels(contract):
    """The divider labels read the pre and post windows straight off the
    response."""
    period = contract["period"]
    assert period["mode"] == "comparison"
    assert period["pre"]["start"] and period["post"]["start"]
