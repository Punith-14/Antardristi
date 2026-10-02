"""
Water screening for an uploaded RGB image, with an evidence record.

This replaces legacy/ndwi.py, which was the one endpoint where "every number
is traceable" was not true. It returned a bare percentage, a severity label
and the sentence "it is likely to be a flood-affected region" - a flood claim
made from the fraction of pixels that are more blue than green.

What an uploaded photograph can actually support is narrow, and this module
says exactly that much:

- **A fraction of image pixels, never an area of ground.** A phone photo or a
  screenshot has no location and no scale. 40% of the pixels could be 40 m2 of
  puddle or 40 km2 of floodplain, and nothing in the file says which. So the
  evidence unit is percent of pixels, and no km2 figure is produced at all.

- **No confidence figure.** The blue-over-green threshold has never been
  scored against labelled imagery. The SAR rule's 0.768 precision is a
  measurement; any number put here would be a guess wearing the same clothes.
  The confidence field is left empty and a caveat says why.

- **The known failure stated plainly.** Floodwater carrying sediment is brown,
  not blue. This screen is most likely to miss exactly the water a flood photo
  contains, and to count blue roofs, tarpaulins and sky. That is printed with
  every result, not buried in documentation.

- **No flood verdict.** The old severity labels ("high" above 30%) described
  pixel colour, not flooding, and read as a flood assessment. They are gone.

Everything else follows the contract the satellite paths use: evidence record,
caveats in `unobserved.notes`, a report checked by the same deterministic
verifier, and a request_id so the result can be fetched and exported as a PDF.
"""

import hashlib
from pathlib import Path

import numpy as np

from core import paths
from core.evidence import EvidenceBuilder, build_provenance, utc_now
from core.verification import check_caveats, verify_report

# Bumped whenever the method changes, so a cached result from an older method
# is never served as though it came from this one. It is part of the
# request_id hash for that reason.
METHOD_VERSION = "rgb_screen_v1"

# (blue - green) / (blue + green) above this counts as water-like. Inherited
# unchanged from the legacy screen; it has not been tuned or validated, which
# is why no confidence is reported against it.
THRESHOLD = 0.08

# Hasler & Susstrunk (2003) colourfulness below this is "not colourful" on
# their scale. A colour-ratio screen has nothing to work with on such an
# image - a greyscale radar scene, a black-and-white scan - and says so.
LOW_COLOURFULNESS = 15.0

# Refuse rather than decode something that would take minutes or gigabytes.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 50_000_000

OUTPUT_DIR = paths.OUTPUTS / "images"

METHOD_TEXT = f"(blue - green) / (blue + green) > {THRESHOLD} on RGB pixels"

NOTE_UNVALIDATED = (
    "This colour screen has not been validated against labelled imagery, so "
    "no accuracy or confidence figure can be given for it."
)
NOTE_NO_SCALE = (
    "The image carries no location or scale, so every figure is a fraction "
    "of image pixels, not an area of ground."
)
NOTE_BROWN_WATER = (
    "Floodwater carrying sediment is usually brown rather than blue and is "
    "likely to be missed, while blue roofs, tarpaulins, vehicles and sky can "
    "be counted as water."
)

# Found by running the screen on data/raw/sample.jpg, a Sentinel-2 true-colour
# view of Kerala in August 2018: 34% of pixels counted, most of them the
# Arabian Sea. The Kuttanad floodwater is in there too, but nothing in one
# image separates it from water that is always there. The satellite path
# subtracts permanent water using JRC Global Surface Water; this cannot.
NOTE_PERMANENT_WATER = (
    "Permanent water such as sea, lakes and rivers is counted together with "
    "any flooding, because a single image cannot separate the two."
)

KNOWN_CONFUSIONS = [
    "Sediment-laden floodwater is brown and scores below the threshold.",
    "Sky in an oblique photograph is blue and scores above it.",
    "Blue roofs, tarpaulins, vehicles and clothing score above it.",
    "Shadows and dark water have little colour and score unpredictably.",
    "Compression artefacts in heavily re-saved images shift colour ratios.",
]


class UnreadableImage(ValueError):
    """The upload is not an image this module can measure, and says why."""


# ---------------------------------------------------------------- measuring

def decode(content):
    """Bytes -> BGR array, or UnreadableImage with a reason a person can use."""
    import cv2

    if not content:
        raise UnreadableImage("The upload was empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise UnreadableImage(
            f"The upload is {len(content) / 1024 / 1024:.1f} MB, over the "
            f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB limit."
        )

    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        # The extension said image; the bytes did not. Previously this path
        # returned an empty metadata dict and the endpoint failed with a
        # KeyError - a 500 for what is the user's file, not our bug.
        raise UnreadableImage(
            "The file could not be read as an image. Upload a PNG, JPEG or WEBP."
        )

    height, width = image.shape[:2]
    if height * width > MAX_PIXELS:
        raise UnreadableImage(
            f"The image is {width} x {height} pixels, over the "
            f"{MAX_PIXELS // 1_000_000} megapixel limit."
        )
    return image


def colourfulness(image_bgr):
    """Hasler & Susstrunk (2003) colourfulness. 0 is greyscale."""
    b, g, r = (image_bgr[:, :, i].astype(np.float64) for i in range(3))
    rg = r - g
    yb = 0.5 * (r + g) - b
    spread = np.sqrt(rg.std() ** 2 + yb.std() ** 2)
    centre = np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    return float(spread + 0.3 * centre)


def measure(image_bgr, threshold=THRESHOLD):
    """Pixel statistics for one image. Pure: no files, no side effects.

    Returns the mask as well, so the overlay is drawn from exactly the pixels
    that were counted rather than from a second computation that could differ.
    """
    green = image_bgr[:, :, 1].astype(np.float64)
    blue = image_bgr[:, :, 0].astype(np.float64)

    # The 1e-5 keeps a pure-black pixel (0 / 0) from becoming NaN. NaN > 0.08
    # is False, so it would not be counted either way - but NaN in an array
    # that is later averaged is how a figure quietly becomes NaN too.
    index = (blue - green) / (blue + green + 1e-5)
    mask = index > threshold

    total = int(mask.size)
    water = int(mask.sum())
    height, width = image_bgr.shape[:2]

    return {
        "mask": mask,
        "water_pixels": water,
        "total_pixels": total,
        "fraction_percent": round(100.0 * water / total, 2) if total else 0.0,
        "width": int(width),
        "height": int(height),
        "colourfulness": round(colourfulness(image_bgr), 1),
        "threshold": threshold,
    }


def draw_overlay(image_bgr, mask, path):
    """Counted pixels tinted magenta, so what was measured can be seen.

    Magenta because it is neither the blue being screened for nor the brown
    being missed, so the tint cannot be mistaken for water in the photo.
    """
    import cv2

    output = image_bgr.copy()
    tint = np.array([255, 0, 255], dtype=np.float64)
    output[mask] = (0.45 * output[mask] + 0.55 * tint).astype(np.uint8)

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), output)
    return path


# ----------------------------------------------------------- the evidence

def request_key(content, threshold=THRESHOLD):
    """What identifies this analysis: the bytes and the method, nothing else.

    The same file screened twice gets the same request_id; the same file under
    a changed method does not.
    """
    return {
        "kind": "upload",
        "sha256": hashlib.sha256(content).hexdigest(),
        "method": METHOD_VERSION,
        "threshold": threshold,
    }


def build_evidence(stats):
    """The evidence record and the caveats that must travel with it."""
    builder = EvidenceBuilder()

    water_id = builder.add(
        "water_like_pixels", stats["water_pixels"], "pixels",
        method=METHOD_TEXT,
        threshold=stats["threshold"], threshold_unit="index",
        source="uploaded image",
    )
    total_id = builder.add(
        "image_pixels", stats["total_pixels"], "pixels",
        note=f"{stats['width']} x {stats['height']}",
        source="uploaded image",
    )
    builder.add(
        "water_like_pixel_fraction", stats["fraction_percent"], "percent",
        method=METHOD_TEXT,
        threshold=stats["threshold"], threshold_unit="index",
        denominator="image_pixels",
        derived_from=[water_id, total_id],
        note="Fraction of image pixels, not of ground area.",
    )
    builder.add(
        "colourfulness", stats["colourfulness"], "index",
        method="Hasler and Susstrunk (2003) colourfulness metric",
        note=(
            f"Below {LOW_COLOURFULNESS:g} an image has too little colour for a "
            "colour-ratio screen to mean anything."
        ),
    )

    builder.note(NOTE_UNVALIDATED)
    builder.note(NOTE_NO_SCALE)
    builder.note(NOTE_BROWN_WATER)
    builder.note(NOTE_PERMANENT_WATER)
    if stats["colourfulness"] < LOW_COLOURFULNESS:
        builder.note(
            f"The image has very little colour (colourfulness "
            f"{stats['colourfulness']}, below {LOW_COLOURFULNESS:g}), so a "
            "colour-based screen can say little about it; a greyscale radar "
            "image cannot be screened this way at all."
        )
    return builder


def render_report(response):
    """Deterministic report text built only from the evidence record.

    Not sent to the language model. The model adds fluency, and there is
    nothing here that needs fluency: four numbers and the reasons not to
    over-read them. A model writing this would add only the chance of
    upgrading "blue pixels" into "flooding", which is the claim this module
    exists to stop making.
    """
    evidence = {item["quantity"]: item for item in response["evidence"]}
    fraction = evidence["water_like_pixel_fraction"]
    water = evidence["water_like_pixels"]
    total = evidence["image_pixels"]
    colour = evidence["colourfulness"]

    sentences = [
        f"{fraction['value']} percent of the image's pixels [{fraction['id']}] "
        f"({water['value']:,} of {total['value']:,} [{water['id']}] [{total['id']}]) "
        f"are more blue than green by the screening threshold of {fraction['threshold']}.",
        f"The image's colourfulness is {colour['value']} [{colour['id']}].",
        "This counts water-coloured pixels; it is not an assessment of flooding.",
    ]
    sentences.extend(response["unobserved"]["notes"])
    return " ".join(sentences)


def verify(response):
    """The same two checks the satellite reports get: numbers and caveats."""
    from pipeline.report import collect_extra_values

    text = response["report"]["text"]
    result = verify_report(
        text, response["evidence"], extra_values=collect_extra_values(response)
    )
    result["caveats"] = check_caveats(text, response["unobserved"]["notes"])
    result["passed"] = result["passed"] and result["caveats"]["passed"]
    return result


def analyse(content, filename, question="", output_dir=None):
    """Screen an uploaded image. Returns a contract-shaped response.

    Raises UnreadableImage for anything that is not a measurable image.
    """
    image = decode(content)
    stats = measure(image)

    key = request_key(content)
    from core import cache
    request_id = cache.key_for(key)

    overlay = draw_overlay(
        image, stats["mask"], Path(output_dir or OUTPUT_DIR) / f"upload_{request_id}.png"
    )
    # Overlays of uploaded photos are kept for the retention period only.
    from core import security
    security.purge_old_files(Path(output_dir or OUTPUT_DIR), patterns=("upload_*.png",))

    builder = build_evidence(stats)
    display_name = Path(filename or "upload").name

    response = {
        "schema_version": "1.0",
        "analysis_type": "uploaded_image_screening",
        "analysis_label": "Uploaded image water screening",
        "generated_at": utc_now(),
        "request_id": request_id,
        "question": question or None,
        "region": {
            "slug": "uploaded-image",
            "name": f"Uploaded image ({display_name})",
            "admin_level": None,
            "state": None,
            "boundary_source": "none - the image is not georeferenced",
            "boundary_vintage": None,
            "note": (
                "A photograph or screenshot has no location or scale. Nothing "
                "here describes a place on a map or an area of ground."
            ),
        },
        "period": None,
        "observation": {
            "sensor_used": "uploaded RGB image",
            "sensor_reason": "supplied by the user",
            "scenes_available": 1,
            "scenes_used": 1,
            "image_width_px": stats["width"],
            "image_height_px": stats["height"],
        },
        "unobserved": {"reason": None, "notes": builder.notes},
        "evidence": builder.to_list(),
        "zones": [],
        "provenance": build_provenance(
            pipeline_version=METHOD_VERSION,
            datasets=[{"id": "user upload", "role": "primary imagery",
                       "sha256": key["sha256"]}],
            known_confusions=KNOWN_CONFUSIONS,
        ),
        "artifacts": {"overlay_image": f"/outputs/images/{overlay.name}"},
    }

    response["report"] = {
        "text": render_report(response),
        "generator_model": None,
        "fallback_used": False,
        "fallback_reason": None,
        "rejected_attempt": None,
        "written_by": "deterministic template (uploads are never sent to the language model)",
    }
    response["verification"] = verify(response)

    cache.put(key, response)
    return response
