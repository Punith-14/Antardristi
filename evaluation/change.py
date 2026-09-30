"""
Numpy mirror of the change-detection flood rule in backend/detection/sar.py.

Why change detection. Notebook 04 measured what the shipped darkness rule
misses: 28.3% of undetected water is brighter than -10 dB, and the median
missed pixel sits about 9 dB above the median caught one. That is a
different population, not a mistuned threshold - no single absolute cutoff
reaches it without flooding the map with false positives.

Change detection asks a different question of each pixel: not "is this dark?"
but "is this much darker than it was when dry?" Shallow water over rough
ground, mixed land/water pixels and wind-roughened floodwater can all stay
above -20 dB while still falling several dB below their own dry-season
value. A pixel that was -8 dB in May and -14 dB in August has almost
certainly changed, even though -14 dB is not dark enough to call water on its
own.

The rule, in dB on mean(VV, VH), the same fused band that ships:

    water  =  post < DARK_DB                              (the shipped rule)
           OR (post - pre < DROP_DB  AND  post < CEILING_DB)   (darkened)

The ceiling stops a large relative drop on bright ground - a harvested field,
a building shadow moving - from counting as water when the result is still
far too bright to be open water.

What is deliberately NOT in the rule: a brightening branch for flooded
vegetation (double bounce). Notebook 04 found only 7.7% of missed water
carries that signature, and Sen1Floods11's labels were drawn from optical
imagery that cannot see water under a canopy - so detecting it would score as
false positives. It cannot be validated on this dataset, so it is not shipped.

Measured (notebook 06, 428 chips): IoU 0.505 against 0.485 for darkness
alone. The gain comes from near-threshold water; none comes from water
brighter than -10 dB, which the ceiling excludes by construction.

Validation needs a pre-event image for every chip, which Sen1Floods11 does
not include. scripts/fetch_pre_event.py fetches one per chip from Earth
Engine; notebook 06 scores the rule once they exist. Until then the backend
carries no accuracy figure for this method.
"""

import numpy as np

# Keep these equal to backend/detection/sar.py. test_change_detection.py holds
# the two together, because two implementations of one rule with different
# constants means the notebook measures something that does not ship.
DARK_DB = -20.0       # the shipped darkness rule, unchanged
DROP_DB = -5.0        # at least this much darker than the dry baseline
CEILING_DB = -16.0    # ...and still dark enough to plausibly be water
# First guess was -3 / -15; notebook 06 measured it at IoU 0.466, below the
# darkness rule's 0.485. -5 / -16 scored 0.505.

NODATA_FLOOR_DB = -50.0


def fused(vv, vh):
    """mean(VV, VH) in dB, NaN where either band is missing."""
    vv = np.asarray(vv, dtype=np.float64)
    vh = np.asarray(vh, dtype=np.float64)
    out = (vv + vh) / 2.0
    out[~(np.isfinite(vv) & np.isfinite(vh))] = np.nan
    return out


def change_rule(dark_db=DARK_DB, drop_db=DROP_DB, ceiling_db=CEILING_DB):
    """The rule as a function of (post, pre) fused dB arrays -> (pred, info).

    A pixel with no pre-event data falls back to the darkness rule alone
    rather than being dropped: missing history is not evidence against water,
    and dropping it would score the change rule on fewer pixels than the rule
    it is compared with.
    """
    def run(post, pre):
        post = np.asarray(post, dtype=np.float64)
        pre = np.asarray(pre, dtype=np.float64)
        has_post = np.isfinite(post)
        has_pre = np.isfinite(pre)

        with np.errstate(invalid="ignore"):
            dark = has_post & (post < dark_db)
            darkened = has_post & has_pre & ((post - pre) < drop_db) & (post < ceiling_db)

        return dark | darkened, {
            "dark_db": dark_db, "drop_db": drop_db, "ceiling_db": ceiling_db,
            "pixels_from_darkness": int(dark.sum()),
            "pixels_added_by_change": int((darkened & ~dark).sum()),
            "pixels_without_history": int((has_post & ~has_pre).sum()),
        }
    run.__name__ = f"change_{dark_db}_{drop_db}_{ceiling_db}"
    return run


def darkness_rule(dark_db=DARK_DB):
    """The shipped rule, in the same (post, pre) signature for a fair table."""
    def run(post, pre):
        post = np.asarray(post, dtype=np.float64)
        with np.errstate(invalid="ignore"):
            return np.isfinite(post) & (post < dark_db), {"dark_db": dark_db}
    run.__name__ = f"dark_{dark_db}"
    return run


def recovery(post, truth, shipped_pred, change_pred, bright_db=-10.0, marginal_db=3.0):
    """Of the water the shipped rule missed, how much does change detection find?

    Binned the same way as notebook 04's diagnostic, so the answer lines up
    with the question it was built to address:

        marginal  within `marginal_db` of the darkness threshold
        moderate  darker than `bright_db` but well above the threshold
        bright    above `bright_db` - too bright to be open water at all

    Returns {bin: {"missed": n, "recovered": n}}. Recovery in the bright bin
    is the headline: that is the water no threshold could reach.
    """
    post = np.asarray(post, dtype=np.float64)
    truth = np.asarray(truth)
    water = truth == 1
    missed = water & ~np.asarray(shipped_pred, dtype=bool) & np.isfinite(post)
    found = missed & np.asarray(change_pred, dtype=bool)

    with np.errstate(invalid="ignore"):
        bins = {
            "marginal": missed & (post < DARK_DB + marginal_db),
            "moderate": missed & (post >= DARK_DB + marginal_db) & (post <= bright_db),
            "bright": missed & (post > bright_db),
        }
    return {
        name: {"missed": int(mask.sum()), "recovered": int((mask & found).sum())}
        for name, mask in bins.items()
    }


def added_false_positives(truth, shipped_pred, change_pred):
    """Pixels the change branch adds that are labelled dry. The price."""
    truth = np.asarray(truth)
    added = np.asarray(change_pred, dtype=bool) & ~np.asarray(shipped_pred, dtype=bool)
    return int((added & (truth == 0)).sum())
