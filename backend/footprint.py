"""
Areas the user defines themselves, rather than named administrative boundaries.

Why this exists: every boundary in FAO GAUL 2015 is fifteen years old, and
measure_boundary_coverage.py puts a number on what that costs - 30 of 30
districts created since 2015 cannot be resolved at all. Ladakh has no boundary
in the dataset. It does have a location, and Earth Engine will happily measure
inside any shape you hand it.

So there are two ways to say where: a name, resolved through GAUL, or a shape,
supplied directly. Both produce the same analysis.

The one rule this module exists to enforce is that the two must never be
confused in the output. A rectangle drawn over Ladakh covers parts of
neighbouring districts and misses corners of Ladakh itself. The flood area
inside it is a real measurement of a real place - it is simply not "flood
extent in Ladakh", and a response that labels it as such would be the same
failure as measuring May against May and reporting it as a comparison. So
metadata() never emits a district name, never cites GAUL, and says in plain
words that the footprint came from the caller.
"""

import math

import ee

import earth_engine

# India plus a margin, in degrees. Not a border - a sanity bound. It catches
# transposed coordinates (a lat/lon swap puts you in the Indian Ocean) and
# typos that would otherwise trigger an expensive Earth Engine reduction over
# the wrong continent.
INDIA_BOUNDS = (67.0, 6.0, 98.5, 37.5)

# Roughly Rajasthan. Large enough for any real question, small enough that a
# misplaced decimal cannot start a reduction over half of Asia.
MAX_AREA_KM2 = 400_000.0

MAX_RADIUS_KM = 300.0
MIN_RADIUS_KM = 0.5

# Below this a bbox is smaller than a few Sentinel-1 pixels and the statistics
# are meaningless.
MIN_SPAN_DEG = 0.001

MAX_POLYGON_POINTS = 500


class InvalidFootprint(ValueError):
    """The supplied shape cannot be analysed, and says why."""


def _number(value, label):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise InvalidFootprint(f"{label} must be a number, got {value!r}") from None


def _check_point(lon, lat, label="point"):
    west, south, east, north = INDIA_BOUNDS
    if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
        raise InvalidFootprint(
            f"{label} ({lon}, {lat}) is not a valid coordinate. "
            "Order is longitude then latitude."
        )
    if not (west <= lon <= east and south <= lat <= north):
        raise InvalidFootprint(
            f"{label} ({lon}, {lat}) is outside India. Coordinates are "
            "longitude, latitude in that order - swapping them lands in the "
            "Indian Ocean."
        )


def _bbox_area_km2(west, south, east, north):
    """Rough area of a lat/lon box, good enough to reject absurd requests.

    A degree of longitude shrinks with latitude, so the width is scaled by the
    cosine of the mid-latitude. Not accurate enough to report; accurate enough
    to tell 400 km2 from 400,000.
    """
    mid = math.radians((south + north) / 2.0)
    height = (north - south) * 111.32
    width = (east - west) * 111.32 * math.cos(mid)
    return abs(height * width)


def from_bbox(bbox):
    """Validate [west, south, east, north] in degrees. Returns the shape."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise InvalidFootprint(
            "bbox must be [west, south, east, north] in degrees, four numbers."
        )

    west, south, east, north = (
        _number(v, name)
        for v, name in zip(bbox, ("west", "south", "east", "north"))
    )

    if west >= east:
        raise InvalidFootprint(f"west ({west}) must be less than east ({east}).")
    if south >= north:
        raise InvalidFootprint(f"south ({south}) must be less than north ({north}).")
    if (east - west) < MIN_SPAN_DEG or (north - south) < MIN_SPAN_DEG:
        raise InvalidFootprint(
            "bbox is smaller than a single satellite pixel. Draw a larger area."
        )

    _check_point(west, south, "bbox south-west corner")
    _check_point(east, north, "bbox north-east corner")

    area = _bbox_area_km2(west, south, east, north)
    if area > MAX_AREA_KM2:
        raise InvalidFootprint(
            f"bbox covers about {area:,.0f} km2, above the {MAX_AREA_KM2:,.0f} "
            "km2 limit. Analyse a smaller area, or name a state instead."
        )

    return {
        "kind": "bbox",
        "bbox": [west, south, east, north],
        "approx_area_km2": round(area, 1),
    }


def from_point(point, radius_km):
    """Validate [longitude, latitude] and a radius in km. Returns the shape."""
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise InvalidFootprint("point must be [longitude, latitude].")

    lon = _number(point[0], "longitude")
    lat = _number(point[1], "latitude")
    radius = _number(radius_km, "radius_km")

    _check_point(lon, lat)

    if not MIN_RADIUS_KM <= radius <= MAX_RADIUS_KM:
        raise InvalidFootprint(
            f"radius_km must be between {MIN_RADIUS_KM} and {MAX_RADIUS_KM}, "
            f"got {radius}."
        )

    return {
        "kind": "circle",
        "centre": [lon, lat],
        "radius_km": radius,
        "approx_area_km2": round(math.pi * radius * radius, 1),
    }


def from_polygon(coordinates):
    """Validate a ring of [longitude, latitude] pairs. Returns the shape."""
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 3:
        raise InvalidFootprint(
            "polygon needs at least three [longitude, latitude] points."
        )
    if len(coordinates) > MAX_POLYGON_POINTS:
        raise InvalidFootprint(
            f"polygon has {len(coordinates)} points, above the "
            f"{MAX_POLYGON_POINTS} limit. Simplify the shape."
        )

    ring = []
    for index, pair in enumerate(coordinates):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise InvalidFootprint(
                f"polygon point {index} is not a [longitude, latitude] pair."
            )
        lon = _number(pair[0], f"point {index} longitude")
        lat = _number(pair[1], f"point {index} latitude")
        _check_point(lon, lat, f"polygon point {index}")
        ring.append([lon, lat])

    # Close the ring if the caller did not. Leaflet returns an open ring.
    if ring[0] != ring[-1]:
        ring.append(list(ring[0]))

    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    area = _bbox_area_km2(min(lons), min(lats), max(lons), max(lats))
    if area > MAX_AREA_KM2:
        raise InvalidFootprint(
            f"polygon spans about {area:,.0f} km2, above the "
            f"{MAX_AREA_KM2:,.0f} km2 limit."
        )

    return {
        "kind": "polygon",
        "ring": ring,
        "points": len(ring) - 1,
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "approx_area_km2": round(area, 1),
    }


def build(bbox=None, point=None, radius_km=None, polygon=None):
    """Whichever shape was supplied. Returns (geometry, meta) or (None, None).

    Exactly one shape, or none. Accepting several and silently preferring one
    would mean the caller could not tell which was measured.
    """
    supplied = [
        name
        for name, value in (
            ("bbox", bbox), ("point", point), ("polygon", polygon)
        )
        if value is not None
    ]

    if not supplied:
        return None, None
    if len(supplied) > 1:
        raise InvalidFootprint(
            f"Supply one area, not {len(supplied)} ({', '.join(supplied)})."
        )

    if bbox is not None:
        shape = from_bbox(bbox)
    elif point is not None:
        if radius_km is None:
            raise InvalidFootprint("point needs radius_km.")
        shape = from_point(point, radius_km)
    else:
        shape = from_polygon(polygon)

    return geometry(shape), metadata(shape)


def geometry(shape):
    """The Earth Engine geometry for an already-validated shape.

    The only function here that touches Earth Engine. Validation and metadata
    are deliberately pure: a transposed coordinate should be caught by
    arithmetic, not by an authenticated round trip, and the honesty of the
    metadata is worth testing on a machine with no credentials.

    geodesic=False because a box drawn on a web map is a box in the map's
    projection - its edges follow lines of latitude and longitude, not great
    circles. Letting Earth Engine curve them would measure a slightly
    different area than the one the user drew.
    """
    earth_engine.initialize()
    kind = shape["kind"]

    if kind == "bbox":
        return ee.Geometry.Rectangle(shape["bbox"], geodesic=False)
    if kind == "circle":
        return ee.Geometry.Point(shape["centre"]).buffer(
            shape["radius_km"] * 1000.0
        )
    if kind == "polygon":
        return ee.Geometry.Polygon([shape["ring"]], geodesic=False)

    raise InvalidFootprint(f"unknown footprint kind {kind!r}")


def metadata(shape):
    """Region metadata for a user-defined area.

    Deliberately shaped like the GAUL metadata so the rest of the pipeline does
    not care where the geometry came from - and deliberately different in every
    field that makes a claim. No district name, no state, no boundary_source
    pointing at a dataset that was not consulted. Anyone reading the response
    can see the footprint was drawn rather than looked up.
    """
    label = {
        "bbox": "user-defined rectangle",
        "circle": "user-defined circle",
        "polygon": "user-defined area",
    }[shape["kind"]]

    return {
        "slug": f"custom-{shape['kind']}",
        "name": label,
        "admin_level": "custom",
        "state": None,
        "boundary_source": "user-supplied",
        "boundary_vintage": None,
        "footprint": shape,
        "note": (
            f"This is a {label}, not an administrative boundary. Figures cover "
            "exactly the shape supplied, which may cross district or state "
            "lines and may not contain all of any named place."
        ),
    }
