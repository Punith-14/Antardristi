import os
from pathlib import Path

import ee

from core import earth_engine
import requests

from core import paths


RAW_DATA_DIR = paths.RAW
OUTPUT_DIR = paths.OUTPUTS / "images"


class NoUsableImagery(RuntimeError):
    """Raised when a date window yields no scene passing the cloud filter.

    Carries the unfiltered scene count so the caller can distinguish
    "the satellite never looked" from "it looked but everything was cloud".
    """

    def __init__(self, available, cloud_limit, start_date, end_date):
        self.available = available
        self.cloud_limit = cloud_limit
        self.start_date = start_date
        self.end_date = end_date

        if available == 0:
            reason = "no Sentinel-2 scenes exist for this region and window"
        else:
            reason = (
                f"{available} scene(s) exist but none had cloud cover below "
                f"{cloud_limit}%"
            )
        super().__init__(
            f"No usable imagery for {start_date} to {end_date}: {reason}."
        )


def _initialize_earth_engine():
    """Kept as a name the rest of this module already calls."""
    earth_engine.initialize()


def _download_png(image, output_path, region, dimensions=1024):
    url = image.getThumbURL(
        {
            "region": region,
            "dimensions": dimensions,
            "format": "png",
            "crs": "EPSG:4326",
        }
    )

    response = requests.get(url, timeout=60)
    response.raise_for_status()
    output_path.write_bytes(response.content)


def _mask_s2_clouds_and_shadows(image):
    scl = image.select("SCL")
    valid_classes = (
        scl.neq(0)   # No data
        .And(scl.neq(1))   # Saturated/defective
        .And(scl.neq(3))   # Cloud shadow
        .And(scl.neq(7))   # Unclassified
        .And(scl.neq(8))   # Cloud medium probability
        .And(scl.neq(9))   # Cloud high probability
        .And(scl.neq(10))  # Thin cirrus
        .And(scl.neq(11))  # Snow or ice
    )
    return image.updateMask(valid_classes)


def analyze_region_water(
    bbox,
    region_geometry=None,
    slug="region",
    mode="disaster",
    start_date="2023-01-01",
    end_date="2023-12-31",
    cloud_limit=10,
    dimensions=1024,
    stats_scale=100,
    composite_method="median",
):
    """
    Run Earth Engine spectral water analysis for a region and export both
    the RGB composite and the highlighted output overlay.
    """
    _initialize_earth_engine()

    region = region_geometry if region_geometry is not None else ee.Geometry.Rectangle(bbox)
    export_region = region.bounds()
    dataset = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_limit))
        .map(_mask_s2_clouds_and_shadows)
    )

    # Diagnose *why* a window is empty: no imagery at all, or imagery that the
    # cloud filter rejected? In monsoon India this distinction matters a lot.
    unfiltered = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start_date, end_date)
    )
    counts = ee.Dictionary({
        "available": unfiltered.size(),
        "usable": dataset.size(),
    }).getInfo()

    scene_count = counts["usable"]
    available_count = counts["available"]

    if scene_count == 0:
        raise NoUsableImagery(
            available=available_count,
            cloud_limit=cloud_limit,
            start_date=start_date,
            end_date=end_date,
        )

    composite = dataset.median().clip(region)
    rgb = composite.select(["B4", "B3", "B2"])

    # NOTE on masking, verified with diagnose_masks.py:
    #   - JRC "occurrence" is nodata wherever water has NEVER been observed,
    #     which is most dry land, not just sea. Its mask cannot identify ocean;
    #     unmask(0) is correct.
    #   - Admin geometries from FAO GAUL are land-only (Kerala measured 38,002
    #     km2 against a 38,863 km2 reference), so no sea enters the statistics.
    #   - A separate sea mask is therefore unnecessary for polygon regions. It
    #     WOULD be needed for any region falling back to a raw bounding box.
    permanent_water = (
        ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
        .select("occurrence")
        .unmask(0)
        .gte(90)
        .clip(region)
        .rename("permanent_water")
    )

    valid_observation_mask = composite.select("B3").mask().rename("valid_observation")
    analysis_area = (
        permanent_water.Not()
        .And(valid_observation_mask)
        .rename("analysis_area")
    )

    if mode == "urban":
        index_bands = ["B3", "B11"]
        threshold = 0.18
        index_label = "MNDWI"
        band_label = "Green and SWIR"
    else:
        index_bands = ["B3", "B8"]
        threshold = 0.12
        index_label = "NDWI"
        band_label = "Green and NIR"

    if composite_method == "max_water":
        # Compute the index on every scene, then keep the maximum. This captures
        # water that was present at ANY point in the window, which is what flood
        # mapping needs. A median composite deliberately rejects transient water
        # and so can never detect a flood.
        index = (
            dataset.map(
                lambda img: img.normalizedDifference(index_bands).rename("water_index")
            )
            .max()
            .clip(region)
        )
        composite_label = "per-scene maximum index over the window"
    else:
        index = composite.normalizedDifference(index_bands).rename("water_index")
        composite_label = "median composite"

    method_name = (
        f"{index_label} from Sentinel-2 {band_label} bands "
        f"({composite_label}) with permanent-water exclusion"
    )

    water_mask = index.gt(threshold).And(analysis_area).rename("water_mask")
    pixel_area = ee.Image.pixelArea()
    water_area_image = pixel_area.updateMask(water_mask).rename("water_area")
    analysis_area_image = pixel_area.updateMask(analysis_area).rename("analysis_area_m2")

    # Single round trip. Calling .getInfo() separately for each derived value
    # makes Earth Engine recompute the whole reduction every time, which is both
    # slow and wasteful of the monthly EECU budget.
    area_stats = water_area_image.addBands(analysis_area_image).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=region,
        scale=stats_scale,
        maxPixels=1_000_000_000,
        bestEffort=True,
    ).getInfo()

    water_area_m2 = area_stats.get("water_area") or 0
    analysis_area_m2 = area_stats.get("analysis_area_m2") or 0

    water_percentage = (water_area_m2 / analysis_area_m2 * 100) if analysis_area_m2 else 0
    water_area_sq_km = water_area_m2 / 1_000_000
    analysis_area_sq_km = analysis_area_m2 / 1_000_000

    coverage_stats = permanent_water.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=stats_scale,
        maxPixels=1_000_000_000,
        bestEffort=True,
    ).getInfo()

    permanent_water_fraction = (coverage_stats.get("permanent_water") or 0) * 100

    if water_percentage > 30:
        explanation = "High water presence detected. Possible flood-affected region."
        severity = "high"
    elif water_percentage > 10:
        explanation = "Moderate water presence detected. Area may be partially flooded."
        severity = "moderate"
    else:
        explanation = "Low water presence detected. No significant flooding observed."
        severity = "low"

    rgb_visual = rgb.visualize(min=0, max=3000, gamma=1.2)
    water_visual = water_mask.selfMask().visualize(palette=["00b7ff"])
    overlay_visual = rgb_visual.blend(water_visual)

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    raw_output_path = RAW_DATA_DIR / f"{slug}_gee.png"
    overlay_output_path = OUTPUT_DIR / f"{slug}_result.png"

    _download_png(rgb_visual, raw_output_path, export_region, dimensions=dimensions)
    _download_png(overlay_visual, overlay_output_path, export_region, dimensions=dimensions)

    metadata = {
        "source": "Google Earth Engine / COPERNICUS/S2_SR_HARMONIZED",
        "date_range": {"start": start_date, "end": end_date},
        "scene_count": scene_count,
        "scenes_available": available_count,
        "scenes_rejected_by_cloud_filter": available_count - scene_count,
        "cloud_limit": cloud_limit,
        "bbox": bbox,
        "method": method_name,
        "threshold": threshold,
        "stats_scale_m": stats_scale,
        "composite_method": composite_method,
        "severity": severity,
        "raw_image_url": f"/data/raw/{raw_output_path.name}",
        "output_image_url": f"/outputs/images/{overlay_output_path.name}",
        "index_type": "MNDWI" if mode == "urban" else "NDWI",
        "permanent_water_excluded": True,
        "permanent_water_percentage": permanent_water_fraction,
        "cloud_shadow_masked": True,
        "water_area_sq_km": water_area_sq_km,
        "analysis_area_sq_km": analysis_area_sq_km,
    }

    return water_percentage, explanation, metadata
