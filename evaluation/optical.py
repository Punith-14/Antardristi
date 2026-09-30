"""
Numpy mirror of the optical flood rule in backend/detection/optical.py.

Same arrangement as thresholds.py for SAR: Earth Engine cannot run numpy, so
the rule that ships and the rule that is scored are two implementations of
one method, and a test holds them to the same threshold.

What makes this comparison like-for-like, and what does not.

Like-for-like:
  - The same 446 Sen1Floods11 hand-labelled chips the SAR rule was scored on.
  - The same pixels. `common_valid` scores both sensors only where the label
    is valid AND both images have data, so neither is scored on ground the
    other was excused from.
  - The same metric code (metrics.evaluate / aggregate).

Not like-for-like, and stated with every result:
  - **The labels lean optical.** Sen1Floods11 labels were drawn from
    Sentinel-2 NDVI/MNDWI and then hand corrected. Scoring an MNDWI rule
    against labels partly made from MNDWI is partly circular, so the optical
    score is biased upward relative to radar. A SAR win on these labels is
    therefore strong evidence; an optical win is weak evidence.
  - **Cloud is excused.** Labellers marked what they could not see as -1, and
    -1 pixels are not scored. Optical is never penalised here for cloud - the
    thing that decides between the sensors in a monsoon. Chip-level
    accuracy is not operational usefulness.
  - **Product level.** The S2Hand chips are Level-1C top-of-atmosphere
    reflectance; the backend uses Level-2A surface reflectance. MNDWI moves
    a little between the two, so the best threshold here may sit slightly
    off the best threshold in production.
"""

import numpy as np

# Must equal backend/detection/optical.py THRESHOLD. test_optical_eval.py holds
# them together; change one and the test fails until the other follows.
# Was -0.15, borrowed from surface water; notebook 05 measured it at precision
# 0.369 on floods and chose 0.15 instead.
SHIPPED_MNDWI = 0.15

# Band order of the Sen1Floods11 S2Hand GeoTIFFs: all 13 Sentinel-2 bands.
S2HAND_BANDS = ("B1", "B2", "B3", "B4", "B5", "B6", "B7",
                "B8", "B8A", "B9", "B10", "B11", "B12")
GREEN = S2HAND_BANDS.index("B3")
SWIR1 = S2HAND_BANDS.index("B11")

LABEL_NODATA = -1

LABEL_CAVEAT = (
    "Sen1Floods11 labels were drawn from Sentinel-2 NDVI/MNDWI and hand "
    "corrected, so an MNDWI rule is scored against labels partly made from "
    "MNDWI. The optical score is biased upward relative to radar; cloud-covered "
    "pixels are unlabelled and therefore never count against optical. The "
    "chips are Level-1C; the backend uses Level-2A."
)


def mndwi(green, swir1):
    """(green - swir1) / (green + swir1), NaN where undefined.

    NaN rather than 0 where both bands are zero: 0 is a real MNDWI value and
    would be scored as a confident non-water prediction on a pixel with no
    data at all.
    """
    green = np.asarray(green, dtype=np.float64)
    swir1 = np.asarray(swir1, dtype=np.float64)
    total = green + swir1
    with np.errstate(divide="ignore", invalid="ignore"):
        index = (green - swir1) / total
    index[~np.isfinite(index) | (total == 0)] = np.nan
    return index


def fixed_mndwi(threshold=SHIPPED_MNDWI):
    """The optical rule, in the (pred, info) shape the SAR rules use."""
    def run(green, swir1):
        index = mndwi(green, swir1)
        return np.isfinite(index) & (index > threshold), {
            "threshold": threshold, "source": "mndwi"}
    run.__name__ = f"mndwi_{threshold}"
    return run


def common_valid(truth, *images):
    """Pixels both sensors are scored on: label valid, every band finite."""
    valid = np.asarray(truth) != LABEL_NODATA
    for image in images:
        valid &= np.isfinite(np.asarray(image, dtype=np.float64))
    return valid


def restrict(truth, valid):
    """Labels with everything outside `valid` set to nodata, so metrics skip it."""
    out = np.asarray(truth).copy()
    out[~valid] = LABEL_NODATA
    return out


def load_s2(path):
    """(green, swir1) from an S2Hand chip, NaN where the chip has no data."""
    import rasterio

    with rasterio.open(path) as src:
        green = src.read(GREEN + 1).astype(np.float64)
        swir1 = src.read(SWIR1 + 1).astype(np.float64)
    # Zero reflectance in both bands is fill, not a measurement.
    empty = (green == 0) & (swir1 == 0)
    green[empty] = np.nan
    swir1[empty] = np.nan
    return green, swir1
