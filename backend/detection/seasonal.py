"""
Year-round and season-against-history images, for green cover and crop stress.

Both of today's rules look at one moment. Green cover thresholds a dry-season
NDVI median, and NDVI cannot tell a tree from a wheat field in February:
Kerala scores 0.905, Punjab 0.047. Crop stress thresholds NDMI, which says how
wet a leaf is now, not whether it is worse than it should be in this place at
this time of year.

The literature fixes both the same way - by looking at time:

* Trees stay green all year; crops green up and are harvested. Percentiles of
  a year of NDVI, quarterly medians and a one-cycle harmonic fit (amplitude,
  phase) describe that shape, and radar texture adds structure. Canopy height
  from ETH Zurich's 10 m map (Lang et al. 2023) is the most direct tree signal
  of all and is tried as a second variant.

* Drought is an anomaly. The Vegetation Condition Index (Kogan 1995) places
  this month's value between the worst and best values of the same month in
  earlier years: VCI = 100 (x - min) / (max - min). India's Manual for Drought
  Management (2016) uses it on NDVI and NDWI, takes the worse of the two, and
  reads 60-100 normal, 40-60 moderate, 0-40 severe.

The image builders live here, not in the notebooks, so that the notebook
measures exactly what the backend would run if the measurement passes.
Notebooks 10 and 11 hold the numbers; nothing here changes a shipped result
until they do.
"""

import math

import ee

S2 = "COPERNICUS/S2_SR_HARMONIZED"
S1 = "COPERNICUS/S1_GRD"
WORLDCOVER = "ESA/WorldCover/v200"
DYNAMIC_WORLD = "GOOGLE/DYNAMICWORLD/V1"
CANOPY_HEIGHT = "users/nlang/ETH_GlobalCanopyHeight_2020_10m_v1"

MOD13Q1 = "MODIS/061/MOD13Q1"      # 250 m, 16-day NDVI
MOD09A1 = "MODIS/061/MOD09A1"      # 500 m, 8-day surface reflectance (NDWI)
MCD12Q1 = "MODIS/061/MCD12Q1"      # 500 m, yearly land cover
JRC_MONTHLY = "JRC/GSW1_4/MonthlyHistory"

TREE = 10                          # WorldCover class code
DW_TREES = 1                       # Dynamic World label for trees
MODIS_CROPLAND = (12, 14)          # croplands, cropland/natural mosaic

# ------------------------------------------------------------ green cover

# Variant A: what a year of Sentinel-2 and Sentinel-1 says about a pixel.
GREEN_FEATURES_A = [
    "ndvi_p10", "ndvi_p50", "ndvi_p90", "ndvi_std",
    "ndvi_q1", "ndvi_q2", "ndvi_q3", "ndvi_q4",
    "ndvi_amp", "ndvi_phase",
    "ndmi_p50", "swir1_p50", "swir2_p50",
    "vv_p50", "vh_p50", "vv_std", "vh_std", "vv_vh",
]
# Variant B adds how tall the vegetation is.
GREEN_FEATURES_B = GREEN_FEATURES_A + ["canopy_h"]

# Today's rule, kept on every sampled pixel so both are scored on the same ones.
BASELINE_BAND = "ndvi_dry"
BASELINE_THRESHOLD = 0.4
DRY_WINDOW = ("01-01", "03-31")
DRY_CLOUD_LIMIT = 40               # as surface.composite


def mask_clouds(image):
    scl = image.select("SCL")
    keep = (
        scl.neq(0).And(scl.neq(1)).And(scl.neq(3))
        .And(scl.neq(7)).And(scl.neq(8)).And(scl.neq(9))
        .And(scl.neq(10)).And(scl.neq(11))
    )
    return image.updateMask(keep)


def _s2(region, start, end, cloud_limit):
    return (
        ee.ImageCollection(S2).filterBounds(region).filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_limit))
        .select(["B4", "B8", "B11", "B12", "SCL"])      # only what is used: less to move
        .map(mask_clouds)
    )


def _year_bands(year):
    origin = ee.Date.fromYMD(year, 1, 1)

    def add(image):
        ndvi = image.normalizedDifference(["B8", "B4"]).rename("ndvi")
        ndmi = image.normalizedDifference(["B8", "B11"]).rename("ndmi")
        swir = image.select(["B11", "B12"]).divide(10000).rename(["swir1", "swir2"])
        angle = image.date().difference(origin, "year").multiply(2 * math.pi)
        time = ee.Image.constant(angle).float()
        stack = ee.Image.cat(
            ndvi, ndmi, swir,
            ee.Image.constant(1).float().rename("const"),
            time.cos().rename("cos"), time.sin().rename("sin"),
        )
        # The constant bands carry no mask of their own; borrow NDVI's, or the
        # harmonic regression would fit cloud-masked pixels as zeros.
        return stack.updateMask(ndvi.mask()).copyProperties(image, ["system:time_start"])

    return add


def optical_year(region, year, cloud_limit=60):
    """NDVI shape over a calendar year, from every usable Sentinel-2 scene."""
    col = _s2(region, f"{year}-01-01", f"{year + 1}-01-01", cloud_limit).map(_year_bands(year))
    ndvi = col.select("ndvi")

    pct = ndvi.reduce(ee.Reducer.percentile([10, 50, 90])).rename(
        ["ndvi_p10", "ndvi_p50", "ndvi_p90"])
    p50 = pct.select("ndvi_p50")
    std = ndvi.reduce(ee.Reducer.stdDev()).rename("ndvi_std")

    # Monsoon quarters are often fully clouded; a missing quarter reads as
    # "typical" (the yearly median) rather than dropping the pixel.
    quarters = []
    for q in range(4):
        start = ee.Date.fromYMD(year, 1 + 3 * q, 1)
        quarters.append(
            ndvi.filterDate(start, start.advance(3, "month")).median()
            .unmask(p50).rename(f"ndvi_q{q + 1}"))

    fit = (
        col.select(["const", "cos", "sin", "ndvi"])
        .reduce(ee.Reducer.linearRegression(numX=3, numY=1))
        .select("coefficients").arrayProject([0]).arrayFlatten([["c0", "c1", "c2"]])
    )
    amp = fit.select("c1").hypot(fit.select("c2")).rename("ndvi_amp")
    phase = fit.select("c2").atan2(fit.select("c1")).rename("ndvi_phase")

    medians = col.select(["ndmi", "swir1", "swir2"]).median().rename(
        ["ndmi_p50", "swir1_p50", "swir2_p50"])
    return ee.Image.cat(pct, std, *quarters, amp, phase, medians)


def radar_year(region, year):
    """Median and spread of VV and VH over the year, lightly speckle-filtered."""
    col = (
        ee.ImageCollection(S1).filterBounds(region)
        .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .select(["VV", "VH"])
    )
    med = col.median().focal_median(30, "circle", "meters").rename(["vv_p50", "vh_p50"])
    std = col.reduce(ee.Reducer.stdDev()).rename(["vv_std", "vh_std"])
    ratio = med.select("vv_p50").subtract(med.select("vh_p50")).rename("vv_vh")
    return ee.Image.cat(med, std, ratio)


def baseline_ndvi(region, year):
    """Exactly what green cover thresholds today: NDVI of a dry-season median."""
    start, end = (f"{year}-{d}" for d in DRY_WINDOW)
    return (
        _s2(region, start, end, DRY_CLOUD_LIMIT).median()
        .normalizedDifference(["B8", "B4"]).rename(BASELINE_BAND)
    )


def canopy_height():
    return ee.Image(CANOPY_HEIGHT).select(0).rename("canopy_h")


def green_features(region, year, with_height=False):
    image = ee.Image.cat(optical_year(region, year), radar_year(region, year),
                         baseline_ndvi(region, year))
    if with_height:
        image = image.addBands(canopy_height())
    return image.clip(region)


def tree_references(region, year):
    """Two independent answers to "is this a tree": WorldCover and Dynamic World.

    WorldCover v200 is a 2021 map, so the year should be 2021. Dynamic World is
    a second opinion with different training data: a gain that shows on
    WorldCover but not on Dynamic World is fitting WorldCover's own habits.
    """
    worldcover = ee.ImageCollection(WORLDCOVER).first().select("Map")
    dw = (
        ee.ImageCollection(DYNAMIC_WORLD).filterBounds(region)
        .filterDate(f"{year}-01-01", f"{year + 1}-01-01").select("label").mode()
    )
    return ee.Image.cat(
        worldcover.rename("wc_class"),
        worldcover.eq(TREE).rename("tree"),
        dw.eq(DW_TREES).rename("dw_tree"),
    ).clip(region)


# ------------------------------------------------------------ crop stress

KHARIF_MONTHS = (8, 9, 10)


def _modis_ndvi(image):
    good = image.select("SummaryQA").lte(1)           # good or marginal
    return (image.select("NDVI").multiply(0.0001).updateMask(good)
            .rename("x").copyProperties(image, ["system:time_start"]))


def _modis_ndwi(image):
    qa = image.select("StateQA")
    clear = qa.bitwiseAnd(3).eq(0).And(qa.bitwiseAnd(4).eq(0))   # no cloud, no shadow
    # Gao (1996): NIR 0.86 um (band 2) against SWIR 1.64 um (band 6).
    return (image.normalizedDifference(["sur_refl_b02", "sur_refl_b06"])
            .updateMask(clear).rename("x").copyProperties(image, ["system:time_start"]))


_SOURCES = {"ndvi": (MOD13Q1, _modis_ndvi), "ndwi": (MOD09A1, _modis_ndwi)}


def monthly_max(index, year, month):
    """Maximum-value composite: the standard defence against residual cloud."""
    dataset, prepare = _SOURCES[index]
    start = ee.Date.fromYMD(year, month, 1)
    return ee.ImageCollection(dataset).filterDate(start, start.advance(1, "month")).map(prepare).max()


def baseline_years(year, first=2003, last=2022):
    """The history a month is compared with: twenty years, never the year itself."""
    return [y for y in range(first, last + 1) if y != year]


def vci(index, year, month, history):
    stack = ee.ImageCollection([monthly_max(index, y, month) for y in history])
    low, high = stack.min(), stack.max()
    spread = high.subtract(low)
    return (monthly_max(index, year, month).subtract(low).divide(spread)
            .multiply(100).clamp(0, 100).updateMask(spread.gt(0.01)))


def cropland(year):
    cover = (ee.ImageCollection(MCD12Q1).filterDate(f"{year}-01-01", f"{year + 1}-01-01")
             .first().select("LC_Type1"))
    mask = cover.eq(MODIS_CROPLAND[0])
    for code in MODIS_CROPLAND[1:]:
        mask = mask.Or(cover.eq(code))
    return mask


def not_water(year, month):
    """False where the month's surface-water map saw water.

    A flooded field has low NDVI and reads as severe drought: Andhra Pradesh's
    own August 2020 bulletin shows exactly that. Masking the water stops a
    flood being scored as a drought.
    """
    monthly = (ee.ImageCollection(JRC_MONTHLY)
               .filter(ee.Filter.eq("year", year)).filter(ee.Filter.eq("month", month)))
    water = ee.Image(ee.Algorithms.If(monthly.size().gt(0),
                                      monthly.first().select("water").eq(2), ee.Image(0)))
    return water.unmask(0).Not()


def stress_image(year, months=KHARIF_MONTHS, history=None):
    """Season VCI on cropland, per index, plus the share of severe pixels.

    Bands: vci_ndvi_<m>, vci_ndwi_<m> per month; vci_ndvi, vci_ndwi (season
    means); severe_share (fraction of cropland whose worse-of-two is below 40);
    crop_share (fraction of the area that is cropland).
    """
    history = history or baseline_years(year)
    crop = cropland(year)
    bands, ndvi_months, ndwi_months = [], [], []
    for month in months:
        keep = crop.And(not_water(year, month))
        a = vci("ndvi", year, month, history).updateMask(keep).rename(f"vci_ndvi_{month}")
        b = vci("ndwi", year, month, history).updateMask(keep).rename(f"vci_ndwi_{month}")
        bands += [a, b]
        ndvi_months.append(a)
        ndwi_months.append(b)
    season_ndvi = ee.ImageCollection([i.rename("v") for i in ndvi_months]).mean().rename("vci_ndvi")
    season_ndwi = ee.ImageCollection([i.rename("v") for i in ndwi_months]).mean().rename("vci_ndwi")
    worse = season_ndvi.min(season_ndwi)
    severe = worse.lt(40).rename("severe_share")
    return ee.Image.cat(*bands, season_ndvi, season_ndwi, severe,
                        crop.unmask(0).rename("crop_share"))


def districts(states=None):
    """FAO GAUL 2015 districts of India, optionally for some states only."""
    fc = ee.FeatureCollection("FAO/GAUL/2015/level2").filter(ee.Filter.eq("ADM0_NAME", "India"))
    if states:
        fc = fc.filter(ee.Filter.inList("ADM1_NAME", list(states)))
    return fc.select(["ADM1_NAME", "ADM2_NAME"])


def district_table(year, states=None, scale=1000, tile_scale=4):
    """One row per district: season VCIs, severe share, cropland share."""
    image = stress_image(year)
    return image.reduceRegions(collection=districts(states), reducer=ee.Reducer.mean(),
                               scale=scale, tileScale=tile_scale)
