"""
Flood water from Sentinel-2, in the same evidence shape as the SAR path.

Why this exists. Until now /analyze with sensor="sentinel-2" raised
NotImplementedError, and the only optical water figure in the system came
from /analyze/surface "water_extent". That analysis answers a different
question in different units:

    SAR flood path                   surface water_extent
    ------------------------------   ------------------------------
    flood_extent (km2)               water_area (km2)
    permanent water excluded         permanent water included
    net_new_water vs a baseline      net_change vs a baseline
    scored on Sen1Floods11 floods    scored on WorldCover permanent water

Put those side by side in a results chapter and the difference between the
two columns is mostly the difference between the two questions, not between
the two sensors. This module answers the flood question with optical imagery
and nothing else changed: same quantities, same JRC permanent-water mask, same
zone extraction, same arithmetic for the baseline. What is left in the
comparison is the sensor.

Two things it deliberately does NOT do.

1. **Borrow an accuracy figure.** The surface analysis's IoU 0.466 was
   measured for permanent water against WorldCover - a different task. This
   rule was instead scored by notebook 05 on the same Sen1Floods11 chips as
   the SAR rule, on the pixels both sensors could see. Doing so showed that
   borrowing the surface threshold as well would have been wrong: -0.15 scored
   precision 0.369 on floods. The flood path now has its own measured
   threshold, and its score travels with a caveat about why it flatters optical.

2. **Pretend it saw through cloud.** Cloud-masked pixels are unobserved, and
   they are subtracted from the observable area rather than counted as dry.
   The failure this guards against is not hypothetical: Kerala in August 2019
   collapsed from 37,575 km2 observable to 6,820 km2, and a naive optical
   figure reported LESS water during a major flood, because the only pixels it
   could see were the clear - and therefore un-rained-on - ones.
"""

from detection import surface
from geo import indices

S2_COLLECTION = surface.S2
INDEX = "mndwi"

# The flood threshold, measured on floods - and deliberately NOT the surface
# analysis's -0.15.
#
# This first borrowed -0.15 from surface water_extent, where it was the best
# value for permanent water against WorldCover. Notebook 05 scored it on the
# 440 Sen1Floods11 flood chips and it was the wrong number for this task:
#
#     MNDWI >    IoU    precision   recall
#     -0.15     0.367     0.369     0.987    <- borrowed: calls most land water
#      0.10     0.716     0.763     0.920
#      0.15     0.743     0.829     0.878    <- ships
#      0.20     0.736     0.874     0.823
#
# The optimum is flat from about 0.10 to 0.20, so 0.15 is not a lucky spike.
# evaluation/optical.py SHIPPED_MNDWI must equal this; a test holds them.
THRESHOLD = 0.15

# Scene-level cloud filter for the composite. Pixel-level cloud is removed
# separately by the SCL mask; this only stops the median being built from
# scenes that are mostly cloud.
DEFAULT_CLOUD_LIMIT = 40

# From backend/notebooks/05_optical_flood.ipynb, at THRESHOLD, on the pixels
# both sensors could see. Radar scored 0.491 on the same pixels.
VALIDATION = {
    "dataset": "Sen1Floods11 HandLabeled (440 chips, S2 L1C, pixels common with S1)",
    "iou": 0.743,
    "precision": 0.829,
    "recall": 0.878,
}

# Why 0.743 is not "optical beats radar". Travels with every validated optical
# result, because the score alone would say exactly that.
#
# One sentence, not three notes: every note must survive into the generated
# report, and a report asked to carry too many drops some and fails the
# completeness check - see the comment on this in detection/surface.py.
VALIDATION_CAVEAT = (
    "That score flatters optical: the labels were drawn partly from Sentinel-2 "
    "indices, cloud-covered ground was never scored, and it was measured on "
    "Level-1C imagery while this result uses Level-2A, on which the same "
    "threshold likely finds somewhat less water."
)

# The IoU the surface analysis measured, kept only so the "not this number"
# note can name it explicitly. Reading the surface module's validation block
# means the note cannot drift from the number it disclaims.
_SURFACE_WATER = surface.ANALYSES["water_extent"]["validation"]

NOT_VALIDATED_NOTE = (
    f"This optical flood rule (MNDWI above {THRESHOLD}) has not yet been scored "
    "against flood labels, so no accuracy or confidence figure is given. The "
    f"IoU of {_SURFACE_WATER['iou']} measured for surface water describes a "
    "different task, permanent water against WorldCover, and does not apply here."
)

KNOWN_CONFUSIONS = [
    "Optical imagery cannot see through cloud. Cloud-masked pixels are counted "
    "as unobserved, not as dry, but monsoon floods are often mostly unobserved.",
    "Cloud shadow and terrain shadow are dark in the shortwave infrared and "
    "can read as water.",
    "Thin cirrus and haze are not always caught by the scene classification "
    "mask and can shift the index either way.",
    "Water under a vegetation canopy is invisible from above and is missed.",
    "The composite is a median over the window, so a flood lasting only part "
    "of it may be averaged away.",
]


def detect_water(region, start_date, end_date, cloud_limit=DEFAULT_CLOUD_LIMIT,
                 threshold=None):
    """Water mask from Sentinel-2 MNDWI.

    Returns (water_mask, valid_mask, composite, info), mirroring
    sar.detect_water's (water_mask, composite, info) plus the explicit valid
    mask - which SAR does not need, because radar has no cloud to subtract.

    Raises surface.NoOpticalImagery when no scene in the window is clear
    enough to use.
    """
    from core import scenes as scene_list

    image, counts, used_scenes = surface.composite(
        region, start_date, end_date, cloud_limit, return_collection=True)
    # The scenes that passed the cloud filter - the ones the median was built
    # from - not every scene in the window.
    scenes = scene_list.describe_safely(used_scenes, "sentinel-2", (start_date, end_date))
    index_image = indices.compute(image, INDEX)

    used = THRESHOLD if threshold is None else float(threshold)
    valid = image.select(indices.GREEN).mask()
    water = index_image.gt(used).And(valid).rename("water_mask")

    info = {
        "sensor": "sentinel-2",
        "collection": S2_COLLECTION,
        "index": INDEX,
        "scene_count": counts["usable"],
        "scenes_available": counts["available"],
        "cloud_limit": cloud_limit,
        "threshold": used,
        "threshold_unit": "MNDWI",
        # Distinguishes the shipped value from a caller-supplied one, so a
        # validation figure can never attach to a threshold it was not
        # measured at - the same rule sar.detect_water follows.
        "threshold_source": "fixed_shipped" if threshold is None else "fixed",
        "validation": dict(VALIDATION) if (VALIDATION and threshold is None) else None,
        "composite": "median of cloud-masked scenes over window",
        "scenes": scenes,
        "acquisition_days": scenes.get("days") if "error" not in scenes else None,
    }
    return water, valid, image, info


def method_text(info):
    """The evidence record's method string, describing what actually ran."""
    return (
        f"Sentinel-2 {info['index'].upper()} above {info['threshold']} "
        f"({info['threshold_source']}), cloud-masked, permanent water excluded"
    )
