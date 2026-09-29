"""
Why did zone extraction return nothing?

Walks the vectorisation step by step and prints what survives each stage.

Run:  python diagnose_zones.py
"""

import os

from dotenv import load_dotenv

load_dotenv()

import ee

import analysis
import sar

POST = ("2018-08-15", "2018-08-25")
SCALE = 200


def main():
    analysis._initialize()

    region = (
        ee.FeatureCollection("FAO/GAUL/2015/level1")
        .filter(ee.Filter.eq("ADM0_NAME", "India"))
        .filter(ee.Filter.eq("ADM1_NAME", "Kerala"))
        .geometry()
    )

    print("Building flood mask...")
    collection = sar.get_collection(region, *POST)
    orbit = sar.dominant_orbit(collection).getInfo()
    mask, composite, info = sar.detect_water(
        region, *POST, relative_orbit=orbit, scale=SCALE
    )
    permanent = analysis.permanent_water_mask(region)
    flood = mask.And(permanent.Not()).rename("flood_mask")

    area = analysis.area_km2(flood, region, SCALE)
    print(f"  flood mask area: {area:,.1f} km2  (threshold {info['threshold']} dB)")

    print("\nStage 1 - reduceToVectors")
    vectors = flood.selfMask().reduceToVectors(
        geometry=region,
        scale=SCALE,
        geometryType="polygon",
        eightConnected=True,
        labelProperty="flooded",
        maxPixels=1_000_000_000,
        bestEffort=True,
    )
    raw_count = vectors.size().getInfo()
    print(f"  polygons returned: {raw_count}")

    if raw_count == 0:
        print("\n  Nothing to vectorise. Check that the mask has 1-valued pixels")
        print("  and that selfMask() left them unmasked.")
        return

    print("\nStage 2 - annotate with area")

    def annotate(feature):
        geometry = feature.geometry()
        return feature.set({"area_km2": geometry.area(maxError=SCALE).divide(1_000_000)})

    annotated = vectors.map(annotate)
    areas = annotated.aggregate_array("area_km2").getInfo()
    areas = sorted([a for a in areas if a is not None], reverse=True)

    print(f"  annotated: {len(areas)}")
    if areas:
        print(f"  largest  : {areas[0]:.3f} km2")
        print(f"  median   : {areas[len(areas) // 2]:.3f} km2")
        print(f"  smallest : {areas[-1]:.3f} km2")
        print(f"  total    : {sum(areas):,.1f} km2")

    print("\nStage 3 - how many survive each minimum area")
    for threshold in (0.0, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0):
        kept = [a for a in areas if a >= threshold]
        share = sum(kept) / area * 100 if area else 0
        print(
            f"  >= {threshold:>4.2f} km2 : {len(kept):>5} zones, "
            f"{sum(kept):>8,.1f} km2 ({share:>4.0f}% of extent)"
        )

    print("\nStage 4 - top 10 by area")
    top = (
        annotated.filter(ee.Filter.gte("area_km2", 0.5))
        .sort("area_km2", False)
        .limit(10)
        .getInfo()
        .get("features", [])
    )
    print(f"  fetched {len(top)} features")
    for i, feature in enumerate(top, 1):
        props = feature.get("properties", {})
        geom = feature.get("geometry", {})
        print(
            f"    {i:>2}. {props.get('area_km2', 0):>8.2f} km2  "
            f"geometry type: {geom.get('type')}"
        )


if __name__ == "__main__":
    main()
