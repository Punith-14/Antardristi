"""
Zone helpers and map display logic.

`zones.extract` needs Earth Engine and is covered in the integration tests. The
pure functions around it are covered here.
"""

import pytest

from geo import zones as z
from geo.mapping import CONTINUOUS, DISCRETE, display_hints


ZONES = [
    {"id": "Z1", "rank": 1, "area_km2": 42.1, "centroid": [86.42, 20.51],
     "severity": "high", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}},
    {"id": "Z2", "rank": 2, "area_km2": 12.0, "centroid": [86.78, 20.38],
     "severity": "moderate", "geometry": {"type": "Polygon", "coordinates": [[[2, 2], [3, 2], [3, 3], [2, 2]]]}},
]


# ------------------------------------------------------------ scale guard

def test_large_masks_get_a_coarser_scale():
    """REGRESSION: Punjab crop stress covered 42,923 km2 and vectorisation died
    with 'User memory limit exceeded'."""
    assert z.scale_for_area(100, 100) == 100
    assert z.scale_for_area(8_000, 100) == 300
    assert z.scale_for_area(25_000, 100) == 500
    assert z.scale_for_area(60_000, 100) == 1000


def test_a_coarser_requested_scale_is_never_reduced():
    assert z.scale_for_area(100, 800) == 800


# ------------------------------------------------------------- geojson

def test_geojson_carries_polygons_for_the_map():
    """REGRESSION: only centroids were emitted, so the map could drop a pin but
    could not highlight the shape of an affected area."""
    collection = z.to_geojson(ZONES, "polygon")
    assert all(f["geometry"]["type"] == "Polygon" for f in collection["features"])
    assert len(collection["features"]) == 2


def test_geojson_both_emits_outline_and_marker_per_zone():
    collection = z.to_geojson(ZONES, "both")
    kinds = [f["properties"]["kind"] for f in collection["features"]]
    assert kinds.count("outline") == 2
    assert kinds.count("marker") == 2


def test_every_feature_carries_what_the_map_needs():
    for feature in z.to_geojson(ZONES, "both")["features"]:
        props = feature["properties"]
        assert {"id", "rank", "area_km2", "severity", "colour", "label"} <= set(props)


def test_severity_colours_are_distinct():
    assert len(set(z.SEVERITY_COLOURS.values())) == 3


def test_zones_without_geometry_still_produce_markers():
    bare = [{**ZONES[0], "geometry": None}]
    collection = z.to_geojson(bare, "both")
    assert len(collection["features"]) == 1
    assert collection["features"][0]["properties"]["kind"] == "marker"


# ------------------------------------------------------------- summarise

def test_summary_separates_true_count_from_listed_count():
    """REGRESSION: zone_count reported 25, which was max_zones - our own cap
    presented as a measurement."""
    result = {
        "zones": ZONES,
        "total_count": 154,
        "listed_count": 2,
        "truncated": True,
        "total_area_km2": 319.0,
        "listed_area_km2": 54.1,
    }
    summary = z.summarise(result)
    assert summary["count"] == 154
    assert summary["listed"] == 2
    assert summary["truncated"]


def test_largest_zone_is_the_maximum_not_the_first():
    result = {
        "zones": list(reversed(ZONES)),
        "total_count": 2,
        "listed_count": 2,
        "truncated": False,
        "total_area_km2": 54.1,
        "listed_area_km2": 54.1,
    }
    assert z.summarise(result)["largest_area_km2"] == 42.1


def test_empty_summary_is_safe():
    summary = z.summarise({"zones": []})
    assert summary["count"] == 0
    assert summary["largest_area_km2"] == 0.0


def test_flatten_handles_nested_coordinates():
    nested = [[[[0, 1], [2, 3]]], [[[4, 5]]]]
    assert z._flatten(nested) == [[0, 1], [2, 3], [4, 5]]


# --------------------------------------------------------- display hints

def test_discrete_things_are_outlined():
    hints = display_hints("flood_extent", zone_count=25, reliability="moderate")
    assert hints["primary_layer"] == "zone_polygons"


def test_continuous_things_are_shaded():
    """25 outlined polygons across a state that is 98% vegetated says nothing."""
    hints = display_hints("vegetation_health", zone_count=21, reliability="good")
    assert hints["primary_layer"] == "raster_overlay"


def test_change_is_always_discrete():
    """New construction appears in patches even though built-up area does not."""
    single = display_hints("built_up", is_change=False, zone_count=1, reliability="poor")
    change = display_hints("built_up", is_change=True, zone_count=4, reliability="poor")

    assert single["primary_layer"] == "raster_overlay"
    assert change["primary_layer"] == "zone_polygons"


def test_no_zones_means_no_outlines():
    hints = display_hints("flood_extent", zone_count=0, reliability="moderate")
    assert hints["primary_layer"] == "raster_overlay"


@pytest.mark.parametrize(
    "reliability,style",
    [("good", "solid"), ("moderate", "translucent"), ("poor", "hatched")],
)
def test_confidence_drives_how_boldly_a_result_is_drawn(reliability, style):
    """A method scoring IoU 0.057 must not look as certain as one scoring
    0.888. The map inherits the honesty rather than relying on a caption."""
    assert display_hints("flood_extent", zone_count=5, reliability=reliability)[
        "overlay_style"
    ] == style


@pytest.mark.parametrize("reliability", ["unvalidated", None, "", "something-new"])
def test_solid_is_earned_not_defaulted_to(reliability):
    """This was `else: "solid"`, so an unscored method - or a missing
    reliability altogether - was drawn more boldly than the SAR rule measured
    on 441 chips. Anything not known to be good is not drawn as good."""
    hints = display_hints("flood_extent", zone_count=5, reliability=reliability)
    assert hints["overlay_style"] != "solid"
    assert "warning_banner" in hints


def test_unvalidated_is_not_called_unreliable():
    """Nobody measured it, so nobody knows. Saying it is unreliable would be
    as unsupported as saying it is good."""
    banner = display_hints("crop_stress", zone_count=0, reliability="unvalidated")[
        "warning_banner"
    ]
    assert "not been validated" in banner
    assert "unreliable" not in banner


def test_poor_reliability_raises_a_banner():
    hints = display_hints("built_up", zone_count=0, reliability="poor")
    assert "warning_banner" in hints
    assert "not be used for decisions" in hints["warning_banner"]


def test_good_reliability_has_no_banner():
    hints = display_hints("vegetation_health", zone_count=0, reliability="good")
    assert "warning_banner" not in hints


def test_every_analysis_is_classified_as_discrete_or_continuous():
    from detection.surface import ANALYSES

    known = DISCRETE | CONTINUOUS
    for name in ANALYSES:
        assert name in known, f"{name} has no map treatment defined"
