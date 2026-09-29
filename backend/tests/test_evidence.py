"""Evidence record: the layer that makes every claim checkable."""

import pytest

from evidence import EvidenceBuilder, Observation, coverage_warning, utc_now


def test_ids_are_assigned_in_order():
    builder = EvidenceBuilder()
    assert builder.add("flood_extent", 187.4, "km2") == "E1"
    assert builder.add("zone_count", 7, "zones") == "E2"
    assert len(builder) == 2


def test_null_values_are_refused():
    """A missing measurement is not zero. Recording it as evidence would let a
    'could not observe' result read as 'nothing there'."""
    builder = EvidenceBuilder()
    with pytest.raises(ValueError, match="null value"):
        builder.add("flood_extent", None, "km2")


def test_derived_values_must_reference_real_evidence():
    builder = EvidenceBuilder()
    with pytest.raises(ValueError, match="unknown id"):
        builder.add("fraction", 7.2, "percent", derived_from=["E7"])


def test_derived_chain_is_preserved():
    builder = EvidenceBuilder()
    e1 = builder.add("flood_extent", 187.4, "km2")
    e2 = builder.add("fraction", 7.21, "percent", derived_from=[e1])

    items = {item["id"]: item for item in builder.to_list()}
    assert items[e2]["derived_from"] == [e1]


def test_empty_fields_are_dropped_from_output():
    builder = EvidenceBuilder()
    builder.add("flood_extent", 187.4, "km2")
    item = builder.to_list()[0]

    assert "confidence" not in item
    assert "derived_from" not in item
    assert item["value"] == 187.4


def test_notes_are_collected():
    builder = EvidenceBuilder()
    builder.note("Only 18% of the region was observed.")
    assert len(builder.notes) == 1


# ------------------------------------------------------------- observation

def test_coverage_is_computed_not_supplied():
    """REGRESSION: coverage was hardcoded to 1.0 on the assumption that radar
    sees everything. Sentinel-1's swath is 250 km and we restrict to one
    relative orbit, so a single track often misses part of a state. Measured
    Kerala coverage was 0.912, not 1.0."""
    observation = Observation(
        sensor_used="sentinel-1",
        observable_area_km2=34674.5,
        region_area_km2=38001.5,
    )
    assert observation.coverage_fraction == pytest.approx(0.9124, abs=0.001)
    assert observation.to_dict()["coverage_fraction"] == pytest.approx(0.9124, abs=0.001)


def test_coverage_of_a_zero_area_region_does_not_divide_by_zero():
    observation = Observation(
        sensor_used="sentinel-2", observable_area_km2=0, region_area_km2=0
    )
    assert observation.coverage_fraction == 0.0


def test_rejected_scene_count_is_derived():
    observation = Observation(
        sensor_used="sentinel-2",
        observable_area_km2=100,
        region_area_km2=100,
        scenes_available=82,
        scenes_used=27,
    )
    assert observation.scenes_rejected_by_cloud_filter == 55


def test_rejected_count_never_goes_negative():
    observation = Observation(
        sensor_used="sentinel-1",
        observable_area_km2=100,
        region_area_km2=100,
        scenes_available=2,
        scenes_used=6,
    )
    assert observation.scenes_rejected_by_cloud_filter == 0


def test_region_area_is_not_leaked_into_the_response():
    """It is an input to the coverage calculation, not a reported figure."""
    observation = Observation(
        sensor_used="sentinel-1", observable_area_km2=90, region_area_km2=100
    )
    assert "region_area_km2" not in observation.to_dict()


# -------------------------------------------------------------- warning

def test_partial_coverage_produces_a_warning():
    """The Kerala August 2019 case: 6,820 km2 observable of 37,575."""
    observation = Observation(
        sensor_used="sentinel-2",
        observable_area_km2=6820,
        region_area_km2=37575,
    )
    warning = coverage_warning(observation)
    assert warning is not None
    assert "18" in warning


def test_full_coverage_produces_no_warning():
    observation = Observation(
        sensor_used="sentinel-1", observable_area_km2=99, region_area_km2=100
    )
    assert coverage_warning(observation) is None


def test_utc_now_is_iso_and_zulu():
    stamp = utc_now()
    assert stamp.endswith("Z")
    assert "T" in stamp
