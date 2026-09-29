"""
Build a land cover training set from Earth Engine.

Why not DeepGlobe: it is RGB only, so NDVI, NDBI and MNDWI cannot be computed
from it at all - and those are the features the production pipeline uses. It is
also 0.5 m imagery of the United States, against 10 m imagery of India in
deployment. A model trained on it would be learning a different problem.

Instead we sample the exact pixels the system actually sees: Sentinel-2 over
Indian districts, with the same indices `indices.py` computes, labelled by ESA
WorldCover - the same reference the index thresholds were validated against.
That makes "beat NDBI's IoU of 0.057" a like-for-like comparison rather than a
change of dataset dressed up as an improvement.

Run:  python -m scripts.export_training_data
      python -m scripts.export_training_data --per-class 400 --scale 20
"""

import argparse
import csv
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import ee

from core import earth_engine

from geo import indices

from core import paths

OUTPUT = paths.EVALUATION / "landcover_samples.csv"
WORLDCOVER = "ESA/WorldCover/v200"
S2 = "COPERNICUS/S2_SR_HARMONIZED"

# WorldCover is a 2021 product, so the imagery must be 2021 too.
WINDOW = ("2021-01-01", "2021-03-31")

# Our four classes, mapped from WorldCover's eleven.
#   1 water  2 vegetation  3 built-up  4 bare
CLASS_MAP = {
    10: 2,   # tree cover
    20: 2,   # shrubland
    30: 2,   # grassland
    40: 2,   # cropland
    50: 3,   # built-up
    60: 4,   # bare / sparse vegetation
    80: 1,   # permanent water
    90: 1,   # herbaceous wetland
    95: 1,   # mangroves
}
# 70 (snow/ice) and 100 (moss) are excluded: near-absent in our districts and
# not classes the platform claims to detect.

CLASS_NAMES = {1: "water", 2: "vegetation", 3: "built-up", 4: "bare"}

# Deliberately spans the landscapes where the index thresholds disagreed most:
# NDBI scored 0.322 in Bangalore and 0.001 in Jaisalmer, so both must be here or
# the classifier will learn only one of them.
DISTRICTS = [
    ("Punjab", "level1"),
    ("Kerala", "level1"),
    ("Bangalore Urban", "level2"),
    ("Jaisalmer", "level2"),
    ("Thrissur", "level2"),
    ("Ludhiana", "level2"),
    ("Mumbai", "level2"),
    ("Ahmadabad", "level2"),
    ("Dehra Dun", "level2"),
    ("Cuttack", "level2"),
]

# Raw bands plus every index the production pipeline computes. The model sees
# exactly what the threshold rules see, so any improvement is the model's.
BANDS = ["B2", "B3", "B4", "B8", "B11", "B12"]
INDEX_NAMES = ["ndvi", "ndwi", "mndwi", "ndbi", "ndmi", "bsi"]

# Sentinel-1 backscatter. Added after the optical-only classifier FAILED to
# separate built-up from bare: 58% of built-up pixels were predicted as bare,
# and the histograms showed the two classes overlapping in every optical index.
#
# Radar carries information optical does not. A building is a corner reflector -
# wall meeting ground bounces the pulse straight back to the satellite, so
# built-up areas are bright. Sand is smooth and scatters the pulse away, so
# desert is dark. That is exactly the distinction the optical bands cannot make.
SAR_BANDS = ["VV", "VH"]
SAR_DERIVED = ["vv_vh_ratio"]

FEATURES = BANDS + INDEX_NAMES + SAR_BANDS + SAR_DERIVED
OPTICAL_FEATURES = BANDS + INDEX_NAMES        # for the ablation


def initialise():
    earth_engine.initialize()


def geometry_for(name, level):
    dataset = f"FAO/GAUL/2015/{level}"
    field = "ADM1_NAME" if level == "level1" else "ADM2_NAME"
    collection = (
        ee.FeatureCollection(dataset)
        .filter(ee.Filter.eq("ADM0_NAME", "India"))
        .filter(ee.Filter.eq(field, name))
    )
    return collection.geometry() if collection.size().getInfo() else None


def mask_clouds(image):
    scl = image.select("SCL")
    keep = (
        scl.neq(0).And(scl.neq(1)).And(scl.neq(3))
        .And(scl.neq(7)).And(scl.neq(8)).And(scl.neq(9))
        .And(scl.neq(10)).And(scl.neq(11))
    )
    return image.updateMask(keep)


def sar_image(region):
    """Sentinel-1 backscatter over the same window.

    Median rather than minimum: this is land cover, not flood detection, so we
    want the typical response rather than the wettest moment. Speckle-filtered
    for the same reason the flood path filters it - single-pixel radar is noisy.
    """
    collection = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(region)
        .filterDate(*WINDOW)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .select(SAR_BANDS)
    )

    if collection.size().getInfo() == 0:
        return None

    composite = collection.median().clip(region)
    smoothed = composite.focal_median(30, "circle", "meters").rename(SAR_BANDS)

    # VV/VH separates surface scattering from volume scattering: buildings and
    # bare ground behave differently here even when their VV is similar.
    ratio = smoothed.select("VV").subtract(smoothed.select("VH")).rename("vv_vh_ratio")
    return smoothed.addBands(ratio)


def feature_image(region):
    """Sentinel-2 composite carrying raw bands and every derived index."""
    composite = (
        ee.ImageCollection(S2)
        .filterBounds(region)
        .filterDate(*WINDOW)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
        .map(mask_clouds)
        .median()
        .clip(region)
    )

    stack = composite.select(BANDS)
    for name in INDEX_NAMES:
        stack = stack.addBands(indices.compute(composite, name))
    return stack


def labelled_image(region):
    """Our four-class label, from WorldCover."""
    cover = ee.ImageCollection(WORLDCOVER).first().select("Map").clip(region)

    label = ee.Image(0)
    for worldcover_class, ours in CLASS_MAP.items():
        label = label.where(cover.eq(worldcover_class), ours)

    return label.rename("label").selfMask()      # drop unmapped classes


def sample_district(name, level, per_class, scale):
    region = geometry_for(name, level)
    if region is None:
        print(f"  ! {name}: not found in FAO GAUL")
        return []

    stack = feature_image(region)

    radar = sar_image(region)
    if radar is None:
        print(f"  ! {name}: no Sentinel-1 coverage, skipping")
        return []
    stack = stack.addBands(radar)

    stack = stack.addBands(labelled_image(region))

    # Stratified: without it, a district that is 98% vegetation contributes
    # almost nothing but vegetation, and the classifier never learns the rest.
    sampled = stack.stratifiedSample(
        numPoints=per_class,
        classBand="label",
        region=region,
        scale=scale,
        seed=42,
        geometries=False,
        tileScale=4,
    )

    try:
        rows = sampled.getInfo().get("features", [])
    except Exception as exc:
        print(f"  ! {name}: sampling failed ({str(exc)[:70]})")
        return []

    out = []
    for row in rows:
        props = row.get("properties", {})
        if props.get("label") is None:
            continue
        if any(props.get(f) is None for f in FEATURES):
            continue          # cloud-masked pixel
        out.append({**{f: props[f] for f in FEATURES},
                    "label": int(props["label"]),
                    "district": name})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-class", type=int, default=300,
                        help="samples per class per district")
    parser.add_argument("--scale", type=int, default=20,
                        help="sampling resolution in metres")
    parser.add_argument("--districts", type=int, default=None)
    args = parser.parse_args()

    initialise()
    districts = DISTRICTS[: args.districts]

    print(f"Sampling {args.per_class} points per class from {len(districts)} districts")
    print(f"Window {WINDOW[0]} to {WINDOW[1]}, scale {args.scale} m")
    print(f"Features: {', '.join(FEATURES)}\n")

    rows = []
    for name, level in districts:
        print(f"  {name} ...", end=" ", flush=True)
        district_rows = sample_district(name, level, args.per_class, args.scale)
        rows.extend(district_rows)

        counts = {}
        for row in district_rows:
            counts[CLASS_NAMES[row["label"]]] = counts.get(CLASS_NAMES[row["label"]], 0) + 1
        print(f"{len(district_rows):>5} samples  {counts}")

    if not rows:
        print("\nNo samples collected.")
        return

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FEATURES + ["label", "district"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n{len(rows)} samples -> {OUTPUT}")

    totals = {}
    for row in rows:
        name = CLASS_NAMES[row["label"]]
        totals[name] = totals.get(name, 0) + 1
    print("\nClass balance:")
    for name, count in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<12} {count:>6}  {count / len(rows):.1%}")

    meta = OUTPUT.with_suffix(".meta.json")
    import json
    meta.write_text(json.dumps({
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": S2,
        "labels": WORLDCOVER,
        "window": list(WINDOW),
        "scale_m": args.scale,
        "per_class_per_district": args.per_class,
        "districts": [d[0] for d in districts],
        "features": FEATURES,
        "classes": CLASS_NAMES,
        "samples": len(rows),
        "note": (
            "Features match backend/geo/indices.py exactly, so a model trained here "
            "is directly comparable with the index thresholds it replaces."
        ),
    }, indent=2), encoding="utf-8")
    print(f"Wrote {meta.name}")


if __name__ == "__main__":
    main()
