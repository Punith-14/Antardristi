"""
Tests that need something outside this process.

Skipped by default so `pytest` is fast and needs no credentials.

    pytest -m earthengine     needs EE authentication, consumes quota
    pytest -m api             needs uvicorn running
    pytest -m llm             needs GROQ_API_KEY
    pytest -m "not slow"      the default, roughly a second
"""

import os

import pytest
import requests

BASE = os.environ.get("ANTARDRISHTI_API", "http://127.0.0.1:8000")

KERALA_2018 = {
    "region": "kerala",
    "post_start": "2018-08-15",
    "post_end": "2018-08-25",
    "pre_start": "2018-02-01",
    "pre_end": "2018-04-30",
    "sensor": "sentinel-1",
    "scale": 200,
}


def api_available():
    try:
        return requests.get(f"{BASE}/", timeout=2).status_code == 200
    except Exception:
        return False


needs_api = pytest.mark.skipif(not api_available(), reason="uvicorn not running")


# --------------------------------------------------------------------- api

@pytest.mark.api
@needs_api
def test_catalogue_is_ordered_by_reliability():
    entries = requests.get(f"{BASE}/analyses", timeout=10).json()["surface"]
    assert entries[0]["reliability"] == "good"
    assert entries[-1]["reliability"] == "unvalidated"


@pytest.mark.api
@needs_api
def test_unknown_region_explains_the_boundary_vintage():
    response = requests.post(
        f"{BASE}/analyze", json={**KERALA_2018, "region": "not-a-place"}, timeout=30
    )
    assert response.status_code == 404
    assert "2015" in response.json()["detail"]


@pytest.mark.api
@pytest.mark.slow
@needs_api
def test_kerala_2018_end_to_end():
    payload = requests.post(f"{BASE}/analyze", json=KERALA_2018, timeout=900).json()

    assert payload["evidence"]
    assert payload["verification"]["faithfulness_rate"] == 1.0
    assert payload["verification"]["caveats"]["completeness"] == 1.0

    # Coverage must be measured, not assumed.
    coverage = payload["observation"]["coverage_fraction"]
    assert 0 < coverage < 1.0

    # No zone can exceed the extent it is part of.
    extent = next(
        e["value"] for e in payload["evidence"] if e["quantity"] == "flood_extent"
    )
    for zone in payload["zones"]:
        assert zone["area_km2"] <= extent


@pytest.mark.api
@pytest.mark.slow
@needs_api
def test_caching_makes_a_repeat_query_fast():
    import time

    requests.post(f"{BASE}/analyze", json=KERALA_2018, timeout=900)

    started = time.time()
    payload = requests.post(f"{BASE}/analyze", json=KERALA_2018, timeout=60).json()
    elapsed = time.time() - started

    assert payload.get("_cache", {}).get("hit") is True
    assert elapsed < 5


@pytest.mark.api
@pytest.mark.slow
@needs_api
def test_zones_geojson_carries_outlines_and_markers():
    payload = requests.post(f"{BASE}/analyze", json=KERALA_2018, timeout=900).json()
    collection = requests.get(
        f"{BASE}/analyze/{payload['request_id']}/zones.geojson", timeout=30
    ).json()

    kinds = {f["properties"]["kind"] for f in collection["features"]}
    assert kinds == {"outline", "marker"}


# ------------------------------------------------------------------- llm

@pytest.mark.llm
@pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"), reason="no GROQ_API_KEY")
def test_the_model_respects_the_evidence(contract):
    """The model receives only the evidence array. If it invents a figure the
    verifier catches it and we fall back - so a pass here means it complied."""
    from pipeline.report import build_report

    report, verification = build_report(contract, prefer_llm=True)

    if report["fallback_used"]:
        rejected = report.get("rejected_attempt")
        pytest.fail(
            f"LLM output rejected: {report['fallback_reason']}. "
            f"{rejected['unsupported_claims'] if rejected else ''}"
        )

    assert verification["faithfulness_rate"] == 1.0
    assert verification["caveats"]["completeness"] == 1.0


# ---------------------------------------------------------- earth engine

@pytest.mark.earthengine
@pytest.mark.slow
def test_masks_behave_over_a_landlocked_control():
    """The control that caught a bad mask during development: any 'sea' layer
    reporting area inside landlocked Punjab is wrong by definition."""
    import ee

    from pipeline import analysis

    analysis._initialize()

    punjab = (
        ee.FeatureCollection("FAO/GAUL/2015/level1")
        .filter(ee.Filter.eq("ADM0_NAME", "India"))
        .filter(ee.Filter.eq("ADM1_NAME", "Punjab"))
        .geometry()
    )

    area = punjab.area(maxError=100).getInfo() / 1e6
    assert 45_000 < area < 55_000, f"Punjab measured {area:.0f} km2"

    water = analysis.area_km2(analysis.permanent_water_mask(punjab), punjab, 500)
    assert water < area * 0.05, "permanent water should be a small share of Punjab"


@pytest.mark.earthengine
@pytest.mark.slow
def test_admin_boundaries_are_land_only():
    """Kerala's GAUL polygon measured 38,002 km2 against a 38,863 km2 land
    reference, which is how we established that no sea enters the statistics."""
    import ee

    from pipeline import analysis

    analysis._initialize()
    geometry, meta = analysis.resolve_geometry("kerala")

    assert meta["boundary_vintage"] == "2015"
    area = geometry.area(maxError=100).getInfo() / 1e6
    assert 37_000 < area < 39_500
