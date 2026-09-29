"""
User-defined areas.

Two things are under test and the second matters more than the first.

Validation: a footprint that reaches Earth Engine starts a reduction, so bad
input has to be caught before it costs anything. A transposed coordinate pair
is the common one - 9.4 E, 76.3 N is the Norwegian Sea.

Honesty: the metadata for a drawn box must not look like the metadata for a
district. A rectangle over Ladakh covers parts of its neighbours and misses
corners of Ladakh itself; the flood area inside it is a real measurement of a
real place, but it is not "flood extent in Ladakh". If the response carries a
district name or cites FAO GAUL, nothing downstream can tell the difference -
the same failure as comparing May against May and reporting it as change.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import footprint  # noqa: E402

# Roughly Kuttanad, where the Kerala flood zones sit.
KUTTANAD = [76.3, 9.4, 76.6, 9.7]
# Leh, in a district FAO GAUL 2015 has never heard of.
LEH = [77.4, 34.0, 77.8, 34.3]


# ------------------------------------------------------------------ bbox

def test_a_sensible_box_is_accepted():
    shape = footprint.from_bbox(KUTTANAD)
    assert shape["kind"] == "bbox"
    assert shape["bbox"] == KUTTANAD


def test_a_post_2015_district_is_reachable_by_drawing_it():
    """The whole point. Ladakh has no boundary in GAUL 2015; it does have a
    location."""
    shape = footprint.from_bbox(LEH)
    assert footprint.metadata(shape)["admin_level"] == "custom"


@pytest.mark.parametrize("bad,expected", [
    ([76.6, 9.4, 76.3, 9.7], "east"),        # west past east
    ([76.3, 9.7, 76.6, 9.4], "north"),       # south past north
    ([76.3, 9.4, 76.6], "four numbers"),
    ("not a box", "four numbers"),
])
def test_a_malformed_box_is_refused_with_a_reason(bad, expected):
    with pytest.raises(footprint.InvalidFootprint) as caught:
        footprint.from_bbox(bad)
    assert expected in str(caught.value)


def test_swapped_longitude_and_latitude_are_caught():
    """9.4 E, 76.3 N is in the Norwegian Sea. Catching it here beats running a
    reduction over the wrong hemisphere and returning a confident zero."""
    with pytest.raises(footprint.InvalidFootprint) as caught:
        footprint.from_bbox([9.4, 76.3, 9.7, 76.6])
    assert "longitude, latitude" in str(caught.value)


def test_a_box_smaller_than_a_pixel_is_refused():
    with pytest.raises(footprint.InvalidFootprint) as caught:
        footprint.from_bbox([76.3, 9.4, 76.30005, 9.40005])
    assert "pixel" in str(caught.value)


def test_an_absurdly_large_box_is_refused_and_says_how_large():
    with pytest.raises(footprint.InvalidFootprint) as caught:
        footprint.from_bbox([70.0, 10.0, 90.0, 30.0])
    message = str(caught.value)
    assert "km2" in message
    assert any(ch.isdigit() for ch in message)


def test_area_shrinks_with_latitude():
    """A degree of longitude narrows away from the equator. Ignoring that
    overestimates Himalayan areas by about a third, which would let through
    boxes the backend means to reject."""
    kerala = footprint._bbox_area_km2(76, 9, 77, 10)
    ladakh = footprint._bbox_area_km2(77, 34, 78, 35)
    assert ladakh < kerala


# ---------------------------------------------------------------- circle

def test_a_point_and_radius_is_accepted():
    shape = footprint.from_point([76.44, 9.52], 25)
    assert shape["radius_km"] == 25
    assert shape["centre"] == [76.44, 9.52]


def test_a_point_without_a_radius_is_refused():
    with pytest.raises(footprint.InvalidFootprint) as caught:
        footprint.from_point([76.44, 9.52], None)
    assert "radius_km" in str(caught.value)


@pytest.mark.parametrize("radius", [0, -5, 0.1, 5000])
def test_an_out_of_range_radius_is_refused(radius):
    with pytest.raises(footprint.InvalidFootprint):
        footprint.from_point([76.44, 9.52], radius)


# --------------------------------------------------------------- polygon

def test_a_polygon_is_accepted_and_closed_automatically():
    """Leaflet returns an open ring; Earth Engine wants it closed."""
    ring = [[76.3, 9.4], [76.6, 9.4], [76.6, 9.7], [76.3, 9.7]]
    shape = footprint.from_polygon(ring)

    assert shape["points"] == 4
    assert shape["ring"][0] == shape["ring"][-1], "the ring must be closed"
    assert len(shape["ring"]) == 5


def test_a_polygon_with_too_few_points_is_refused():
    with pytest.raises(footprint.InvalidFootprint) as caught:
        footprint.from_polygon([[76.3, 9.4], [76.6, 9.7]])
    assert "three" in str(caught.value)


def test_a_polygon_with_absurdly_many_points_is_refused():
    ring = [[76.3 + i * 0.0001, 9.4] for i in range(footprint.MAX_POLYGON_POINTS + 5)]
    with pytest.raises(footprint.InvalidFootprint) as caught:
        footprint.from_polygon(ring)
    assert "Simplify" in str(caught.value)


# ------------------------------------------------------------ build rules

def test_no_shape_returns_nothing_rather_than_guessing():
    assert footprint.build() == (None, None)


def test_two_shapes_are_refused_rather_than_one_silently_winning():
    """If both a box and a circle arrive, preferring one means the caller
    cannot tell which was measured. Refused before Earth Engine is touched."""
    with pytest.raises(footprint.InvalidFootprint) as caught:
        footprint.build(bbox=KUTTANAD, point=[76.44, 9.52], radius_km=25)
    assert "one area" in str(caught.value)


# ------------------------------------------------- the metadata must not lie

def test_a_drawn_area_never_claims_an_administrative_boundary():
    """The test this module exists for.

    If any of these fields looked like the GAUL metadata, a rectangle would be
    indistinguishable from a district in the response - and every check
    downstream would still pass, because the arithmetic over the rectangle is
    perfectly correct.
    """
    meta = footprint.metadata(footprint.from_bbox(LEH))

    assert meta["admin_level"] == "custom"
    assert meta["boundary_source"] == "user-supplied"
    assert meta["boundary_vintage"] is None
    assert meta["state"] is None
    assert "gaul" not in str(meta).lower()


def test_the_name_reads_as_a_shape_not_a_place():
    meta = footprint.metadata(footprint.from_bbox(LEH))
    assert "user-defined" in meta["name"]
    # Naming it after a place someone had in mind is exactly the confusion.
    assert "ladakh" not in meta["name"].lower()


def test_the_note_warns_that_the_shape_is_not_a_district():
    meta = footprint.metadata(footprint.from_bbox(LEH))
    note = meta["note"].lower()
    assert "not an administrative boundary" in note
    assert "may not contain all" in note


@pytest.mark.parametrize("shape", [
    footprint.from_bbox(KUTTANAD),
    footprint.from_point([76.44, 9.52], 25),
    footprint.from_polygon([[76.3, 9.4], [76.6, 9.4], [76.6, 9.7]]),
])
def test_every_shape_carries_the_same_honest_fields(shape):
    meta = footprint.metadata(shape)
    for field in ("slug", "name", "admin_level", "boundary_source", "note"):
        assert meta[field], f"{field} missing for {shape['kind']}"
    assert meta["admin_level"] == "custom"


def test_the_area_is_reported_so_the_user_can_sanity_check_it():
    meta = footprint.metadata(footprint.from_bbox(KUTTANAD))
    area = meta["footprint"]["approx_area_km2"]
    # 0.3 x 0.3 degrees near the equator is roughly 1,100 km2.
    assert 900 < area < 1300, area


def test_the_frontend_and_backend_limits_agree():
    """lib/bbox.js duplicates these so the user hears "too big" while still
    holding the mouse. If they drift, the UI accepts a box the API rejects."""
    js = (BACKEND.parent / "frontend" / "src" / "lib" / "bbox.js").read_text(
        encoding="utf-8"
    )
    assert f"MAX_AREA_KM2 = {int(footprint.MAX_AREA_KM2)}" in js
    assert f"MIN_SPAN_DEG = {footprint.MIN_SPAN_DEG}" in js

    west, south, east, north = footprint.INDIA_BOUNDS
    assert f"[{west}, {south}, {east}, {north}]" in js


def test_geometry_starts_earth_engine_itself(monkeypatch):
    """The 500 that prompted earth_engine.py.

    footprint.geometry() reached ee.Geometry without passing through any
    initialiser and died with "Earth Engine client library not initialized" -
    at request time, not at startup, because three modules each had their own
    copy of the setup and a new code path went through none of them.
    """
    import earth_engine

    called = []
    monkeypatch.setattr(earth_engine, "initialize", lambda *a, **k: called.append(1))
    monkeypatch.setattr(footprint.ee.Geometry, "Rectangle", lambda *a, **k: "geom")

    footprint.geometry(footprint.from_bbox(KUTTANAD))
    assert called, "geometry() must initialise Earth Engine before using it"


def test_initialisation_lives_in_exactly_one_place():
    """Three copies is what let a new path miss it. A fourth would do the same."""
    import re

    backend = Path(__file__).resolve().parent.parent
    offenders = []
    for path in backend.glob("*.py"):
        if path.name == "earth_engine.py":
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"^\s*ee\.Initialize\(", source, re.M):
            offenders.append(path.name)

    assert not offenders, (
        f"{offenders} call ee.Initialize directly. Use earth_engine.initialize()."
    )


# ------------------------------------------------- the /ask path takes a shape
#
# Checked by reading main.py rather than importing it, so these run without
# FastAPI installed and cannot be quietly skipped - the same reason the
# validation-numbers check reads source.

MAIN = (BACKEND / "main.py").read_text(encoding="utf-8")


def _block(header, length=1400):
    return MAIN.split(header)[1][:length]


def test_ask_accepts_a_drawn_area():
    """The gap that split the two interfaces.

    /ask read only a question, so the map's draw tool could not reach the
    natural-language path at all - and that path is the one that works for
    districts GAUL 2015 cannot name.
    """
    block = _block("class AskRequest(BaseModel):")
    for field in ("bbox", "point", "radius_km", "polygon"):
        assert f"{field}:" in block, f"AskRequest cannot receive {field}"


def test_the_area_reaches_the_analysis_rather_than_stopping_at_the_router():
    """Declaring the fields is not enough - they have to be passed through to
    the sub-request that actually runs."""
    block = _block("def ask(payload: AskRequest):", 2600)
    assert "payload.area_fields()" in block


def test_a_drawn_area_clears_the_routed_region_rather_than_competing_with_it():
    """If both survived, the routing block would name a district while the
    analysis measured a box, with nothing saying which was used."""
    block = _block("def ask(payload: AskRequest):", 2600)
    assert 'decision["region"] = None' in block
    assert "user_defined_area" in block


def test_a_question_with_no_region_and_no_shape_is_still_refused():
    """Drawing must not become a way to run an analysis over nowhere."""
    block = _block("def ask(payload: AskRequest):", 2600)
    assert "not drawn and not decision.get(\"region\")" in block


def test_the_understood_line_names_the_drawn_area():
    """"no region identified" reads as a failure when the caller deliberately
    supplied a shape instead of a name."""
    import routing

    decision = {
        "analysis_type": "flood_extent",
        "region": None,
        "region_source": "user_defined_area",
        "post_start": "2018-08-01",
        "post_end": "2018-08-31",
    }
    text = routing.describe(decision)
    assert "the area you drew" in text
    assert "no region identified" not in text
