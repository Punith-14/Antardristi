"""
Sentinel-1 SAR flood detection.

Why this exists: our own measurement on Kerala, August 2019, showed optical
Sentinel-2 could observe only 6,820 km2 of a 37,575 km2 state during a major
flood, and reported LESS water than the dry-season baseline. The visible pixels
during a monsoon flood are the cloud-free ones, which are the un-rained-on ones,
which are the un-flooded ones. Optical flood mapping in India is structurally
biased, not merely noisy.

Radar sees through cloud and works at night. Open water is smooth, so it
reflects the radar pulse away from the sensor and appears very dark. That is the
entire physical basis of the method.

We use BOTH polarisations and threshold their mean. See the measurement table
below: that choice is strictly better than the VV-only rule it replaced, on
every metric, which is rare enough to be worth stating plainly.
"""

import ee

S1_COLLECTION = "COPERNICUS/S1_GRD"

# Sentinel-1 IW transmits vertically and listens on both channels at once, so
# both of these arrive in the same scene at no extra acquisition cost.
POLARISATIONS = ("VV", "VH")
FUSED_BAND = "VV_VH_mean"

# Thresholds, chosen from measurement rather than intuition.
#
# Evaluated on Sen1Floods11, 441 of 446 hand-labelled chips - the other five
# carry no labelled pixels - over 100,983,314 valid pixels
# (notebooks/04_polarisation.ipynb, evaluation/polarisation_results.json):
#
#                               IoU    precision   recall
#     mean(VV,VH) -20 dB       0.489     0.768      0.574   <- ships
#     VH only     -23 dB       0.488     0.659      0.652
#     VV or VH                 0.487     0.623      0.690
#     VV only     -17 dB       0.441     0.764      0.511   <- previous
#     VV and VH                0.439     0.859      0.473
#     otsu unbounded (VV)      0.186     0.199      0.732
#
# The mean rule dominates the old VV-only rule on every metric at once: recall
# rises from 0.511 to 0.574 and precision does NOT pay for it, edging up from
# 0.764 to 0.768. There is no trade-off to defend, which is why this one ships
# rather than VH-only, whose higher recall costs 11 points of precision.
#
# Otsu still performs WORSE than a constant. It selects about -11.3 dB on
# average, because flood chips rarely show the clean bimodal histogram Otsu
# assumes. Our previous clamp only helped by dragging Otsu back toward a sane
# value, and the bound decided 67% of the time - a constant in an adaptive
# costume. So: fixed threshold by default, Otsu for experiments only.
DEFAULT_DB = -20.0

# What the remaining error is made of, measured the same way. Of the water this
# method still misses, 28.3% is BRIGHTER than -10 dB - the missed pixels sit at
# a median of -12.25 dB against -21.25 dB for the pixels we catch. That is not a
# mistuned threshold, it is a different physical population: mixed pixels,
# turbid water, wind-roughened surfaces. Only 7.7% carries the double-bounce
# signature of flooded vegetation. No darkness threshold in any polarisation
# reaches the rest. Change detection was expected to, and notebook 06 showed it
# does not: its gain is near-threshold water, and it recovers none of the water
# above -10 dB (see CHANGE_* below). Those percentages are in VV terms; on the
# fused band only about 2% of missed water is that bright.
RECALL_CEILING_NOTE = (
    "Recall is bounded by what single-date backscatter thresholding can do. "
    "28.3% of undetected water is brighter than -10 dB, too bright for any "
    "defensible water threshold."
)

# Measured performance, in one place. main.py's /analyses catalogue reads this
# rather than restating it - a second copy of these numbers went stale once
# already, and a stale number here becomes a false claim in a user-facing
# report.
VALIDATION = {
    "dataset": "Sen1Floods11 HandLabeled (441 chips, VV+VH mean)",
    "iou": 0.489,
    "precision": 0.768,
    "recall": 0.574,
}

# The previous rule, kept because it is still the right answer when a scene
# carries only one polarisation. Dual-pol IW is the norm over India but not a
# guarantee, and raising "no imagery" over imagery that exists would be a worse
# failure than falling back to a weaker rule and saying so.
VV_ONLY_DB = -17.0
VV_ONLY_VALIDATION = {
    "dataset": "Sen1Floods11 HandLabeled (446 chips, VV only)",
    "iou": 0.441,
    "precision": 0.763,
    "recall": 0.511,
}

# The threshold depends on the analysis scale. Measured, not assumed.
#
# Everything above was measured on 10 m chips. The service never thresholds
# 10 m pixels: it reduces at 100-200 m, where Earth Engine serves the MEAN of
# each 10 x 10 or 20 x 20 block. Averaging pulls dark water pixels towards
# their brighter neighbours, so the right threshold moves up. Notebook 07
# measured it on the official Sen1Floods11 split - threshold chosen on the 251
# train chips, scored on chips the choice never saw, labels aggregated to the
# coarse pixel by majority (evaluation/spatial_results.json):
#
#     200 m     -20 dB   -> -18.5 dB     IoU on test 0.524 -> 0.609
#                                        valid 0.544 -> 0.594, Bolivia 0.579 -> 0.718
#     100 m     -20 dB   -> -19.0 dB     IoU on test 0.529 -> 0.570
#                                        valid 0.538 -> 0.564, Bolivia 0.596 -> 0.682
#
# The largest measured gain in the project, from correcting a mismatch rather
# than adding a method. The coarse-scale IoUs are scored against coarse labels,
# an easier target, so compare -18.5 with -20 at 200 m - not 0.609 with 0.489.
SCALE_RULES = {
    10: {"db": DEFAULT_DB, "validation": VALIDATION},
    100: {
        "db": -19.0,
        "validation": {
            "dataset": "Sen1Floods11 HandLabeled test split (88 chips) at 100 m, "
                       "threshold chosen on the train split",
            "iou": 0.570,
            "precision": 0.808,
            "recall": 0.659,
        },
    },
    200: {
        "db": -18.5,
        "validation": {
            "dataset": "Sen1Floods11 HandLabeled test split (88 chips) at 200 m, "
                       "threshold chosen on the train split",
            "iou": 0.609,
            "precision": 0.807,
            "recall": 0.713,
            # Notebook 08 (evaluation/events_results.json): the Indian chips the
            # threshold was never tuned on. They lie at 25.7-27.3 N, 92.4-93.9 E
            # - the Brahmaputra valley in Assam, August 2016. One event in one
            # river valley: the 95{'chips': 28, 'iou': 0.725, 'precision': 0.902, 'recall': 0.787, 'lo': 0.564, 'hi': 0.829}ange is the honest answer, not the point.
            "india": {
                "event": "Assam, August 2016",
                "chips": 28,
                "iou": 0.725,
                "precision": 0.902,
                "recall": 0.787,
                "ci95": [0.564, 0.829],
            },
        },
    },
}

# Part of every flood cache key (main.FloodRequest.cache_key). Results computed
# under an earlier rule must be recomputed, not served: the same request would
# otherwise return the old -20 dB extent with no sign the rule had changed.
RULE_VERSION = "fused_vvvh_scale_thresholds_v2"


def rule_for_scale(scale):
    """The measured threshold and validation for an analysis scale in metres.

    Exact for 10, 100 and 200 m. Anything else uses the nearest measured scale
    on a log axis (150 m -> 200 m, 30 m -> 10 m) and says so: `exact` is False
    and `measured_at_m` names the scale the numbers actually describe.
    """
    import math

    try:
        scale = float(scale)
    except (TypeError, ValueError):
        scale = 100.0
    scale = max(scale, 1.0)
    nearest = min(SCALE_RULES, key=lambda m: abs(math.log(scale / m)))
    rule = SCALE_RULES[nearest]
    return {
        "db": rule["db"],
        "validation": dict(rule["validation"]),
        "measured_at_m": nearest,
        "exact": abs(scale - nearest) < 1e-9,
    }


# Retained for the Otsu path only. These bounds were measured for VV and then
# shifted by the -3 dB the optimum moved when we switched to the fused band.
# That shift is an extrapolation, NOT a measurement - re-sweep before trusting
# the Otsu path on fused data.
WATER_DB_MIN = -31.0
WATER_DB_MAX = -17.0
FALLBACK_DB = DEFAULT_DB


# Change detection: darker than its own dry self, not just dark.
#
# The RECALL_CEILING_NOTE above names the gap and the mechanism that would
# reach it. This is that mechanism. The rule, on the same fused band:
#
#     water = post < CHANGE_DARK_DB
#             OR (post - pre < CHANGE_DROP_DB AND post < CHANGE_CEILING_DB)
#
# where `pre` is the MEDIAN of a dry pre-event window from the same relative
# orbit - the median, not the minimum used for the post window, because the
# baseline has to describe the pixel's typical dry state. A minimum would
# latch onto any transient wetness in the baseline and hide the change.
#
# Measured by notebook 06 on 428 Sen1Floods11 chips, against a baseline fetched
# for each by scripts/fetch_pre_event.py (60 days ending 15 days before the
# flood image, same relative orbit). On those chips:
#
#                                    IoU    precision   recall
#     darkness only, -20 dB         0.485     0.752      0.577   <- default
#     change, drop -3 / ceiling -15 0.466     0.601      0.674   <- first guess
#     change, drop -5 / ceiling -16 0.505     0.728      0.622   <- ships (opt-in)
#
# The first guess FAILED: it bought recall with more precision than it was
# worth, calling 2.5M dry pixels water to recover 0.94M wet ones. Requiring a
# 5 dB drop fixed that. The gain is small but not a spike - every drop of 5 or
# 6 dB scored 0.501-0.505 whatever the ceiling.
#
# What it recovers, at these settings, is mostly near-threshold water: 23.7% of
# the marginal misses, 2.0% of the moderate ones - 439k wet pixels found for
# 410k dry ones wrongly added, close to one for one, which is why the IoU gain
# is real but thin. (The 40% / 12% recovered by the -3 dB first guess came
# with 2.5M false positives.) It recovers NONE of the water brighter than
# -10 dB: the ceiling excludes it by construction. That population, 28.3% of misses in
# notebook 04's VV terms, is only about 2% of misses on the fused band this
# rule uses - most of the missed water is moderately dark, not bright, and
# still out of reach of single-date radar.
#
# Opt-in, not the default: +0.02 IoU does not justify requiring a baseline
# window on every request. evaluation/change.py mirrors these constants and a
# test holds the two equal.
CHANGE_DARK_DB = DEFAULT_DB
CHANGE_DROP_DB = -5.0
CHANGE_CEILING_DB = -16.0
CHANGE_VALIDATION = {
    "dataset": "Sen1Floods11 HandLabeled (428 chips with a fetched pre-event baseline, at 10 m)",
    "iou": 0.505,
    "precision": 0.728,
    "recall": 0.622,
}

# Why 0.505 needs reading carefully. One sentence, because every note has to
# survive into the generated report.
CHANGE_VALIDATION_CAVEAT = (
    "Change detection was tuned and scored on the same chips, its baselines were "
    "fetched rather than supplied with the dataset, and a baseline window that "
    "was already wet makes it find less new water than was there."
)

CHANGE_NOT_VALIDATED_NOTE = (
    "Change detection (darker by more than "
    f"{abs(CHANGE_DROP_DB):g} dB than the dry baseline, and below "
    f"{CHANGE_CEILING_DB:g} dB) has not been scored at these settings, so no "
    "accuracy figure is given."
)


class NoBaselineImagery(RuntimeError):
    """Change detection was asked for, and the pre-event window has no radar.

    Separate from NoSarImagery because the right response differs: with no
    post-event imagery there is nothing to report, but with no baseline the
    darkness rule still works - the caller should be told to use it, not
    silently handed it.
    """

    def __init__(self, start_date, end_date, relative_orbit=None):
        self.start_date = start_date
        self.end_date = end_date
        orbit = f" from relative orbit {relative_orbit}" if relative_orbit else ""
        super().__init__(
            f"Change detection needs a pre-event baseline, and there are no "
            f"Sentinel-1 scenes{orbit} for {start_date} to {end_date}. Widen the "
            "baseline window, or use method 'threshold'."
        )


def acquisition_days(collection):
    """Distinct UTC days the scenes in a collection were acquired. One round trip."""
    from pipeline.latest import days_from_ms

    return days_from_ms(collection.aggregate_array("system:time_start").getInfo() or [])


def latest_acquisition(region, today=None, lookback_days=None):
    """The newest Sentinel-1 pass over a region, and the passes before it.

    Dual-polarisation scenes first, VV alone if there are none - the same
    order detect_water uses, so the pass found is one the analysis can use.
    Raises NoSarImagery when nothing passed in the lookback window.
    """
    from datetime import date as _date, timedelta
    from pipeline import latest as latest_mode

    today = today or _date.today()
    lookback = lookback_days or latest_mode.LOOKBACK_DAYS
    start = (today - timedelta(days=lookback)).isoformat()
    end = (today + timedelta(days=1)).isoformat()

    for polarisations in (POLARISATIONS, ("VV",)):
        collection = get_collection(region, start, end, polarisations)
        times = collection.aggregate_array("system:time_start").getInfo() or []
        if times:
            orbits = collection.aggregate_array("relativeOrbitNumber_start").getInfo() or []
            newest = max(range(len(times)), key=lambda i: times[i])
            days = latest_mode.days_from_ms(times)
            return {
                "date": days[-1],
                "relative_orbit": orbits[newest] if newest < len(orbits) else None,
                "recent_passes": days,
                "polarisations": "+".join(polarisations),
                "lookback_days": lookback,
            }
    raise NoSarImagery(start, end)


def baseline_composite(region, start_date, end_date, relative_orbit,
                       polarisations=POLARISATIONS, speckle_radius=50):
    """The dry reference for change detection: fused median backscatter.

    Same relative orbit as the post-event window, or the difference contains
    viewing geometry. Same speckle filter and fusion as detect_water, so the
    two composites differ only in time.
    """
    polarisations = tuple(polarisations)
    collection = get_collection(
        region, start_date, end_date, polarisations, relative_orbit=relative_orbit
    )
    if collection.size().getInfo() == 0:
        raise NoBaselineImagery(start_date, end_date, relative_orbit)

    stack = speckle_filter(collection.median().clip(region), speckle_radius)
    return fuse(stack, polarisations)


def change_mask(post_composite, pre_composite, dark_db=CHANGE_DARK_DB,
                drop_db=CHANGE_DROP_DB, ceiling_db=CHANGE_CEILING_DB):
    """Water by the change rule. Both inputs are fused dB composites.

    Where the baseline has no data the darkened branch is simply absent, so
    the pixel falls back to the darkness rule: missing history is not
    evidence against water.
    """
    dark = post_composite.lt(dark_db)
    darkened = (
        post_composite.subtract(pre_composite).lt(drop_db)
        .And(post_composite.lt(ceiling_db))
    )
    return dark.Or(darkened.unmask(0)).rename("water_mask")


class NoSarImagery(RuntimeError):
    def __init__(self, start_date, end_date, orbit_pass=None):
        self.start_date = start_date
        self.end_date = end_date
        self.orbit_pass = orbit_pass
        detail = f" ({orbit_pass} pass)" if orbit_pass else ""
        super().__init__(
            f"No Sentinel-1 GRD scenes{detail} for {start_date} to {end_date}."
        )


def get_collection(region, start_date, end_date, polarisations=POLARISATIONS,
                   orbit_pass=None, relative_orbit=None):
    """Sentinel-1 IW GRD scenes over a region, carrying every polarisation asked for.

    Requiring both VV and VH narrows the collection slightly: a scene has to
    have been acquired in dual-pol mode. Over India that is the norm, but the
    filter is real and a caller that gets no scenes should check this before
    assuming there was no pass.

    orbit_pass and relative_orbit matter for change detection: backscatter
    depends on viewing geometry, so comparing an ASCENDING pre-image against a
    DESCENDING post-image produces differences that are geometry, not water.
    """
    if isinstance(polarisations, str):
        polarisations = (polarisations,)
    polarisations = tuple(polarisations)

    collection = (
        ee.ImageCollection(S1_COLLECTION)
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
    )

    for polarisation in polarisations:
        collection = collection.filter(
            ee.Filter.listContains("transmitterReceiverPolarisation", polarisation)
        )

    if orbit_pass:
        collection = collection.filter(ee.Filter.eq("orbitProperties_pass", orbit_pass))
    if relative_orbit is not None:
        collection = collection.filter(
            ee.Filter.eq("relativeOrbitNumber_start", relative_orbit)
        )

    return collection.select(list(polarisations))


def fuse(composite, polarisations=POLARISATIONS):
    """Combine the polarisations into the single band the threshold is applied to.

    The mean of VV and VH, which measured best. Why it beats either band alone:
    VV separates smooth water from land well but is sensitive to surface
    roughness, so a breeze or sediment pushes water above the threshold. VH is
    weaker overall but degrades more gracefully. Averaging keeps VV's
    separation while letting VH pull back the pixels roughness would have lost.

    One polarisation in, and this is a rename - so the VV-only path still works
    for comparison runs.
    """
    polarisations = tuple(polarisations)
    if len(polarisations) == 1:
        return composite.select(polarisations[0]).rename(FUSED_BAND)
    return (
        composite.select(list(polarisations))
        .reduce(ee.Reducer.mean())
        .rename(FUSED_BAND)
    )


def dominant_orbit(collection):
    """Most frequently occurring relative orbit, for consistent comparison."""
    orbits = collection.aggregate_array("relativeOrbitNumber_start")
    counts = ee.Dictionary(orbits.reduce(ee.Reducer.frequencyHistogram()))
    keys = counts.keys()
    values = keys.map(lambda k: counts.get(k))
    sorted_keys = keys.sort(values)
    return ee.Number.parse(sorted_keys.get(-1))


def speckle_filter(image, radius=50):
    """Suppress SAR speckle with a focal median.

    A refined Lee filter preserves edges better, but focal median is far
    simpler, is standard in Earth Engine flood workflows, and the difference is
    small at the scale of flood extent mapping.
    """
    return image.focal_median(radius, "circle", "meters").rename(image.bandNames())


def otsu_threshold(image, region, scale=100, band="VV"):
    """Otsu's method: the threshold maximising between-class variance.

    Adaptive rather than fixed because backscatter varies with terrain,
    incidence angle and surface roughness. A threshold tuned for Kerala will be
    wrong for Assam.
    """
    histogram = image.select(band).reduceRegion(
        reducer=ee.Reducer.histogram(255, 0.1),
        geometry=region,
        scale=scale,
        maxPixels=1_000_000_000,
        bestEffort=True,
    ).get(band)

    histogram = ee.Dictionary(histogram)
    counts = ee.Array(histogram.get("histogram"))
    means = ee.Array(histogram.get("bucketMeans"))
    size = means.length().get([0])
    total = counts.reduce(ee.Reducer.sum(), [0]).get([0])
    total_sum = means.multiply(counts).reduce(ee.Reducer.sum(), [0]).get([0])
    grand_mean = total_sum.divide(total)

    def between_class_variance(i):
        a_counts = counts.slice(0, 0, i)
        a_count = a_counts.reduce(ee.Reducer.sum(), [0]).get([0])
        a_means = means.slice(0, 0, i)
        a_mean = (
            a_means.multiply(a_counts)
            .reduce(ee.Reducer.sum(), [0])
            .get([0])
            .divide(a_count)
        )
        b_count = total.subtract(a_count)
        b_mean = total_sum.subtract(a_count.multiply(a_mean)).divide(b_count)
        return a_count.multiply(a_mean.subtract(grand_mean).pow(2)).add(
            b_count.multiply(b_mean.subtract(grand_mean).pow(2))
        )

    # Must produce exactly as many variances as there are bucket means, or
    # Array.sort rejects the pair. sequence(1, size) gives `size` elements.
    indices = ee.List.sequence(1, size)
    variances = indices.map(between_class_variance)
    return means.sort(ee.Array(variances)).get([-1])


def detect_water(region, start_date, end_date, polarisations=POLARISATIONS,
                 orbit_pass=None, relative_orbit=None, scale=100,
                 threshold_db=None, speckle_radius=50, method="fixed"):
    """Water mask from Sentinel-1.

    Returns (water_mask, composite, info_dict). The mask is an ee.Image of 1/0;
    info carries the values the evidence record needs.
    """
    if isinstance(polarisations, str):
        polarisations = (polarisations,)
    polarisations = tuple(polarisations)

    collection = get_collection(
        region, start_date, end_date, polarisations, orbit_pass, relative_orbit
    )
    scene_count = collection.size().getInfo()

    # Dual-pol asks the scene to carry BOTH bands. Where only single-pol VV was
    # acquired, that filter empties the collection over imagery that exists.
    # Fall back to the VV-only rule rather than reporting no coverage - and
    # carry the reason out in info, because the result is now the weaker method.
    degraded_to = None
    if scene_count == 0 and len(polarisations) > 1:
        fallback = get_collection(
            region, start_date, end_date, ("VV",), orbit_pass, relative_orbit
        )
        fallback_count = fallback.size().getInfo()
        if fallback_count > 0:
            collection = fallback
            polarisations = ("VV",)
            scene_count = fallback_count
            degraded_to = (
                "no dual-polarisation scenes in this window; used VV alone at "
                f"{VV_ONLY_DB} dB, which scores IoU "
                f"{VV_ONLY_VALIDATION['iou']} against "
                f"{VALIDATION['iou']} for the fused rule, both measured at "
                "10 m - the VV-only threshold has not been re-measured at "
                "coarser analysis scales"
            )

    if scene_count == 0:
        raise NoSarImagery(start_date, end_date, orbit_pass)

    # When the images were taken, so every result can say how old it is.
    acquired = acquisition_days(collection)

    # Minimum backscatter over the window: water is dark, so the minimum
    # captures water present at ANY point. A mean would dilute a short flood.
    # Taken per band, then speckle filtered per band, and only then fused -
    # filtering before averaging gives the focal median two independent looks
    # at the same ground instead of one pre-averaged one.
    stack = speckle_filter(collection.min().clip(region), speckle_radius)
    composite = fuse(stack, polarisations)

    scale_rule = rule_for_scale(scale)

    if threshold_db is None and method == "fixed":
        # Default. Measured on Sen1Floods11 AT THE SCALE THIS RUNS AT - see
        # SCALE_RULES. The VV-only fallback keeps its 10 m threshold, the only
        # one measured for it, and says so in `degraded`.
        threshold = VV_ONLY_DB if degraded_to else scale_rule["db"]
        threshold_source = "fixed_validated"
        raw_threshold = None

    elif threshold_db is None:
        # One round trip: fetch the raw Otsu value, clamp in Python.
        raw = ee.Number(
            otsu_threshold(composite, region, scale, FUSED_BAND)
        ).getInfo()

        if raw is None:
            threshold, threshold_source = FALLBACK_DB, "fallback_otsu_failed"
        else:
            # Otsu lands absurdly on a uniform scene. Clamp to the physically
            # plausible range for open water rather than trusting it blindly.
            threshold = max(WATER_DB_MIN, min(WATER_DB_MAX, raw))
            threshold_source = (
                "otsu" if abs(threshold - raw) < 0.01 else "otsu_clamped"
            )
        raw_threshold = raw
    else:
        threshold = float(threshold_db)
        threshold_source = "fixed"
        raw_threshold = threshold

    water_mask = composite.lt(threshold).rename("water_mask")

    fused = len(polarisations) > 1

    info = {
        "sensor": "sentinel-1",
        "collection": S1_COLLECTION,
        # Read straight into the evidence record's method string, so this has to
        # describe what was actually measured: "VV+VH" not "VV".
        "polarisation": "+".join(polarisations),
        "fusion": "mean of VV and VH" if fused else None,
        # None on the normal path. When set, the result came from the weaker
        # rule and every number below refers to THAT rule.
        "degraded": degraded_to,
        "scene_count": scene_count,
        "threshold": round(threshold, 2),
        "threshold_unit": "dB",
        "threshold_source": threshold_source,
        "threshold_raw": round(raw_threshold, 2) if raw_threshold is not None else None,
        # Measured performance of the rule that actually ran, so it travels with
        # the result. An Otsu or caller-supplied threshold gets None rather than
        # borrowing a number it did not earn.
        "validation": (
            (scale_rule["validation"] if fused else dict(VV_ONLY_VALIDATION))
            if threshold_source == "fixed_validated" else None
        ),
        # The scale the threshold and its validation were measured at. Equal to
        # the analysis scale for 10, 100 and 200 m; otherwise the nearest one.
        "threshold_scale_m": scale_rule["measured_at_m"] if fused else 10,
        "threshold_scale_exact": scale_rule["exact"] if fused else scale == 10,
        "composite": "per-pixel minimum backscatter over window",
        "acquisition_days": acquired,
        "speckle_filter": f"focal median {speckle_radius} m",
        "orbit_pass": orbit_pass,
        "relative_orbit": relative_orbit,
    }
    return water_mask, composite, info


KNOWN_CONFUSIONS = [
    "Radar shadow behind terrain and tall buildings is dark and can read as water.",
    "Smooth dry surfaces such as tarmac, sand flats and bare hardpan can read as water. Measured: precision falls to 0.22 in arid Somalia and 0.32 in Pakistan, against 0.88 in Assam - treat flood maps over dry ground (Rajasthan, Kutch) with matching caution.",
    "Flooded vegetation may NOT be detected: the canopy bounces the pulse off the "
    "trunks and back to the sensor, so water beneath trees or tall crops reads as "
    "bright rather than dark. Measured at 7.7% of all undetected water.",
    "Wind roughens open water and raises backscatter, which can cause flooding to be "
    "underestimated during a storm.",
    "Recall is 0.574: roughly two fifths of labelled water is still missed. Most of "
    "it is brighter than any defensible water threshold - mixed pixels, turbid or "
    "shallow water - and is a limit of single-date thresholding rather than of this "
    "particular threshold.",
]
