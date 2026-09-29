"""
Turn a flood mask into discrete, located, ranked zones.

"486 km2 of Kerala is flooded" is not actionable. "Seven zones, the largest
142 km2 centred near Chengannur" is. Same measurement, usable answer.

Done entirely inside Earth Engine with connectedComponents + reduceToVectors,
so no raster is downloaded. Clustering the pixels locally would mean pulling a
state-sized array over the network on every request.

Connected components, not DBSCAN: a flood zone is a contiguous body of water.
DBSCAN's eps radius would merge distinct patches that happen to be near each
other, which would misstate the geography.
"""

import ee

# Ignore specks. At 20 m a single pixel is 0.0004 km2, and a mask always carries
# scattered noise pixels that are not "zones" in any useful sense.
MIN_ZONE_AREA_KM2 = 0.5

# Above this share of the region, "zones" stop being informative: a mask
# covering 96% of a district vectorises into one blob the size of the district.
# Bengaluru built-up returned exactly that - a single 2,090 km2 zone - which
# tells a reader nothing they did not already have from the total.
MAX_COVERAGE_FOR_ZONES = 0.5

# Vectorising a very large mask exhausts Earth Engine's per-request memory.
# Punjab crop stress (42,923 km2) failed with "User memory limit exceeded".
# Coarsen the vectorisation scale as the mask grows.
SCALE_BY_AREA = [
    (50_000, 1000),
    (20_000, 500),
    (5_000, 300),
]


def scale_for_area(area_km2, requested_scale):
    """Vectorisation scale that will not blow the memory limit."""
    for threshold, scale in SCALE_BY_AREA:
        if area_km2 >= threshold:
            return max(requested_scale, scale)
    return requested_scale

# NOTE: an earlier version ran connectedComponents(maxSize=256) before
# vectorising. That was wrong twice over. reduceToVectors already merges
# connected same-valued pixels, so the step was redundant; and the 256-pixel cap
# silently shredded large flood bodies into ~10 km2 fragments, which made the
# largest zone look small and the zone count look large.


def extract(
    flood_mask,
    region,
    scale=100,
    max_zones=25,
    min_area_km2=MIN_ZONE_AREA_KM2,
    severity_thresholds=(50.0, 10.0),
    simplify_m=None,
    area_km2=None,
    region_area_km2=None,
):
    """Ranked flood zones from a binary mask.

    Args:
        flood_mask: ee.Image, 1 where flooded.
        region: ee.Geometry to constrain the search.
        scale: metres per pixel for vectorisation. Coarser is much cheaper and
            the zone geography survives it; 100 m is a reasonable default.
        max_zones: cap on returned zones, largest first.
        min_area_km2: discard anything smaller.
        severity_thresholds: (high, moderate) area cut-offs in km2.

    Returns a list of zone dicts matching the contract, largest first.
    """
    # Refuse rather than return something useless or blow the memory limit.
    if area_km2 is not None and region_area_km2:
        coverage = area_km2 / region_area_km2
        if coverage > MAX_COVERAGE_FOR_ZONES:
            return {
                "zones": [],
                "total_count": 0,
                "listed_count": 0,
                "truncated": False,
                "total_area_km2": 0.0,
                "listed_area_km2": 0.0,
                "min_area_km2": min_area_km2,
                "skipped": True,
                "skipped_reason": (
                    f"The detected surface covers {coverage * 100:.0f}% of the "
                    "region, so it forms one continuous area rather than "
                    "distinct zones. Use the shaded overlay instead."
                ),
            }

    if area_km2 is not None:
        scale = scale_for_area(area_km2, scale)

    # One polygon per connected run of flooded pixels. No size cap.
    vectors = flood_mask.selfMask().reduceToVectors(
        geometry=region,
        scale=scale,
        geometryType="polygon",
        eightConnected=True,
        labelProperty="flooded",
        maxPixels=1_000_000_000,
        bestEffort=True,
    )

    # Area must come from the masked PIXELS inside each polygon, never from the
    # polygon outline. An outline traced around a patchy region encloses the
    # gaps too, which made single zones come out larger than the total measured
    # area they were part of.
    measured = ee.Image.pixelArea().updateMask(flood_mask).reduceRegions(
        collection=vectors,
        reducer=ee.Reducer.sum(),
        scale=scale,
    )

    def annotate(feature):
        centroid = feature.geometry().centroid(maxError=scale).coordinates()
        return feature.set(
            {
                "area_km2": ee.Number(feature.get("sum")).divide(1_000_000),
                "lon": centroid.get(0),
                "lat": centroid.get(1),
            }
        )

    significant = measured.map(annotate).filter(
        ee.Filter.gte("area_km2", min_area_km2)
    )

    if simplify_m:
        # Raw boundaries follow pixel edges, so a 25-zone response can carry
        # tens of thousands of coordinate pairs and stall the map. Simplifying
        # at the vectorisation scale barely helps - the staircase is exactly
        # that size - so the tolerance is several pixels wide. Areas are
        # computed BEFORE this, from the real pixels, so no number changes.
        tolerance = simplify_m * 3
        significant = significant.map(
            lambda f: f.setGeometry(f.geometry().simplify(tolerance))
        )

    # Scalars in one call. An earlier version put the FeatureCollection inside
    # the same ee.Dictionary; getInfo() then returned a structure this code read
    # as empty, and zones silently vanished with no error.
    totals = ee.Dictionary({
        "count": significant.size(),
        "area": significant.aggregate_sum("area_km2"),
    }).getInfo()

    total_count = int(totals.get("count") or 0)
    total_area = float(totals.get("area") or 0.0)

    # The listed subset as its own request.
    listed = significant.sort("area_km2", False).limit(max_zones)
    features = listed.getInfo().get("features", [])

    if total_count > 0 and not features:
        raise RuntimeError(
            f"Vectorisation found {total_count} regions but returned no "
            "features. This usually means the geometry or scale is rejecting "
            "them - do not treat it as 'no flooding'."
        )

    high, moderate = severity_thresholds
    zones = []
    for index, feature in enumerate(features, start=1):
        props = feature.get("properties", {})
        area = props.get("area_km2") or 0.0

        if area >= high:
            severity = "high"
        elif area >= moderate:
            severity = "moderate"
        else:
            severity = "low"

        bounds = None
        geometry = feature.get("geometry") or {}
        coords = _flatten(geometry.get("coordinates", []))
        if coords:
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            bounds = [min(xs), min(ys), max(xs), max(ys)]

        zones.append(
            {
                "id": f"Z{index}",
                "rank": index,
                "area_km2": round(area, 2),
                "centroid": [
                    round(props.get("lon", 0.0), 4),
                    round(props.get("lat", 0.0), 4),
                ],
                "bbox": [round(v, 4) for v in bounds] if bounds else None,
                "severity": severity,
                # The outline, so the map can highlight the shape rather than
                # drop a pin at its middle.
                "geometry": geometry or None,
            }
        )

    return {
        "zones": zones,
        "total_count": total_count,
        "listed_count": len(zones),
        "truncated": total_count > len(zones),
        "total_area_km2": round(total_area, 2),
        "listed_area_km2": round(sum(z["area_km2"] for z in zones), 2),
        "min_area_km2": min_area_km2,
        "vectorisation_scale_m": scale,
        "skipped": False,
    }


def _flatten(coordinates):
    """All [lon, lat] pairs from nested GeoJSON coordinate arrays."""
    if not coordinates:
        return []
    if isinstance(coordinates[0], (int, float)):
        return [coordinates]
    points = []
    for item in coordinates:
        points.extend(_flatten(item))
    return points


SEVERITY_COLOURS = {
    "high": "#d7301f",
    "moderate": "#fc8d59",
    "low": "#fdcc8a",
}


def _properties(zone):
    return {
        "id": zone["id"],
        "rank": zone["rank"],
        "area_km2": zone["area_km2"],
        "severity": zone["severity"],
        "colour": SEVERITY_COLOURS.get(zone["severity"], "#fdcc8a"),
        "label": f"{zone['id']}: {zone['area_km2']:,.1f} km2",
    }


def to_geojson(zones, geometry_type="polygon"):
    """FeatureCollection for the map.

    geometry_type:
        "polygon"  outlines, so a region is highlighted on the basemap
        "point"    centroids, for numbered markers
        "both"     one collection containing each zone twice, tagged by `kind`

    Polygons are what make the map useful: a pin at 9.51 N tells a user nothing,
    an outline over Kuttanad tells them which villages are affected.
    """
    features = []

    for zone in zones:
        if geometry_type in ("polygon", "both") and zone.get("geometry"):
            features.append(
                {
                    "type": "Feature",
                    "geometry": zone["geometry"],
                    "properties": {**_properties(zone), "kind": "outline"},
                }
            )
        if geometry_type in ("point", "both"):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": zone["centroid"],
                    },
                    "properties": {**_properties(zone), "kind": "marker"},
                }
            )

    return {"type": "FeatureCollection", "features": features}


def summarise(result):
    """Figures the evidence record and the report generator need.

    Takes the dict returned by extract(), not a bare list, so the true count is
    never confused with the number we chose to list.
    """
    zones = result.get("zones") or []
    if not zones:
        return {
            "count": 0,
            "largest_area_km2": 0.0,
            "total_area_km2": 0.0,
            "high_severity_count": 0,
            "truncated": False,
        }

    return {
        "count": result["total_count"],
        "listed": result["listed_count"],
        "truncated": result["truncated"],
        "largest_area_km2": max(z["area_km2"] for z in zones),
        "total_area_km2": result["total_area_km2"],
        "listed_area_km2": result["listed_area_km2"],
        "high_severity_count": sum(1 for z in zones if z["severity"] == "high"),
    }
