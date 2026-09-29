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
# reaches the rest; change detection against a dry baseline would.
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

# Retained for the Otsu path only. These bounds were measured for VV and then
# shifted by the -3 dB the optimum moved when we switched to the fused band.
# That shift is an extrapolation, NOT a measurement - re-sweep before trusting
# the Otsu path on fused data.
WATER_DB_MIN = -31.0
WATER_DB_MAX = -17.0
FALLBACK_DB = DEFAULT_DB


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
                f"{VALIDATION['iou']} for the fused rule"
            )

    if scene_count == 0:
        raise NoSarImagery(start_date, end_date, orbit_pass)

    # Minimum backscatter over the window: water is dark, so the minimum
    # captures water present at ANY point. A mean would dilute a short flood.
    # Taken per band, then speckle filtered per band, and only then fused -
    # filtering before averaging gives the focal median two independent looks
    # at the same ground instead of one pre-averaged one.
    stack = speckle_filter(collection.min().clip(region), speckle_radius)
    composite = fuse(stack, polarisations)

    if threshold_db is None and method == "fixed":
        # Default. Measured best on Sen1Floods11; see the note at the top.
        # Both branches are validated numbers, just different ones - the VV-only
        # optimum is 3 dB higher than the fused one, so the fallback has to move
        # the threshold too, not merely drop a band.
        threshold = VV_ONLY_DB if degraded_to else DEFAULT_DB
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
            dict(VALIDATION if fused else VV_ONLY_VALIDATION)
            if threshold_source == "fixed_validated" else None
        ),
        "composite": "per-pixel minimum backscatter over window",
        "speckle_filter": f"focal median {speckle_radius} m",
        "orbit_pass": orbit_pass,
        "relative_orbit": relative_orbit,
    }
    return water_mask, composite, info


KNOWN_CONFUSIONS = [
    "Radar shadow behind terrain and tall buildings is dark and can read as water.",
    "Smooth dry surfaces such as tarmac, sand flats and bare hardpan can read as water.",
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
