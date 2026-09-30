"""
Analysis service layer.

Deliberately free of HTTP concerns so the evaluation harness can import and call
it directly. main.py is a thin adapter over this; nothing here knows what a
request or a response is.

Produces the shape defined in contracts/analysis_response.json.
"""

import os

import ee

from core import earth_engine
from geo import mapping
from geo import regions
from detection import optical
from detection import sar
from detection import surface
from geo import zones as zone_extraction
from core.evidence import EvidenceBuilder, Observation, build_provenance, coverage_warning, utc_now

PIPELINE_VERSION = "0.5.0"

GSW = "JRC/GSW1_4/GlobalSurfaceWater"
PERMANENT_WATER_OCCURRENCE = 90

# Above this mean cloud fraction, optical is not worth attempting.
OPTICAL_CLOUD_CEILING = 0.4


def _initialize():
    """Kept as a name the rest of this module already calls."""
    earth_engine.initialize()


def resolve_geometry(slug_or_name, level="level1"):
    """Region geometry and metadata from FAO GAUL 2015.

    Returns (geometry, meta), or (None, None) when the name is not in the
    dataset. Raises regions.RegionAmbiguous when the name belongs to more than
    one place - it never picks one.

    Three things this used to get wrong, all of them silent:

      - It filtered with `name.title()`, which turns "Jammu and Kashmir" into
        "Jammu And Kashmir" and matches nothing.
      - It knew only GAUL's own vocabulary, so "Odisha" (renamed from Orissa in
        2011) and "Puducherry" (2006) failed outright.
      - On a multi-feature match it called collection.geometry(), which returns
        the UNION. India reuses district names across states, so a query for
        Aurangabad produced a geometry spanning Maharashtra and Bihar - two
        districts a thousand kilometres apart - reported under one name, with
        the state taken from whichever feature came first.
    """
    _initialize()

    try:
        entry = regions.lookup(slug_or_name)
    except regions.RegionNotFound:
        return None, None

    collection = (
        ee.FeatureCollection(entry["dataset"])
        .filter(ee.Filter.eq("ADM0_NAME", "India"))
        .filter(ee.Filter.eq(entry["field"], entry["name"]))
    )

    meta = {
        "slug": entry["name"].lower().replace(" ", "-"),
        "name": entry["name"],
        "admin_level": entry["level"],
        "state": entry["state"],
        "boundary_source": entry["dataset"],
        "boundary_vintage": regions.BOUNDARY_VINTAGE,
    }

    # What the caller asked for, when GAUL files it under an older name. The
    # response says "Orissa" because that is the boundary actually measured;
    # this records that the question said Odisha.
    asked = (slug_or_name or "").replace("-", " ").strip()
    if regions._normalize(asked) != regions._normalize(entry["name"]):
        meta["requested_as"] = asked
        meta["name_note"] = (
            f"Requested as {asked!r}; {regions.BOUNDARY_SOURCE} files this "
            f"boundary under its earlier name {entry['name']!r}."
        )

    return collection.geometry(), meta


def summary_min(zone_result):
    return zone_result.get("min_area_km2", zone_extraction.MIN_ZONE_AREA_KM2)


def permanent_water_mask(region):
    return (
        ee.Image(GSW)
        .select("occurrence")
        .unmask(0)
        .gte(PERMANENT_WATER_OCCURRENCE)
        .clip(region)
        .rename("permanent_water")
    )


def area_km2(mask, region, scale=100):
    """Area of the 1-valued pixels of `mask`, in km2."""
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


def cloud_fraction(region, start_date, end_date):
    """Mean cloud cover of available Sentinel-2 scenes, 0-1.

    Used for sensor routing. Cheap: reads scene metadata, no pixels.
    """
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start_date, end_date)
    )
    size = collection.size().getInfo()
    if size == 0:
        return None, 0
    mean = collection.aggregate_mean("CLOUDY_PIXEL_PERCENTAGE").getInfo()
    return (mean or 0) / 100.0, size


def choose_sensor(region, start_date, end_date, force=None):
    """Decide optical vs SAR, and record why.

    The decision goes into the evidence record. A result that silently used a
    degraded sensor is worse than one that says so.
    """
    if force:
        return force, f"forced_by_request:{force}", None, 0

    fraction, available = cloud_fraction(region, start_date, end_date)

    if available == 0:
        return "sentinel-1", "no_optical_scenes_available", fraction, available
    if fraction is not None and fraction > OPTICAL_CLOUD_CEILING:
        return (
            "sentinel-1",
            "cloud_fraction_exceeded_optical_threshold",
            fraction,
            available,
        )
    return "sentinel-2", "optical_conditions_acceptable", fraction, available


SENSORS = ("sentinel-1", "sentinel-2")

# "threshold" is the validated darkness rule that ships. "change" compares
# against a dry baseline and is unvalidated until notebook 06 has run.
METHODS = ("threshold", "change")


def _detect_sar(region, post_start, post_end, scale, method="threshold",
                pre_start=None, pre_end=None):
    """The post-event SAR detection, and how to repeat it for a baseline.

    Returns a dict the shared flood assembly reads. Everything sensor-specific
    lives in here or in _detect_optical; everything after them is identical,
    which is what makes the two results comparable.

    method="change" swaps the darkness rule for change detection against a
    dry baseline (see sar.change_mask). It needs the pre-event window, and it
    is unvalidated - the result says so.
    """
    collection = sar.get_collection(region, post_start, post_end)
    if collection.size().getInfo() == 0:
        raise sar.NoSarImagery(post_start, post_end)

    # Same relative orbit for both windows, or the difference is viewing
    # geometry rather than water.
    orbit = sar.dominant_orbit(collection).getInfo()

    post_mask, post_composite, post_info = sar.detect_water(
        region, post_start, post_end, relative_orbit=orbit, scale=scale
    )

    notes = []
    method_text = (
        f"Sentinel-1 {post_info['polarisation']} at "
        f"{post_info['threshold']} dB ({post_info['threshold_source']}), "
        "permanent water excluded"
    )
    validation = post_info.get("validation")

    if method == "change":
        # The darkness mask is replaced, not added to afterwards: change_mask
        # already contains the darkness rule as its first branch, so every
        # pixel the threshold method finds, this finds too.
        pre_composite = sar.baseline_composite(
            region, pre_start, pre_end, relative_orbit=orbit,
            polarisations=tuple(post_info["polarisation"].split("+")),
        )
        post_mask = sar.change_mask(post_composite, pre_composite)
        method_text = (
            f"Sentinel-1 {post_info['polarisation']} change detection: below "
            f"{sar.CHANGE_DARK_DB} dB, or more than {abs(sar.CHANGE_DROP_DB):g} dB "
            f"darker than the {pre_start} to {pre_end} median and below "
            f"{sar.CHANGE_CEILING_DB} dB; permanent water excluded"
        )
        # The darkness rule's scores do not describe this rule. Borrowing them
        # would be the misattribution the whole evidence record exists to stop.
        validation = sar.CHANGE_VALIDATION
        notes.append(sar.CHANGE_VALIDATION_CAVEAT if validation
                     else sar.CHANGE_NOT_VALIDATED_NOTE)

    if post_info["threshold_source"] == "otsu_clamped":
        notes.append(
            f"The adaptive threshold was overruled by a physical bound "
            f"(Otsu returned {post_info['threshold_raw']} dB, clamped to "
            f"{post_info['threshold']} dB). This usually means the scene lacked "
            "a clear land/water separation, so the extent is less reliable."
        )
    if post_info.get("degraded"):
        # sar.detect_water carried this reason out precisely so it would be
        # reported. It never was: the numbers switched to the weaker rule's
        # while nothing in the result said the rule had changed.
        notes.append(f"Weaker method used: {post_info['degraded']}.")

    def detect_pre(pre_start, pre_end):
        # Same threshold for both windows. Letting Otsu re-fit on the dry
        # baseline is wrong twice over: it assumes a bimodal histogram that a
        # dry scene does not have, and any difference it produces mixes real
        # change with threshold drift.
        pre_mask, _, pre_info = sar.detect_water(
            region, pre_start, pre_end, relative_orbit=orbit, scale=scale,
            threshold_db=post_info["threshold"],
        )
        method = (
            f"Sentinel-1 {pre_info['polarisation']} at the same "
            f"{pre_info['threshold']} dB threshold and relative orbit as the "
            "post-event window"
        )
        return pre_mask, None, method

    return {
        "sensor": "sentinel-1",
        "mask": post_mask,
        # MEASURE the footprint, never assume it. Sentinel-1's swath is ~250 km
        # and we restrict to one relative orbit for geometric consistency, so a
        # single track frequently does not cover a whole state.
        "valid": post_composite.mask(),
        # SAR's water mask is already masked where the composite has no data,
        # so it is not ANDed with `valid` again - kept exactly as it was.
        "restrict_to_valid": False,
        "scene_count": post_info["scene_count"],
        "scenes_available": post_info["scene_count"],
        "method": method_text,
        "threshold": post_info["threshold"],
        "threshold_unit": "dB",
        "source": f"{sar.S1_COLLECTION} IW {post_info['polarisation']}",
        "validation": validation,
        "notes": notes,
        "detect_pre": detect_pre,
        "no_pre_imagery": (sar.NoSarImagery,),
        # Same orbit means the same footprint, so the baseline is measured over
        # the whole region as it always has been.
        "baseline_common_area": False,
        "dataset": sar.S1_COLLECTION,
        "known_confusions": list(sar.KNOWN_CONFUSIONS),
        "reliability": "moderate" if validation else "unvalidated",
        "internal": {"composite": post_composite, "orbit": orbit},
    }


def _detect_optical(region, post_start, post_end, cloud_limit):
    """The post-event optical detection, in the same shape as _detect_sar."""
    post_mask, post_valid, post_image, post_info = optical.detect_water(
        region, post_start, post_end, cloud_limit=cloud_limit
    )

    notes = []
    if post_info.get("validation"):
        # Appended after the shared accuracy note, which states the scores;
        # this says why those scores are not a like-for-like win over radar.
        notes.append(optical.VALIDATION_CAVEAT)
    else:
        notes.append(optical.NOT_VALIDATED_NOTE)

    def detect_pre(pre_start, pre_end):
        # Same index, same threshold, same cloud filter. A different threshold
        # on the baseline would put a change of method into the "change".
        pre_mask, pre_valid, _, pre_info = optical.detect_water(
            region, pre_start, pre_end, cloud_limit=cloud_limit,
            threshold=post_info["threshold"],
        )
        method = (
            f"Sentinel-2 {pre_info['index'].upper()} above the same "
            f"{pre_info['threshold']} threshold as the post-event window"
        )
        return pre_mask, pre_valid, method

    return {
        "sensor": "sentinel-2",
        "mask": post_mask,
        "valid": post_valid,
        "restrict_to_valid": True,
        "scene_count": post_info["scene_count"],
        "scenes_available": post_info["scenes_available"],
        "method": optical.method_text(post_info),
        "threshold": post_info["threshold"],
        "threshold_unit": post_info["threshold_unit"],
        "source": f"{optical.S2_COLLECTION} {post_info['index'].upper()}",
        "validation": post_info.get("validation"),
        "notes": notes,
        "detect_pre": detect_pre,
        "no_pre_imagery": (surface.NoOpticalImagery,),
        # Cloud moves between windows. Comparing water seen through one set of
        # gaps against water seen through another would report the cloud
        # moving as water appearing - so the baseline is measured only where
        # BOTH windows were clear.
        "baseline_common_area": True,
        "dataset": optical.S2_COLLECTION,
        "known_confusions": list(optical.KNOWN_CONFUSIONS),
        "reliability": "moderate" if post_info.get("validation") else "unvalidated",
        # No backscatter basemap: the composite is reflectance, not dB.
        "internal": {"composite": None, "true_colour": post_image, "orbit": None},
    }


def analyse_flood(
    region_geometry,
    region_meta,
    post_start,
    post_end,
    pre_start=None,
    pre_end=None,
    force_sensor=None,
    scale=100,
    cloud_limit=optical.DEFAULT_CLOUD_LIMIT,
    method="threshold",
):
    """Flood extent for a region and window, optionally against a baseline.

    Either sensor, one evidence shape. Sentinel-1 and Sentinel-2 differ in
    how they find water and in what they cannot see; after detection they go
    through exactly the same arithmetic, so two results for the same place and
    window differ only by sensor. That is the property a SAR-versus-optical
    comparison needs, and why the two are not separate functions.

    Returns a dict matching the analysis response contract.
    """
    _initialize()

    if force_sensor is not None and force_sensor not in SENSORS:
        raise ValueError(
            f"Unknown sensor {force_sensor!r}. Use one of {', '.join(SENSORS)}, "
            "or leave it empty to let cloud cover decide."
        )

    if method not in METHODS:
        raise ValueError(
            f"Unknown method {method!r}. Use one of {', '.join(METHODS)}."
        )
    if method == "change":
        if force_sensor != "sentinel-1":
            raise ValueError(
                "Change detection is a radar method: it compares backscatter "
                "against a dry baseline. Set sensor to 'sentinel-1'."
            )
        if not (pre_start and pre_end):
            raise ValueError(
                "Change detection needs a pre-event baseline window "
                "(pre_start and pre_end) to compare against."
            )

    region = region_geometry
    region_area = region.area(maxError=100).getInfo() / 1_000_000

    sensor, reason, cloud, optical_scenes = choose_sensor(
        region, post_start, post_end, force_sensor
    )

    if sensor == "sentinel-2":
        try:
            detection = _detect_optical(region, post_start, post_end, cloud_limit)
        except surface.NoOpticalImagery:
            if force_sensor:
                raise
            # Routed to optical on scene-level cloud statistics, then found no
            # scene clear enough to composite. Radar sees through cloud; use it,
            # and record that this is why.
            sensor, reason = "sentinel-1", "no_cloud_free_optical_scenes"
            detection = _detect_sar(region, post_start, post_end, scale)
    else:
        detection = _detect_sar(region, post_start, post_end, scale,
                                method=method, pre_start=pre_start, pre_end=pre_end)

    builder = EvidenceBuilder()
    permanent = permanent_water_mask(region)

    valid = detection["valid"]
    observable_area = area_km2(valid, region, scale)

    observation = Observation(
        sensor_used=sensor,
        sensor_reason=reason,
        sensors_considered=["sentinel-2", "sentinel-1"],
        observable_area_km2=round(observable_area, 1),
        region_area_km2=region_area,
        scenes_available=detection["scenes_available"],
        scenes_used=detection["scene_count"],
        cloud_fraction=round(cloud, 3) if cloud is not None else None,
        relative_orbit=detection["internal"].get("orbit"),
    )

    flood_mask = detection["mask"].And(permanent.Not())
    if detection["restrict_to_valid"]:
        flood_mask = flood_mask.And(valid)
    flood_mask = flood_mask.rename("flood_mask")
    flood_area = area_km2(flood_mask, region, scale)
    permanent_area = area_km2(permanent, region, scale)

    validation = detection["validation"]
    e_extent = builder.add(
        "flood_extent",
        round(flood_area, 1),
        "km2",
        method=detection["method"],
        threshold=detection["threshold"],
        threshold_unit=detection["threshold_unit"],
        confidence=validation["precision"] if validation else None,
        spatial_support_km2=round(observable_area, 1),
        source=detection["source"],
    )

    if validation:
        # Derived, never written out in words. The previous version said
        # "around half of true flooding is expected to be missed", which was
        # true at recall 0.511 and false the moment recall moved to 0.574 - a
        # stale accuracy claim in a string users actually read. Prose that
        # restates a number goes out of date silently; arithmetic does not.
        false_positives = 1 - validation["precision"]
        missed = 1 - validation["recall"]
        builder.note(
            f"This detection method scores IoU {validation['iou']}, precision "
            f"{validation['precision']} and recall {validation['recall']} on "
            f"{validation['dataset']}. About {false_positives:.0%} of detected "
            f"pixels are expected to be false positives, and about {missed:.0%} "
            "of true flooding is expected to be missed."
        )

    builder.add(
        "flood_extent_fraction",
        round(flood_area / observable_area * 100, 2) if observable_area else 0.0,
        "percent",
        derived_from=[e_extent],
        denominator="observable_area_km2",
        note="Percentage is of observable area, not total region area.",
    )

    builder.add(
        "permanent_water_area",
        round(permanent_area, 1),
        "km2",
        method=f"{GSW} occurrence >= {PERMANENT_WATER_OCCURRENCE}, excluded from flood extent",
        source=GSW,
    )

    for note in detection["notes"]:
        builder.note(note)

    # Where, not just how much.
    zone_result = {"zones": []}
    if flood_area > 0:
        try:
            # simplify_m smooths outlines for the map only; areas are computed
            # from the real pixels before simplification, so no number changes.
            zone_result = zone_extraction.extract(
                flood_mask,
                region,
                scale=scale,
                simplify_m=scale,
                area_km2=flood_area,
                region_area_km2=observable_area,
            )
            if zone_result.get("skipped"):
                builder.note(zone_result["skipped_reason"])
        except Exception as exc:
            builder.note(f"Zone extraction failed, extent is unaffected: {exc}")

    flood_zones = zone_result.get("zones") or []

    # 486 km2 of flooding that resolves into zero zones is a bug, not a finding.
    # Say so rather than quietly omitting the zone evidence.
    if flood_area > 0 and not flood_zones:
        builder.note(
            f"Zone extraction returned no regions despite {flood_area:.1f} km2 "
            "of detected flooding. Location detail is unavailable for this "
            "result; the extent figure is unaffected."
        )

    if flood_zones:
        summary = zone_extraction.summarise(zone_result)

        e_zones = builder.add(
            "zone_count",
            summary["count"],
            "zones",
            method=(
                f"Connected flooded regions vectorised at {scale} m; "
                f"regions below {summary_min(zone_result)} km2 excluded as noise"
            ),
            derived_from=[e_extent],
            note=(
                f"{summary['listed']} largest are listed individually"
                if summary["truncated"]
                else None
            ),
        )
        builder.add(
            "largest_zone_area",
            summary["largest_area_km2"],
            "km2",
            derived_from=[e_zones],
            note=(
                f"Centred near {flood_zones[0]['centroid'][1]:.2f} N, "
                f"{flood_zones[0]['centroid'][0]:.2f} E"
            ),
        )

        # How much of the extent is contiguous enough to be a zone at all.
        if flood_area:
            builder.add(
                "zoned_fraction",
                round(summary["total_area_km2"] / flood_area * 100, 1),
                "percent",
                derived_from=[e_extent, e_zones],
                note=(
                    "Remainder is scattered pixels below the minimum zone size, "
                    "which may indicate dispersed wet ground rather than "
                    "contiguous flooding."
                ),
            )

    baseline_mask = None
    if pre_start and pre_end:
        try:
            pre_mask, pre_valid, pre_method = detection["detect_pre"](pre_start, pre_end)
            baseline_mask = pre_mask.And(permanent.Not())

            if detection["baseline_common_area"]:
                both = valid.And(pre_valid)
                baseline_mask = baseline_mask.And(both)
                baseline_area = area_km2(baseline_mask, region, scale)
                common_area = area_km2(both, region, scale)
                post_in_common = area_km2(flood_mask.And(both), region, scale)

                # A separate record rather than reusing E1, because E1 is over
                # everything the post window saw and the baseline is not. If
                # net_new_water were E1 minus the baseline, a reader checking
                # the arithmetic would find it does not add up - and would be
                # right, because the two figures cover different ground.
                e_post = builder.add(
                    "flood_extent_in_common_area",
                    round(post_in_common, 1),
                    "km2",
                    derived_from=[e_extent],
                    spatial_support_km2=round(common_area, 1),
                    method="flood extent restricted to ground clear in both windows",
                )
                e_baseline = builder.add(
                    "baseline_water_extent",
                    round(baseline_area, 1),
                    "km2",
                    period_ref="pre",
                    method=pre_method,
                    spatial_support_km2=round(common_area, 1),
                    note="Measured only where both windows were cloud-free.",
                )
                builder.add(
                    "net_new_water",
                    round(post_in_common - baseline_area, 1),
                    "km2",
                    derived_from=[e_post, e_baseline],
                    method="post minus pre water extent, over ground clear in both windows",
                )
                if observable_area and common_area < observable_area * 0.9:
                    builder.note(
                        f"The before-and-after comparison covers "
                        f"{common_area:.1f} km2, the ground clear of cloud in "
                        f"both windows, against {observable_area:.1f} km2 seen "
                        "after the event. Change outside it is not measured."
                    )
            else:
                baseline_area = area_km2(baseline_mask, region, scale)
                e_baseline = builder.add(
                    "baseline_water_extent",
                    round(baseline_area, 1),
                    "km2",
                    period_ref="pre",
                    method=pre_method,
                )
                builder.add(
                    "net_new_water",
                    round(flood_area - baseline_area, 1),
                    "km2",
                    derived_from=[e_extent, e_baseline],
                    method="post minus pre water extent",
                )
        except detection["no_pre_imagery"] as exc:
            baseline_mask = None
            builder.note(f"Baseline could not be computed: {exc}")

    warning = coverage_warning(observation)
    if warning:
        builder.note(warning)

    return {
        "schema_version": "1.0",
        "generated_at": utc_now(),
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
            for zone in flood_zones
        ],
        "zones_summary": zone_extraction.summarise(zone_result) if flood_zones else None,
        "zones_geojson": zone_extraction.to_geojson(flood_zones, "both"),
        "map": mapping.display_hints(
            "flood_extent",
            is_change=bool(pre_start),
            zone_count=len(flood_zones),
            reliability=detection["reliability"],
        ),
        "provenance": build_provenance(
            PIPELINE_VERSION,
            datasets=[
                {"id": detection["dataset"], "role": "primary imagery"},
                {"id": GSW, "role": "permanent water mask"},
                {
                    "id": region_meta.get("boundary_source", "unknown"),
                    "role": "administrative boundary",
                },
            ],
            stats_scale_m=scale,
            known_confusions=detection["known_confusions"]
            + [
                "Boundary vintage is 2015; districts created after that date are "
                "not resolvable."
            ],
        ),
        "_internal": {
            "flood_mask": flood_mask,
            # The pre-event water mask, at the SAME threshold (and for SAR the
            # same relative orbit). Kept so the frontend can draw a
            # before/after swipe: any difference the user sees is water, not
            # threshold drift or a change of viewing geometry. None in
            # single-window mode, and None when the baseline had no imagery.
            "baseline_mask": baseline_mask,
            "region": region,
            **detection["internal"],
        },
    }


FLOOD_PALETTE = "00b7ff"

# Deliberately a different hue AND a different brightness, not a lighter
# version of the same blue. The swipe has to stay readable at a glance and for
# anyone who cannot reliably separate two shades of blue, so the baseline is a
# dark slate against the flood's bright cyan - about 75 points of perceived
# luminance apart, which test_swipe_layers.py holds it to.
BASELINE_PALETTE = "2c4250"


def tile_urls(payload):
    """Earth Engine tile templates for the map overlays.

    Returned instead of rendered PNGs so the frontend map can zoom and pan. The
    URLs are signed and expire, so they are generated per response rather than
    cached.

    Keys:
        flood_tiles        water detected in the post-event window
        baseline_tiles     water in the pre-event window, comparison mode only
        backscatter_tiles  the raw radar composite, as a greyscale basemap
    """
    internal = payload.get("_internal") or {}
    mask = internal.get("flood_mask")
    baseline = internal.get("baseline_mask")
    composite = internal.get("composite")
    if mask is None:
        return None

    def tiles(image):
        return ee.Image(image).getMapId({})["tile_fetcher"].url_format

    try:
        urls = {
            "flood_tiles": tiles(mask.selfMask().visualize(palette=[FLOOD_PALETTE]))
        }

        if baseline is not None:
            urls["baseline_tiles"] = tiles(
                baseline.selfMask().visualize(palette=[BASELINE_PALETTE])
            )

        if composite is not None:
            urls["backscatter_tiles"] = tiles(
                composite.visualize(min=-25, max=0, palette=["000000", "ffffff"])
            )

        # Optical results have no backscatter; a dB stretch on reflectance
        # would draw a white rectangle. Show what the camera saw instead, which
        # also shows the reader where the cloud was.
        true_colour = internal.get("true_colour")
        if true_colour is not None:
            urls["true_colour_tiles"] = tiles(
                true_colour.visualize(bands=["B4", "B3", "B2"], min=0, max=3000)
            )

        return urls
    except Exception:
        # A missing basemap must not fail an otherwise valid analysis.
        return None
