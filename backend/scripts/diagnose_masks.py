"""
Diagnostic: is the sea actually inside our region geometry, and which mask
correctly identifies it?

Run:  python -m scripts.diagnose_masks

Reference land areas (km^2), for comparison with what we compute:
    Kerala  ~38,863      Punjab  ~50,362      Surat district ~4,549
"""

import os

from dotenv import load_dotenv

load_dotenv()

import ee

from core import earth_engine

earth_engine.initialize()

SCALE = 300          # coarse on purpose: this is a shape question, not a precision one
REFERENCE = {"Kerala": 38863, "Punjab": 50362}


def km2(image, geometry, band):
    """Area in km^2 of the pixels where `image` is 1."""
    stats = (
        ee.Image.pixelArea()
        .updateMask(image)
        .rename(band)
        .reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=geometry,
            scale=SCALE,
            maxPixels=1_000_000_000,
            bestEffort=True,
        )
        .getInfo()
    )
    return (stats.get(band) or 0) / 1_000_000


def diagnose(state_name):
    geom = (
        ee.FeatureCollection("FAO/GAUL/2015/level1")
        .filter(ee.Filter.eq("ADM0_NAME", "India"))
        .filter(ee.Filter.eq("ADM1_NAME", state_name))
        .geometry()
    )

    polygon_area = geom.area(maxError=100).getInfo() / 1_000_000
    ones = ee.Image.constant(1)

    # Candidate A: ESA WorldCover has no data -> sea?
    wc_land = ee.ImageCollection("ESA/WorldCover/v200").first().mask()
    wc_sea = wc_land.Not().unmask(1)

    # Candidate B: JRC occurrence mask (the one that failed)
    gsw = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence")
    gsw_nodata = gsw.mask().Not()

    # Candidate C: JRC max_extent mask
    max_extent = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("max_extent")
    maxext_nodata = max_extent.mask().Not()

    permanent = gsw.unmask(0).gte(90)

    print(f"\n=== {state_name} ===")
    print(f"  reference land area        {REFERENCE.get(state_name, '?'):>10} km2")
    print(f"  GAUL polygon area          {polygon_area:>10.0f} km2")
    print(f"  area covered by geometry   {km2(ones, geom, 'all'):>10.0f} km2")
    print(f"  A) WorldCover nodata       {km2(wc_sea, geom, 'a'):>10.0f} km2  <- candidate sea")
    print(f"  B) GSW occurrence nodata   {km2(gsw_nodata, geom, 'b'):>10.0f} km2  <- the bad one")
    print(f"  C) GSW max_extent nodata   {km2(maxext_nodata, geom, 'c'):>10.0f} km2")
    print(f"  permanent water (occ>=90)  {km2(permanent, geom, 'p'):>10.0f} km2")


def diagnose_bbox(label, bbox):
    """Regions without a geometry_source fall back to a raw rectangle."""
    geom = ee.Geometry.Rectangle(bbox)
    ones = ee.Image.constant(1)

    wc_land = ee.ImageCollection("ESA/WorldCover/v200").first().mask()
    wc_sea = wc_land.Not().unmask(1)

    total = km2(ones, geom, "all")
    sea = km2(wc_sea, geom, "a")

    print(f"\n=== {label} (raw bbox fallback) ===")
    print(f"  rectangle area             {total:>10.0f} km2")
    print(f"  WorldCover nodata (sea)    {sea:>10.0f} km2")
    print(f"  sea fraction               {sea / total * 100 if total else 0:>10.1f} %")


if __name__ == "__main__":
    diagnose("Kerala")   # coastal - expect a real sea number if the polygon includes ocean
    diagnose("Punjab")   # landlocked - every sea candidate should be ~0

    # These two have no geometry_source in REGIONS, so they use the bbox.
    diagnose_bbox("Surat", [72.6, 21.0, 73.0, 21.4])
    diagnose_bbox("Bengaluru", [77.3, 12.8, 77.9, 13.3])
