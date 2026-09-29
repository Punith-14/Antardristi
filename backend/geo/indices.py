"""
Spectral indices on Sentinel-2 surface reflectance.

IMPORTANT - validation status.

The Sentinel-1 flood threshold of -20 dB on the mean of VV and VH was chosen by
measuring both bands and six fusion rules against 441 hand-labelled chips. The
thresholds in this file have NOT been validated that way. They are conventional
values from the remote sensing literature, and the literature values are
frequently wrong for a specific landscape: our own Otsu experiment produced
-11.3 dB where the measured optimum was -17 for VV alone.

Every index below therefore carries `validated: False`, and that fact travels
into the evidence record and into the generated report. A user must be able to
tell a measured number from a borrowed one.

Validating these against DeepGlobe or Indian ground truth is open work.
"""

import ee

# Sentinel-2 band aliases, so the formulas read like the literature.
BLUE, GREEN, RED = "B2", "B3", "B4"
NIR, SWIR1, SWIR2 = "B8", "B11", "B12"
RED_EDGE = "B5"


def _nd(image, a, b):
    """Normalised difference (a - b) / (a + b)."""
    return image.normalizedDifference([a, b])


def ndvi(image):
    """Vegetation. High where leaves are dense and healthy."""
    return _nd(image, NIR, RED).rename("ndvi")


def ndwi(image):
    """Open water, McFeeters 1996. Confused by built-up surfaces."""
    return _nd(image, GREEN, NIR).rename("ndwi")


def mndwi(image):
    """Water, Xu 2006. SWIR instead of NIR suppresses built-up false positives."""
    return _nd(image, GREEN, SWIR1).rename("mndwi")


def ndbi(image):
    """Built-up area. High over concrete, asphalt and bare rock alike."""
    return _nd(image, SWIR1, NIR).rename("ndbi")


def ndmi(image):
    """Vegetation moisture. Falls before NDVI does under water stress, which
    makes it the earlier drought signal."""
    return _nd(image, NIR, SWIR1).rename("ndmi")


def bsi(image):
    """Bare soil index."""
    numerator = image.select(SWIR1).add(image.select(RED)).subtract(
        image.select(NIR).add(image.select(BLUE))
    )
    denominator = image.select(SWIR1).add(image.select(RED)).add(
        image.select(NIR).add(image.select(BLUE))
    )
    return numerator.divide(denominator).rename("bsi")


def evi(image):
    """Enhanced vegetation index. Less prone to saturating over dense canopy
    than NDVI, and less sensitive to soil background."""
    return image.expression(
        "2.5 * ((nir - red) / (nir + 6 * red - 7.5 * blue + 1))",
        {
            "nir": image.select(NIR),
            "red": image.select(RED),
            "blue": image.select(BLUE),
        },
    ).rename("evi")


INDEX_FUNCTIONS = {
    "ndvi": ndvi,
    "ndwi": ndwi,
    "mndwi": mndwi,
    "ndbi": ndbi,
    "ndmi": ndmi,
    "bsi": bsi,
    "evi": evi,
}


# Conventional thresholds. NOT measured on Indian imagery - see module docstring.
THRESHOLDS = {
    "ndvi": {
        "dense_vegetation": 0.5,
        "vegetation": 0.3,
        "sparse": 0.15,
        "source": "conventional; McFeeters-era vegetation literature",
    },
    "ndwi": {
        "water": 0.0,
        "source": "McFeeters 1996",
    },
    "mndwi": {
        "water": 0.0,
        "source": "Xu 2006",
    },
    "ndbi": {
        "built_up": 0.0,
        "source": "Zha et al. 2003",
    },
    "ndmi": {
        "moist": 0.2,
        "dry": 0.0,
        "very_dry": -0.2,
        "source": "conventional moisture-stress ranges",
    },
    "bsi": {
        "bare": 0.0,
        "source": "conventional",
    },
}


def compute(image, name):
    if name not in INDEX_FUNCTIONS:
        raise ValueError(
            f"Unknown index '{name}'. Available: {sorted(INDEX_FUNCTIONS)}"
        )
    return INDEX_FUNCTIONS[name](image)


def classify_surface(image):
    """Rule-based land cover from index comparison.

    A placeholder for the Random Forest that will be trained on DeepGlobe.
    Priority order matters: water is tested first because open water has low
    NDVI and would otherwise fall through to 'bare'.

    Classes: 1 water, 2 vegetation, 3 built-up, 4 bare.
    """
    water = mndwi(image).gt(THRESHOLDS["mndwi"]["water"])
    vegetation = ndvi(image).gt(THRESHOLDS["ndvi"]["sparse"])
    built = ndbi(image).gt(THRESHOLDS["ndbi"]["built_up"])

    return (
        ee.Image(4)
        .where(built, 3)
        .where(vegetation, 2)
        .where(water, 1)
        .rename("surface_class")
        .toInt()
    )


SURFACE_CLASSES = {
    1: "water",
    2: "vegetation",
    3: "built-up",
    4: "bare",
}

CLASSIFIER_NOTE = (
    "Land cover here is a rule-based comparison of spectral indices using "
    "conventional thresholds, not a trained classifier. Unlike the flood "
    "threshold, these values have not been validated against labelled data for "
    "Indian landscapes, so class boundaries should be treated as indicative."
)
