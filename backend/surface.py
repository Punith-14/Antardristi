"""
Surface analyses: vegetation, water, built-up, bare ground, moisture.

The six application domains - agriculture, urban growth, water resources,
forest cover, drought, land-use change - are not six systems. They are one
operation pointed at different questions:

    compute an index -> threshold it -> measure area -> optionally compare
    two dates -> cluster what changed

So this is a single config-driven analysis rather than six implementations.
Adding a domain means adding a dictionary entry.

Unlike the flood path, these use Sentinel-2 and are therefore blocked by cloud.
Slow phenomena tolerate that: a month-long compositing window usually finds
enough clear pixels. Coverage is measured and reported either way.
"""

import ee

import classifier
import indices
import mapping
import zones as zone_extraction
from evidence import EvidenceBuilder, Observation, build_provenance, coverage_warning, utc_now

S2 = "COPERNICUS/S2_SR_HARMONIZED"
PIPELINE_VERSION = "0.6.0"


class NoOpticalImagery(RuntimeError):
    def __init__(self, start_date, end_date, available, cloud_limit):
        self.available = available
        reason = (
            "no Sentinel-2 scenes exist for this region and window"
            if available == 0
            else f"{available} scene(s) exist but none below {cloud_limit}% cloud"
        )
        super().__init__(f"No usable imagery for {start_date} to {end_date}: {reason}.")


# Each entry is one user-facing question.
#
# Thresholds below are MEASURED, not borrowed. Each was chosen by sweeping
# candidate values across six Indian districts spanning cropland, forest, dense
# urban, arid desert and wetland, scored against ESA WorldCover v200 as ground
# truth (validate_surface.py, surface_validation.json, dry season 2021, 500 m).
#
# The `validation` block travels into every result, so a user always knows how
# much to trust the number. Two of these analyses do not work well, and the
# system says so rather than presenting all six as equivalent.
ANALYSES = {
    "vegetation_health": {
        "label": "Vegetation health",
        "index": "ndvi",
        "threshold": 0.2,
        "direction": "above",
        "quantity": "vegetated_area",
        "domain": "agriculture",
        "question": "How much of this region carries healthy vegetation?",
        "validation": {
            "iou": 0.888,
            "precision": 0.949,
            "recall": 0.932,
            "reliability": "good",
            "per_district": {
                "Kerala": 0.969, "Thrissur": 0.965, "Punjab": 0.957,
                "Ludhiana": 0.939, "Bangalore Urban": 0.716, "Jaisalmer": 0.112,
            },
            "caveat": (
                "Reliable in vegetated landscapes. The low score in arid "
                "Jaisalmer reflects there being almost no vegetation to detect "
                "rather than a detection failure."
            ),
        },
    },
    "crop_stress": {
        "label": "Crop and vegetation stress",
        "index": "ndmi",
        "threshold": indices.THRESHOLDS["ndmi"]["dry"],
        "direction": "below",
        "quantity": "moisture_stressed_area",
        "domain": "agriculture",
        "question": "Where is vegetation under moisture stress?",
        "note": (
            "NDMI falls before NDVI under water stress, so this is an earlier "
            "signal than greenness loss."
        ),
    },
    "water_extent": {
        "label": "Surface water",
        "index": "mndwi",
        "threshold": -0.15,
        "direction": "above",
        "quantity": "water_area",
        "domain": "water resources",
        "question": "How much open water is present?",
        "validation": {
            "iou": 0.466,
            "precision": 0.757,
            "recall": 0.548,
            "reliability": "moderate",
            "per_district": {
                "Kerala": 0.436, "Thrissur": 0.369, "Jaisalmer": 0.286,
                "Punjab": 0.232, "Bangalore Urban": 0.169, "Ludhiana": 0.000,
            },
            "caveat": (
                "Roughly a quarter of detections are false positives and almost "
                "half of open water is missed. Part of the apparent error is a "
                "definition mismatch: MNDWI detects seasonal water, while the "
                "WorldCover reference class covers permanent water only."
            ),
        },
    },
    "built_up": {
        "label": "Built-up area",
        "index": "ndbi",
        "threshold": -0.1,
        "direction": "above",
        "quantity": "built_up_area",
        "domain": "urban",
        "question": "How much of this region is built up?",
        # Prefer the trained classifier. NDBI alone is unusable here, and the
        # reason is physical rather than fixable by tuning: concrete and sand
        # are spectrally near-identical, so no threshold separates them.
        "classifier_class": "built-up",
        # These figures are asserted against models/landcover_rf_v1.json by
        # test_surface_claims_match_the_measured_scores. Retraining changes them
        # and the test fails until they are updated here - which is the point.
        "validation": {
            "iou": 0.433,
            "precision": 0.754,
            "recall": 0.505,
            "reliability": "moderate",
            "method": "Random Forest over Sentinel-2 and Sentinel-1 features",
            "per_district": {},
            "caveat": (
                "Uses a Random Forest trained on seven Indian districts and "
                "validated on two held out entirely (Jaisalmer and Bangalore "
                "Urban), scoring IoU 0.433. Roughly half of built-up land is "
                "still missed. The classifier requires Sentinel-1 radar: "
                "without it the system falls back to an NDBI threshold that "
                "scores 0.057 and should not be relied on."
            ),
            # Carries the same keys as the main block: the note formatter reads
            # precision and recall directly, and a partial dict raised KeyError
            # the first time radar was unavailable.
            "fallback": {
                "index": "ndbi",
                "threshold": -0.1,
                "iou": 0.057,
                "precision": 0.058,
                "recall": 0.713,
                "reliability": "poor",
            },
        },
    },
    "bare_ground": {
        "label": "Bare ground",
        "index": "bsi",
        "threshold": 0.15,
        "direction": "above",
        "quantity": "bare_area",
        "domain": "land degradation",
        "question": "How much ground is bare?",
        "validation": {
            "iou": 0.747,
            "precision": 0.804,
            "recall": 0.914,
            "reliability": "good",
            "per_district": {
                "Jaisalmer": 0.778, "Ludhiana": 0.023, "Punjab": 0.012,
                "Kerala": 0.004, "Bangalore Urban": 0.004, "Thrissur": 0.001,
            },
            "caveat": (
                "Validated almost entirely on arid Jaisalmer, the only district "
                "in the reference set with substantial bare ground. The score "
                "should be read as 'reliable in arid landscapes', not as a "
                "general figure. The threshold is also sharp: performance falls "
                "from 0.747 at 0.15 to 0.081 at 0.20."
            ),
        },
    },
    "green_cover": {
        "label": "Green cover",
        "index": "ndvi",
        "threshold": 0.4,
        "direction": "above",
        "quantity": "dense_vegetation_area",
        "domain": "forest",
        "question": "How much dense green cover is there?",
        "validation": {
            "iou": 0.405,
            "precision": 0.409,
            "recall": 0.975,
            "reliability": "poor",
            "per_district": {
                "Kerala": 0.905, "Thrissur": 0.864, "Bangalore Urban": 0.245,
                "Punjab": 0.047, "Ludhiana": 0.024, "Jaisalmer": 0.013,
            },
            "caveat": (
                "Only usable in genuinely forested regions. NDVI cannot "
                "distinguish tree canopy from dense cropland, so in agricultural "
                "districts almost every detection is a false positive: Kerala "
                "scores 0.905 but Punjab scores 0.047. Do not use this to "
                "measure forest cover in farming landscapes."
            ),
        },
    },
}

RELIABILITY_ORDER = {"good": 0, "moderate": 1, "poor": 2, "unvalidated": 3}


def _mask_clouds(image):
    scl = image.select("SCL")
    keep = (
        scl.neq(0).And(scl.neq(1)).And(scl.neq(3))
        .And(scl.neq(7)).And(scl.neq(8)).And(scl.neq(9))
        .And(scl.neq(10)).And(scl.neq(11))
    )
    return image.updateMask(keep)


def composite(region, start_date, end_date, cloud_limit=40):
    """Median composite, plus the scene counts needed for honest reporting."""
    unfiltered = (
        ee.ImageCollection(S2).filterBounds(region).filterDate(start_date, end_date)
    )
    usable = unfiltered.filter(
        ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_limit)
    ).map(_mask_clouds)

    counts = ee.Dictionary(
        {"available": unfiltered.size(), "usable": usable.size()}
    ).getInfo()

    if counts["usable"] == 0:
        raise NoOpticalImagery(
            start_date, end_date, counts["available"], cloud_limit
        )

    return usable.median().clip(region), counts


def _area_km2(mask, region, scale):
    stats = (
        ee.Image.pixelArea()
        .updateMask(mask)
        .rename("area")
        .reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=region,
            scale=scale,
            maxPixels=1_000_000_000,
            bestEffort=True,
        )
        .getInfo()
    )
    return (stats.get("area") or 0) / 1_000_000


def _threshold_mask(index_image, threshold, direction):
    return (
        index_image.gt(threshold) if direction == "above" else index_image.lt(threshold)
    )


def _detect(config, optical, index_image, region, start_date, end_date):
    """Classifier if we have one, index rule otherwise.

    Returns (mask, method, model_info). `method` is recorded in the evidence so
    a reader can tell which produced the number - the two differ by a factor of
    seven in accuracy for built-up.
    """
    target = config.get("classifier_class")
    threshold_mask = _threshold_mask(
        index_image, config["threshold"], config["direction"]
    )

    if not target:
        return threshold_mask, "index_threshold", None

    try:
        classified, info = classifier.classify(optical, region, start_date, end_date)
        return classifier.class_mask(classified, target), "classifier", info
    except classifier.ModelUnavailable as exc:
        return threshold_mask, f"index_threshold_fallback: {exc}", None
    except Exception as exc:
        return threshold_mask, f"index_threshold_fallback: {str(exc)[:120]}", None


def analyse(
    region_geometry,
    region_meta,
    analysis_type,
    post_start,
    post_end,
    pre_start=None,
    pre_end=None,
    cloud_limit=40,
    scale=100,
    include_zones=True,
):
    """One surface analysis, in the same contract shape as the flood path."""
    if analysis_type not in ANALYSES:
        raise ValueError(
            f"Unknown analysis '{analysis_type}'. Available: {sorted(ANALYSES)}"
        )

    config = ANALYSES[analysis_type]
    region = region_geometry
    region_area = region.area(maxError=100).getInfo() / 1_000_000

    post_image, post_counts = composite(region, post_start, post_end, cloud_limit)
    index_image = indices.compute(post_image, config["index"])

    # Use the trained classifier where one exists and beats the threshold.
    # Falls back to the index rule, loudly, when the model or its radar input
    # is unavailable - a degraded answer that says so beats no answer.
    mask, method_used, model_info = _detect(
        config, post_image, index_image, region, post_start, post_end
    )

    # Observed area is where the composite actually has data after cloud masking.
    valid = post_image.select(indices.GREEN).mask()
    observable_area = _area_km2(valid, region, scale)
    target_area = _area_km2(mask.And(valid), region, scale)

    builder = EvidenceBuilder()
    observation = Observation(
        sensor_used="sentinel-2",
        sensor_reason="optical_suitable_for_slow_phenomena",
        sensors_considered=["sentinel-2"],
        observable_area_km2=round(observable_area, 1),
        region_area_km2=region_area,
        scenes_available=post_counts["available"],
        scenes_used=post_counts["usable"],
    )

    validation = config.get("validation")
    used_classifier = method_used == "classifier"

    # The reported accuracy must describe the method that actually ran. Quoting
    # the classifier's 0.434 for a result the threshold produced would be the
    # exact kind of misattribution this project exists to prevent.
    if validation and not used_classifier and validation.get("fallback"):
        effective = validation["fallback"]
    else:
        effective = validation

    if used_classifier:
        method_text = (
            f"Random Forest ({model_info['model']}) over Sentinel-2 and "
            f"Sentinel-1 features"
        )
        source_text = f"{S2} + COPERNICUS/S1_GRD"
    else:
        method_text = (
            f"{config['index'].upper()} {config['direction']} "
            f"{config['threshold']} on Sentinel-2 median composite"
        )
        source_text = S2

    e_area = builder.add(
        config["quantity"],
        round(target_area, 1),
        "km2",
        method=method_text,
        threshold=None if used_classifier else config["threshold"],
        confidence=effective.get("precision") if effective else None,
        spatial_support_km2=round(observable_area, 1),
        source=source_text,
    )

    builder.add(
        f"{config['quantity']}_fraction",
        round(target_area / observable_area * 100, 2) if observable_area else 0.0,
        "percent",
        derived_from=[e_area],
        denominator="observable_area_km2",
        note="Percentage is of observed area, not total region area.",
    )

    # Measured accuracy travels with every result, including when it is bad.
    #
    # ONE note, not three. An earlier version emitted the scores, the caveat and
    # the model provenance separately, all repeating the same IoU. Five caveats
    # is more than a short report can carry, so the language model dropped some,
    # failed the completeness check, and fell back every single time - caveats
    # so numerous they stopped being read.
    if effective:
        if used_classifier:
            builder.note(
                f"Measured by {model_info['model']}, a Random Forest over "
                f"Sentinel-2 and Sentinel-1 features, scoring IoU "
                f"{effective['iou']} (precision {effective['precision']}, "
                f"recall {effective['recall']}) on "
                f"{', '.join(model_info['validated_on'])} - districts held out "
                "of training entirely. About half of this class is still missed."
            )
        else:
            builder.note(
                f"This method scores IoU {effective['iou']}, precision "
                f"{effective['precision']} and recall {effective['recall']} "
                f"on held-out validation. Reliability: {effective['reliability']}."
            )
            if validation.get("caveat") and not config.get("classifier_class"):
                builder.note(validation["caveat"])

        if not used_classifier and config.get("classifier_class"):
            # Silently serving the weak method under the strong method's name
            # would be the worst failure available here.
            builder.note(
                f"WARNING: the trained classifier could not run, so this result "
                f"used the {config['index'].upper()} threshold instead "
                f"({method_used.split(': ', 1)[-1]}). That method scores IoU "
                f"{effective['iou']} and should not be relied on."
            )

        if effective["reliability"] == "poor":
            builder.note(
                f"WARNING: {config['label']} detection is unreliable and these "
                "figures should not be used for decisions."
            )
    else:
        builder.note(
            f"The {config['index'].upper()} threshold of {config['threshold']} "
            "has not been validated against labelled data, so no accuracy "
            "figure is available for this analysis."
        )

    if config.get("note"):
        builder.note(config["note"])

    change_mask = None
    if pre_start and pre_end:
        try:
            pre_image, pre_counts = composite(
                region, pre_start, pre_end, cloud_limit
            )
            pre_index = indices.compute(pre_image, config["index"])
            # The baseline must use the same detector as the current window, or
            # the "change" is partly a change of method.
            pre_mask, _, _ = _detect(
                config, pre_image, pre_index, region, pre_start, pre_end
            )
            pre_valid = pre_image.select(indices.GREEN).mask()
            both_observed = valid.And(pre_valid)

            baseline_area = _area_km2(pre_mask.And(both_observed), region, scale)
            current_area = _area_km2(mask.And(both_observed), region, scale)

            e_base = builder.add(
                f"baseline_{config['quantity']}",
                round(baseline_area, 1),
                "km2",
                period_ref="pre",
                method="Same index and threshold on the earlier window",
                note="Measured only where both windows were cloud-free.",
            )
            builder.add(
                "net_change",
                round(current_area - baseline_area, 1),
                "km2",
                derived_from=[e_area, e_base],
                method="current minus baseline, over commonly observed area",
            )

            # Gained and lost, kept separate: a net of zero can hide large
            # offsetting changes in different places.
            gained = mask.And(pre_mask.Not()).And(both_observed)
            lost = pre_mask.And(mask.Not()).And(both_observed)

            builder.add(
                "area_gained",
                round(_area_km2(gained, region, scale), 1),
                "km2",
                derived_from=[e_area, e_base],
            )
            builder.add(
                "area_lost",
                round(_area_km2(lost, region, scale), 1),
                "km2",
                derived_from=[e_area, e_base],
            )
            change_mask = gained
        except NoOpticalImagery as exc:
            builder.note(f"Baseline could not be computed: {exc}")

    zone_result = {"zones": []}
    if include_zones:
        # Change is always discrete, so zone it even when the total coverage is
        # continuous: new construction appears in patches, built-up area does not.
        if change_mask is not None:
            zone_source = change_mask
            zone_area = next(
                (
                    item["value"]
                    for item in builder.to_list()
                    if item["quantity"] == "area_gained"
                ),
                None,
            )
        else:
            zone_source = mask.And(valid)
            zone_area = target_area

        try:
            zone_result = zone_extraction.extract(
                zone_source,
                region,
                scale=scale,
                simplify_m=scale,
                area_km2=zone_area,
                region_area_km2=observable_area,
            )
            if zone_result.get("skipped"):
                builder.note(zone_result["skipped_reason"])
        except Exception as exc:
            builder.note(f"Zone extraction failed, areas are unaffected: {exc}")

    surface_zones = zone_result.get("zones") or []
    if surface_zones:
        summary = zone_extraction.summarise(zone_result)
        e_zones = builder.add(
            "zone_count",
            summary["count"],
            "zones",
            method=f"Connected regions vectorised at {scale} m",
            derived_from=[e_area],
            note=f"{summary['listed']} largest listed" if summary["truncated"] else None,
        )
        builder.add(
            "largest_zone_area",
            summary["largest_area_km2"],
            "km2",
            derived_from=[e_zones],
            note=(
                f"Centred near {surface_zones[0]['centroid'][1]:.2f} N, "
                f"{surface_zones[0]['centroid'][0]:.2f} E"
            ),
        )

    warning = coverage_warning(observation)
    if warning:
        builder.note(warning)

    return {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "analysis_type": analysis_type,
        "analysis_label": config["label"],
        "domain": config["domain"],
        "region": region_meta,
        "period": {
            "mode": "comparison" if pre_start else "single",
            "post": {"start": post_start, "end": post_end},
            **({"pre": {"start": pre_start, "end": pre_end}} if pre_start else {}),
        },
        "observation": observation.to_dict(),
        "unobserved": {
            "reason": None,
            "masked_area_km2": round(max(region_area - observable_area, 0.0), 1),
            "region_area_km2": round(region_area, 1),
            "notes": builder.notes,
        },
        "evidence": builder.to_list(),
        "zones": [
            {k: v for k, v in zone.items() if k != "geometry"}
            for zone in surface_zones
        ],
        "zones_summary": zone_extraction.summarise(zone_result) if surface_zones else None,
        "zones_geojson": zone_extraction.to_geojson(surface_zones, "both"),
        "map": mapping.display_hints(
            analysis_type,
            is_change=bool(pre_start),
            zone_count=len(surface_zones),
            reliability=(validation or {}).get("reliability", "unvalidated"),
        ),
        "method": {
            "used": "classifier" if used_classifier else "index_threshold",
            "detail": method_used,
            "model": model_info["model"] if used_classifier else None,
        },
        "provenance": build_provenance(
            PIPELINE_VERSION,
            datasets=(
                [{"id": S2, "role": "primary imagery"}]
                + ([{"id": "COPERNICUS/S1_GRD", "role": "radar features"}]
                   if used_classifier else [])
                + [{
                    "id": region_meta.get("boundary_source", "unknown"),
                    "role": "administrative boundary",
                }]
            ),
            models=(
                [{
                    "id": model_info["model"],
                    "algorithm": model_info["algorithm"],
                    "validated_on": model_info["validated_on"],
                    "split": model_info["split"],
                    "scores": model_info["scores"],
                }]
                if used_classifier else []
            ),
            stats_scale_m=scale,
            known_confusions=(
                (classifier.KNOWN_CONFUSIONS if used_classifier
                 else [indices.CLASSIFIER_NOTE])
                + [
                    "Optical imagery cannot see through cloud; monsoon results "
                    "are computed from whatever was clear.",
                    "Boundary vintage is 2015; districts created later do not "
                    "resolve.",
                ]
            ),
        ),
        "_internal": {
            "mask": mask,
            "index": index_image,
            "region": region,
            "change_mask": change_mask,
            "palette": mapping.PALETTES.get(analysis_type, ["#3182bd"]),
        },
    }


def tile_urls(payload):
    """Earth Engine tile templates for the map.

    Returns the detected surface, and for change queries the gained area as a
    separate layer so a user can toggle "what is there now" against "what
    appeared".
    """
    internal = payload.get("_internal") or {}
    mask = internal.get("mask")
    if mask is None:
        return None

    palette = internal.get("palette") or ["#3182bd"]

    try:
        urls = {
            "surface_tiles": ee.Image(
                mask.selfMask().visualize(palette=palette)
            ).getMapId({})["tile_fetcher"].url_format
        }

        change_mask = internal.get("change_mask")
        if change_mask is not None:
            urls["change_tiles"] = ee.Image(
                change_mask.selfMask().visualize(palette=["#e31a1c"])
            ).getMapId({})["tile_fetcher"].url_format

        index_image = internal.get("index")
        if index_image is not None:
            urls["index_tiles"] = ee.Image(
                index_image.visualize(
                    min=-1, max=1, palette=["#8c510a", "#f5f5f5", "#01665e"]
                )
            ).getMapId({})["tile_fetcher"].url_format

        return urls
    except Exception:
        # A missing basemap must never fail an otherwise valid analysis.
        return None


def catalogue():
    """Available analyses with their measured reliability, best first.

    Ordered so the frontend surfaces methods that work before methods that do
    not. A platform that presents a 0.888 analysis and a 0.057 analysis as
    equivalent options is misleading by layout.
    """
    entries = []
    for key, config in ANALYSES.items():
        validation = config.get("validation")
        entries.append(
            {
                "type": key,
                "label": config["label"],
                "domain": config["domain"],
                "question": config["question"],
                "index": config["index"],
                "threshold": config["threshold"],
                "validated": validation is not None,
                "reliability": validation["reliability"] if validation else "unvalidated",
                "iou": validation["iou"] if validation else None,
                "precision": validation["precision"] if validation else None,
                "recall": validation["recall"] if validation else None,
                "caveat": validation["caveat"] if validation else None,
                "reference": "ESA/WorldCover/v200, six Indian districts" if validation else None,
                "recommended": bool(
                    validation and validation["reliability"] in ("good", "moderate")
                ),
            }
        )

    return sorted(
        entries,
        key=lambda e: (RELIABILITY_ORDER.get(e["reliability"], 9), -(e["iou"] or 0)),
    )
