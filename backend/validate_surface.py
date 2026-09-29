"""
Validate the surface analyses against ESA WorldCover.

The flood threshold was chosen by measurement (-20 dB on the mean of VV and VH,
IoU 0.489 on 441 hand-labelled chips). The six surface thresholds were borrowed
from textbooks. This closes that gap for five of them.

Method: ESA WorldCover v200 is a 10 m land cover map of the world for 2021,
produced by ESA. Treat it as ground truth. For each analysis, build our mask
from Sentinel-2, ask WorldCover the same question, and count agreements.

Everything runs inside Earth Engine. The confusion matrix comes from a single
frequency histogram over a combined image, so no raster is ever downloaded:

    combined = prediction * 2 + truth      0=TN  1=FN  2=FP  3=TP

Run:  python validate_surface.py
      python validate_surface.py --quick     (two districts, fewer steps)
"""

import argparse
import json
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import ee

import earth_engine

import indices
import surface

WORLDCOVER = "ESA/WorldCover/v200"

# WorldCover class codes.
TREE, SHRUB, GRASS, CROP = 10, 20, 30, 40
BUILT, BARE, SNOW, WATER = 50, 60, 70, 80
WETLAND, MANGROVE, MOSS = 90, 95, 100

# WorldCover is a 2021 product, so predictions must come from 2021 imagery.
YEAR = 2021

# A full year pulls over a thousand scenes for a large state, and taking a
# median across them dominates the runtime - far more than the threshold sweep
# itself. A dry-season quarter gives plenty of clear imagery at a fraction of
# the cost. Use --window full when a seasonal composite is actually needed.
WINDOWS = {
    "dry": ("2021-01-01", "2021-03-31"),
    "monsoon": ("2021-07-01", "2021-09-30"),
    "full": ("2021-01-01", "2021-12-31"),
}
WINDOW = WINDOWS["dry"]

# Which WorldCover classes count as "true" for each of our analyses.
TRUTH_CLASSES = {
    "vegetation_health": [TREE, SHRUB, GRASS, CROP],
    "green_cover": [TREE],
    "water_extent": [WATER],
    "built_up": [BUILT],
    "bare_ground": [BARE],
    # crop_stress is absent on purpose: no ground truth exists for moisture
    # stress. Leaving it unvalidated is more honest than inventing a proxy.
}

# Districts spanning the landscape types. Per-district results matter as much as
# the pooled number: a threshold that works in Kerala may fail in Rajasthan.
DISTRICTS = [
    ("Punjab", "level1", "irrigated cropland"),
    ("Kerala", "level1", "tropical forest and backwater"),
    ("Bangalore Urban", "level2", "dense urban"),
    ("Jaisalmer", "level2", "arid desert"),
    ("Thrissur", "level2", "mixed wetland and settlement"),
    ("Ludhiana", "level2", "intensive agriculture"),
]

QUICK_DISTRICTS = DISTRICTS[:2]

# Threshold ranges to sweep, chosen around the literature value.
SWEEPS = {
    "vegetation_health": ("ndvi", [round(0.10 + 0.05 * i, 2) for i in range(11)]),
    "green_cover": ("ndvi", [round(0.30 + 0.05 * i, 2) for i in range(9)]),
    "water_extent": ("mndwi", [round(-0.30 + 0.05 * i, 2) for i in range(13)]),
    "built_up": ("ndbi", [round(-0.20 + 0.05 * i, 2) for i in range(13)]),
    "bare_ground": ("bsi", [round(-0.20 + 0.05 * i, 2) for i in range(13)]),
}


def geometry_for(name, level):
    dataset = f"FAO/GAUL/2015/{level}"
    field = "ADM1_NAME" if level == "level1" else "ADM2_NAME"
    collection = (
        ee.FeatureCollection(dataset)
        .filter(ee.Filter.eq("ADM0_NAME", "India"))
        .filter(ee.Filter.eq(field, name))
    )
    if collection.size().getInfo() == 0:
        return None
    return collection.geometry()


def truth_mask(classes):
    cover = ee.ImageCollection(WORLDCOVER).first().select("Map")
    mask = ee.Image(0)
    for code in classes:
        mask = mask.Or(cover.eq(code))
    return mask.rename("truth")


def _counts_from(histogram):
    counts = histogram or {}
    return {
        "tn": int(float(counts.get("0", 0))),
        "fn": int(float(counts.get("1", 0))),
        "fp": int(float(counts.get("2", 0))),
        "tp": int(float(counts.get("3", 0))),
    }


def confusion_all_thresholds(index_image, truth, observed, region, candidates,
                             direction, scale=200):
    """Confusion matrices for EVERY candidate threshold in ONE request.

    Each threshold becomes a band of a single image, and one frequency
    histogram returns a separate count dictionary per band. Calling
    reduceRegion once per threshold instead means a dozen full-state
    reductions per analysis, which is what made the first version unusably
    slow.

    combined = prediction*2 + truth  ->  0 TN, 1 FN, 2 FP, 3 TP
    """
    bands = []
    for threshold in candidates:
        prediction = (
            index_image.gt(threshold)
            if direction == "above"
            else index_image.lt(threshold)
        ).And(observed)

        combined = (
            prediction.unmask(0)
            .multiply(2)
            .add(truth.unmask(0))
            .rename(_band_name(threshold))
        )
        bands.append(combined)

    stacked = ee.Image.cat(bands)
    histograms = stacked.reduceRegion(
        reducer=ee.Reducer.frequencyHistogram(),
        geometry=region,
        scale=scale,
        maxPixels=1_000_000_000,
        bestEffort=True,
    ).getInfo()

    return {
        str(threshold): _counts_from(histograms.get(_band_name(threshold)))
        for threshold in candidates
    }


def _band_name(threshold):
    """Earth Engine band names cannot contain '.' or '-'."""
    return "t" + str(threshold).replace("-", "n").replace(".", "_")


def scores(counts):
    tp, fp, fn, tn = counts["tp"], counts["fp"], counts["fn"], counts["tn"]

    def safe(numerator, denominator):
        return round(numerator / denominator, 4) if denominator else 0.0

    precision = safe(tp, tp + fp)
    recall = safe(tp, tp + fn)
    f1 = safe(2 * precision * recall, precision + recall) if (precision + recall) else 0.0

    return {
        "iou": safe(tp, tp + fp + fn),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        **counts,
    }


def sweep_district(geometry, composite_image, scene_counts, analysis_type, scale):
    """Score every candidate threshold for one analysis, in one request.

    Takes a pre-built composite: the same imagery serves all five analyses for a
    district, so building it once per district instead of once per analysis
    removes four redundant compositing passes.
    """
    index_name, candidates = SWEEPS[analysis_type]
    config = surface.ANALYSES[analysis_type]

    truth = truth_mask(TRUTH_CLASSES[analysis_type])
    index_image = indices.compute(composite_image, index_name)
    observed = composite_image.select(indices.GREEN).mask()

    counts = confusion_all_thresholds(
        index_image,
        truth.And(observed),
        observed,
        geometry,
        candidates,
        config["direction"],
        scale,
    )

    return {
        "scenes_used": scene_counts["usable"],
        "scenes_available": scene_counts["available"],
        "thresholds": {t: scores(c) for t, c in counts.items()},
    }


def best_threshold(per_district):
    """Pool counts across districts, then pick the threshold with the best IoU.

    Pooling rather than averaging per-district IoU: a small district should not
    carry the same weight as a large one.
    """
    pooled = {}
    for district in per_district.values():
        for threshold, result in (district.get("thresholds") or {}).items():
            entry = pooled.setdefault(
                threshold, {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
            )
            for key in entry:
                entry[key] += result[key]

    scored = {t: scores(c) for t, c in pooled.items()}
    if not scored:
        return None, {}
    best = max(scored.items(), key=lambda kv: kv[1]["iou"])
    return best[0], scored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    # Threshold selection does not need fine resolution, and a whole state at
    # 200 m is ~1M pixels per band. 500 m keeps the sweep responsive.
    parser.add_argument("--scale", type=int, default=500)
    parser.add_argument(
        "--only", help="validate a single analysis type", default=None
    )
    parser.add_argument(
        "--window", choices=sorted(WINDOWS), default="dry",
        help="compositing window; 'full' is a year and is much slower",
    )
    parser.add_argument(
        "--districts", type=int, default=None,
        help="limit to the first N districts",
    )
    args = parser.parse_args()

    global WINDOW
    WINDOW = WINDOWS[args.window]

    earth_engine.initialize()

    districts = QUICK_DISTRICTS if args.quick else DISTRICTS
    if args.districts:
        districts = districts[: args.districts]
    types = [args.only] if args.only else list(TRUTH_CLASSES)

    print(f"Validating against {WORLDCOVER} ({YEAR})")
    print(f"Districts: {', '.join(d[0] for d in districts)}")
    print(f"Window: {WINDOW[0]} to {WINDOW[1]} ({args.window})")
    print(f"Scale: {args.scale} m\n")

    if args.quick:
        print(
            "  NOTE: --quick uses Punjab and Kerala only. Neither has meaningful\n"
            "  built-up or bare ground, so those two analyses cannot be judged\n"
            "  from a quick run. Use the full set for a fair test.\n"
        )

    # Build each district's composite once and reuse it for all five analyses.
    geometries = {}
    for name, level, description in districts:
        geometry = geometry_for(name, level)
        if geometry is None:
            print(f"  ! {name} not found in FAO GAUL, skipping")
            continue

        print(f"  building composite for {name} ...", end=" ", flush=True)
        try:
            composite_image, scene_counts = surface.composite(
                geometry, *WINDOW, cloud_limit=40
            )
        except surface.NoOpticalImagery as exc:
            print(f"skipped: {exc}")
            continue

        print(f"{scene_counts['usable']}/{scene_counts['available']} scenes")
        geometries[name] = (geometry, composite_image, scene_counts, description)

    print()

    output = {
        "reference": WORLDCOVER,
        "year": YEAR,
        "window": list(WINDOW),
        "scale_m": args.scale,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "analyses": {},
    }

    for analysis_type in types:
        if analysis_type not in SWEEPS:
            print(f"skipping {analysis_type}: no sweep defined")
            continue

        index_name, candidates = SWEEPS[analysis_type]
        literature = surface.ANALYSES[analysis_type]["threshold"]

        print("=" * 68)
        print(f"{analysis_type}  ({index_name.upper()}, literature value {literature})")
        print("=" * 68)

        per_district = {}
        for name, (geometry, composite_image, scene_counts, description) in geometries.items():
            print(f"  {name} ({description}) ...", end=" ", flush=True)
            try:
                result = sweep_district(
                    geometry, composite_image, scene_counts, analysis_type, args.scale
                )
            except Exception as exc:
                print(f"failed: {str(exc)[:70]}")
                continue

            per_district[name] = result
            at_literature = result["thresholds"].get(str(literature))
            if at_literature:
                print(f"IoU at {literature}: {at_literature['iou']:.3f}")
            else:
                print("done")

        best, pooled = best_threshold(per_district)
        if best is None:
            print("  no results\n")
            continue

        print(f"\n  {'threshold':>10}{'IoU':>8}{'prec':>8}{'recall':>8}")
        print("  " + "-" * 34)
        for threshold in sorted(pooled, key=float):
            row = pooled[threshold]
            marker = "  <-- best" if threshold == best else ""
            print(
                f"  {float(threshold):>10.2f}{row['iou']:>8.3f}"
                f"{row['precision']:>8.3f}{row['recall']:>8.3f}{marker}"
            )

        literature_score = pooled.get(str(literature), {}).get("iou", 0)
        best_score = pooled[best]["iou"]
        gain = best_score - literature_score

        print(
            f"\n  literature {literature} -> IoU {literature_score:.3f}"
            f"\n  measured   {best} -> IoU {best_score:.3f}"
            f"\n  gain       {gain:+.3f}\n"
        )

        output["analyses"][analysis_type] = {
            "index": index_name,
            "literature_threshold": literature,
            "literature_iou": literature_score,
            "measured_threshold": float(best),
            "measured_scores": pooled[best],
            "pooled": pooled,
            "per_district": per_district,
        }

    output["analyses"]["crop_stress"] = {
        "validated": False,
        "reason": (
            "No ground truth exists for vegetation moisture stress. Validating "
            "against a proxy would misrepresent the confidence available."
        ),
    }

    with open("surface_validation.json", "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2)

    print("=" * 68)
    print("Saved surface_validation.json")
    print("\nSummary of measured thresholds:")
    for name, result in output["analyses"].items():
        if result.get("validated") is False:
            print(f"  {name:<20} not validated - {result['reason'][:50]}...")
        else:
            print(
                f"  {name:<20} {result['literature_threshold']} -> "
                f"{result['measured_threshold']}  "
                f"(IoU {result['literature_iou']:.3f} -> "
                f"{result['measured_scores']['iou']:.3f})"
            )


if __name__ == "__main__":
    sys.exit(main())
