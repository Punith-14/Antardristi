"""
Numpy mirrors of the thresholding in backend/sar.py.

Earth Engine cannot run numpy, so the production path and the evaluation path
are necessarily two implementations of one method. They must stay in step, or
the evaluation measures something other than what ships. `compare_with_ee()`
exists to check that they agree.

Water is dark in SAR: smooth surfaces reflect the pulse away from the sensor.
So the water class is everything BELOW the threshold.
"""

import numpy as np

# Same bounds as backend/sar.py. Keep in sync.
WATER_DB_MIN = -25.0
WATER_DB_MAX = -12.0
FALLBACK_DB = -16.0

# Conventional literature value for open water in Sentinel-1 VV.
CONVENTIONAL_DB = -16.0


def otsu(values, bins=255, value_range=(-45.0, 5.0)):
    """Threshold maximising between-class variance.

    Mirrors the Earth Engine reduceRegion(Reducer.histogram()) version.
    Returns None when the data cannot support a split.
    """
    values = np.asarray(values, dtype=np.float64).ravel()
    values = values[np.isfinite(values)]
    if values.size < 2:
        return None

    counts, edges = np.histogram(values, bins=bins, range=value_range)
    centres = (edges[:-1] + edges[1:]) / 2.0

    total = counts.sum()
    if total == 0:
        return None

    weight_bg = np.cumsum(counts)
    weight_fg = total - weight_bg

    cumulative_sum = np.cumsum(counts * centres)
    grand_sum = cumulative_sum[-1]

    with np.errstate(divide="ignore", invalid="ignore"):
        mean_bg = cumulative_sum / weight_bg
        mean_fg = (grand_sum - cumulative_sum) / weight_fg
        between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2

    between[~np.isfinite(between)] = -np.inf
    if not np.any(np.isfinite(between)):
        return None

    return float(centres[int(np.argmax(between))])


def clamped_otsu(values, lo=WATER_DB_MIN, hi=WATER_DB_MAX):
    """Otsu, bounded to the physically plausible range for open water.

    Returns (threshold, source) where source is one of
    'otsu' | 'otsu_clamped' | 'fallback_otsu_failed'.
    """
    raw = otsu(values)
    if raw is None:
        return FALLBACK_DB, "fallback_otsu_failed", None

    threshold = max(lo, min(hi, raw))
    source = "otsu" if abs(threshold - raw) < 0.01 else "otsu_clamped"
    return threshold, source, raw


def apply_threshold(image, threshold):
    """Water mask: True where backscatter is below the threshold."""
    image = np.asarray(image, dtype=np.float64)
    return np.where(np.isfinite(image), image < threshold, False)


# ---------------------------------------------------------------- methods

def method_fixed(db):
    def run(image):
        return apply_threshold(image, db), {"threshold": db, "source": "fixed"}
    run.__name__ = f"fixed_{db}dB"
    return run


def method_otsu(image):
    raw = otsu(image)
    threshold = raw if raw is not None else FALLBACK_DB
    return apply_threshold(image, threshold), {
        "threshold": threshold,
        "source": "otsu" if raw is not None else "fallback",
        "raw": raw,
    }


def method_clamped_otsu(image):
    """What backend/sar.py currently does."""
    threshold, source, raw = clamped_otsu(image)
    return apply_threshold(image, threshold), {
        "threshold": threshold,
        "source": source,
        "raw": raw,
    }


def method_otsu_wide(image):
    """Otsu clamped to a wider band, to test whether our upper bound of
    -12 dB is too permissive. Kerala clamped to exactly -12, which suggests
    the bound was doing the deciding rather than the data."""
    threshold, source, raw = clamped_otsu(image, lo=-28.0, hi=-14.0)
    return apply_threshold(image, threshold), {
        "threshold": threshold,
        "source": source,
        "raw": raw,
    }


METHODS = {
    "fixed -16 dB (literature)": method_fixed(CONVENTIONAL_DB),
    "fixed -12 dB": method_fixed(-12.0),
    "fixed -18 dB": method_fixed(-18.0),
    "otsu (unbounded)": method_otsu,
    "otsu clamped [-25,-12]": method_clamped_otsu,
    "otsu clamped [-28,-14]": method_otsu_wide,
}


# ------------------------------------------------- dual polarisation (VV+VH)
#
# Why bother: VV alone scores recall 0.511 on Sen1Floods11. Half of the water
# that is really there is missed, and a threshold sweep cannot fix it - lowering
# the threshold trades precision away faster than it buys recall (see the sweep
# in evaluation/sen1floods11_results.json).
#
# The physics that might explain the gap:
#
#   open water        smooth, specular. Pulse reflects AWAY from the sensor.
#                     VV very low, VH very low. This is what VV thresholding
#                     already finds.
#
#   flooded vegetation  double bounce. The pulse hits the water, bounces off
#                     trunks and stems, and returns to the sensor. Double bounce
#                     PRESERVES polarisation, so it lifts VV (co-pol) much more
#                     than VH (cross-pol). Result: BRIGHT, with a large VV-VH
#                     gap. A "darker than -17 dB" rule cannot see this in any
#                     polarisation, which is the point.
#
#   dry vegetation    volume scattering inside the canopy, which DEPOLARISES.
#                     VH comes up relative to VV, so the VV-VH gap is small.
#
# So VV-VH separates flooded vegetation from dry vegetation in principle, and
# that is the hypothesis these rules test.
#
# Two honest caveats, both of which the notebook reports on:
#
#   1. Built-up areas are also strong double-bounce scatterers. Without a land
#      cover mask, "bright and strongly co-polarised" catches towns as well as
#      flooded forest. Expect the double-bounce rule to cost precision.
#   2. Sen1Floods11 labels were drawn from Sentinel-2 NDVI/MNDWI and hand
#      corrected using optical, DEM, land cover and slope. None of those reveal
#      water under a canopy. If flooded vegetation is not in the labels, then
#      detecting it correctly is scored as a FALSE POSITIVE, and no fusion rule
#      can win. Measure before believing either way.

# Starting points for the sweep, not validated values. VH sits roughly 6-8 dB
# below VV over the same surface because cross-pol return is weaker, so the
# water threshold has to move down with it. The notebook replaces these with
# whatever the sweep actually finds.
VH_WATER_DB = -22.0
VV_WATER_DB = -17.0
# A VV-VH gap this wide means the return is strongly co-polarised, which points
# at double bounce rather than volume scattering.
DOUBLE_BOUNCE_GAP_DB = 9.0


def _finite(*arrays):
    """Pixels where every band has real data. Non-finite is never water."""
    valid = np.ones(np.asarray(arrays[0]).shape, dtype=bool)
    for array in arrays:
        valid &= np.isfinite(np.asarray(array, dtype=np.float64))
    return valid


# Every rule below requires BOTH bands, even the ones that only read one of
# them. That is not defensive padding - it is what makes the comparison table
# honest. If "VH only" were allowed to score pixels where VV is missing, it
# would be graded over a different set of pixels than "VV only", and the two
# rows could not be compared. Same denominator, or no table.


def dual_fixed_vv(db=VV_WATER_DB):
    """The method that ships today, expressed as a dual-band rule so it sits in
    the same table as the others."""
    def run(vv, vh):
        return apply_threshold(vv, db) & _finite(vv, vh), {
            "threshold": db, "source": "vv_only"}
    run.__name__ = f"vv_{db}dB"
    return run


def dual_fixed_vh(db=VH_WATER_DB):
    def run(vv, vh):
        return apply_threshold(vh, db) & _finite(vv, vh), {
            "threshold": db, "source": "vh_only"}
    run.__name__ = f"vh_{db}dB"
    return run


def dual_union(vv_db=VV_WATER_DB, vh_db=VH_WATER_DB):
    """Water if EITHER polarisation says so. Buys recall, spends precision."""
    def run(vv, vh):
        mask = apply_threshold(vv, vv_db) | apply_threshold(vh, vh_db)
        return mask & _finite(vv, vh), {
            "threshold": (vv_db, vh_db), "source": "union"}
    run.__name__ = f"union_{vv_db}_{vh_db}"
    return run


def dual_intersection(vv_db=VV_WATER_DB, vh_db=VH_WATER_DB):
    """Water only if BOTH agree. Buys precision, spends recall - the wrong
    direction for our problem, included so the trade-off is visible."""
    def run(vv, vh):
        mask = apply_threshold(vv, vv_db) & apply_threshold(vh, vh_db)
        return mask & _finite(vv, vh), {
            "threshold": (vv_db, vh_db), "source": "intersection"}
    run.__name__ = f"intersection_{vv_db}_{vh_db}"
    return run


def dual_mean(db=-19.5):
    """Threshold the average of the two bands. Cheap way to use both without
    committing to a fusion rule."""
    def run(vv, vh):
        vv = np.asarray(vv, dtype=np.float64)
        vh = np.asarray(vh, dtype=np.float64)
        with np.errstate(invalid="ignore"):
            combined = (vv + vh) / 2.0
        return apply_threshold(combined, db) & _finite(vv, vh), {
            "threshold": db, "source": "mean"}
    run.__name__ = f"mean_{db}dB"
    return run


def dual_open_water_or_double_bounce(vv_db=VV_WATER_DB,
                                     gap_db=DOUBLE_BOUNCE_GAP_DB):
    """Open water OR flooded vegetation.

    Open water is dark in VV. Flooded vegetation is bright in VV with a wide
    VV-VH gap, because double bounce preserves polarisation while the volume
    scattering of a dry canopy depolarises.

    This is the rule that could raise recall if the missing water really is
    under canopy. It is also the rule most likely to pick up towns.
    """
    def run(vv, vh):
        vv = np.asarray(vv, dtype=np.float64)
        vh = np.asarray(vh, dtype=np.float64)
        valid = _finite(vv, vh)

        open_water = np.where(valid, vv < vv_db, False)
        with np.errstate(invalid="ignore"):
            gap = vv - vh
        # Bright AND strongly co-polarised. The vv >= vv_db clause keeps this
        # arm from re-claiming pixels the open-water arm already has, so the two
        # contributions stay separable in the diagnostics.
        double_bounce = np.where(valid, (vv >= vv_db) & (gap > gap_db), False)

        return open_water | double_bounce, {
            "threshold": (vv_db, gap_db),
            "source": "open_water_or_double_bounce",
        }
    run.__name__ = f"water_or_doublebounce_{vv_db}_{gap_db}"
    return run


# What backend/sar.py now ships, mirrored here. These two files are two
# implementations of one method and must stay in step, or the evaluation stops
# measuring what users actually get.
SHIPPED_DB = -20.0
SHIPPED_METHOD = "mean(VV,VH)"


def shipped():
    """The rule in production, for re-scoring it whenever the harness is run."""
    return dual_mean(SHIPPED_DB)


DUAL_METHODS = {
    "VV only -17 dB (previous)": dual_fixed_vv(),
    "VH only -22 dB": dual_fixed_vh(),
    "VV or VH (union)": dual_union(),
    "VV and VH (intersection)": dual_intersection(),
    "mean(VV,VH) -20 dB (ships today)": shipped(),
    "open water or double bounce": dual_open_water_or_double_bounce(),
}
