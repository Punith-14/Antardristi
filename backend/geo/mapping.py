"""
How a result should be drawn.

The backend knows things the frontend cannot reasonably work out: whether the
measured thing is discrete or continuous, whether this is a change query, and
how much to trust the numbers. Those decisions belong here, not scattered
through React components.

The rule: discrete things get outlined, continuous things get shaded.

Twenty-five outlined polygons across a state that is 80% vegetated communicates
nothing - the correct picture there is a shaded raster. But flooding is a set of
distinct water bodies, and outlining them over a basemap tells a user which
villages are affected. Change is always discrete, whatever the underlying
quantity: new construction appears in patches even though built-up area does not.
"""

# Analyses whose subject is a set of distinct objects.
DISCRETE = {"flood_extent", "water_extent", "crop_stress"}

# Analyses that typically blanket a region.
CONTINUOUS = {"vegetation_health", "green_cover", "bare_ground", "built_up"}

PALETTES = {
    "flood_extent": ["#00b7ff"],
    "water_extent": ["#1f78b4"],
    "vegetation_health": ["#31a354"],
    "green_cover": ["#006d2c"],
    "built_up": ["#d95f0e"],
    "bare_ground": ["#d8b365"],
    "crop_stress": ["#e6550d"],
}


def display_hints(analysis_type, is_change=False, zone_count=0, reliability=None):
    """What the map should lead with, and what to warn about."""
    discrete = analysis_type in DISCRETE or is_change

    if discrete and zone_count:
        primary, secondary = "zone_polygons", "raster_overlay"
        reason = (
            "Change appears in distinct patches, so outlines carry the meaning."
            if is_change
            else "The subject is a set of distinct areas, so outlines carry the meaning."
        )
    else:
        primary, secondary = "raster_overlay", "zone_polygons"
        reason = (
            "The measured surface covers much of the region, so shading reads "
            "better than outlines. Zone polygons are still available but will "
            "be large and numerous."
        )

    hints = {
        "primary_layer": primary,
        "secondary_layer": secondary,
        "reason": reason,
        "palette": PALETTES.get(analysis_type, ["#3182bd"]),
        "basemap": "openstreetmap",
        "show_zone_labels": bool(discrete and zone_count and zone_count <= 30),
        "fit_bounds_to": "zones" if discrete and zone_count else "region",
    }

    # An unreliable result must not be drawn as confidently as a good one.
    #
    # Solid is earned, not defaulted to. This used to be `else: "solid"`, so
    # "unvalidated" - and a missing reliability altogether - got the most
    # confident styling in the system: an unscored crop-stress threshold was
    # drawn more boldly than the SAR flood rule measured on 441 chips.
    if reliability == "good":
        hints["overlay_style"] = "solid"
    elif reliability == "moderate":
        hints["overlay_style"] = "translucent"
    elif reliability == "poor":
        hints["overlay_style"] = "hatched"
        hints["warning_banner"] = (
            "This detection method is unreliable. The overlay is indicative "
            "only and should not be used for decisions."
        )
    else:
        # Unvalidated is not the same as poor - nobody has measured it, so
        # nobody knows - but it is not good either.
        hints["overlay_style"] = "hatched"
        hints["warning_banner"] = (
            "This detection method has not been validated, so how far to trust "
            "the overlay is unknown."
        )

    return hints
