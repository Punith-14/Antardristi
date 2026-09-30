"""
Time series: one full analysis per month, arranged and checked.

A line through monthly numbers asserts every point measured the same thing.
These tests are mostly about the ways that assertion quietly fails - a month
with no imagery drawn as zero, a month seen through a narrower footprint, a
month measured by a different method, orbit or sensor - and that each one is
flagged rather than smoothed over.

The end-to-end tests drive the real /analyze/series endpoint through the real
flood path, with Earth Engine replaced by the numpy grids in flood_scenario.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from pipeline import timeseries as ts  # noqa: E402


# --------------------------------------------------------------- windows

def test_windows_are_calendar_months_clipped_to_the_range():
    windows = ts.monthly_windows("2018-06-15", "2018-08-10")
    assert [(w["start"], w["end"], w["label"]) for w in windows] == [
        ("2018-06-15", "2018-06-30", "2018-06"),
        ("2018-07-01", "2018-07-31", "2018-07"),
        ("2018-08-01", "2018-08-10", "2018-08"),
    ]


def test_windows_cross_a_year_boundary():
    labels = [w["label"] for w in ts.monthly_windows("2018-11-01", "2019-02-28")]
    assert labels == ["2018-11", "2018-12", "2019-01", "2019-02"]


def test_february_ends_on_the_right_day():
    assert ts.monthly_windows("2020-02-01", "2020-02-29")[0]["end"] == "2020-02-29"
    assert ts.monthly_windows("2019-02-01", "2019-03-31")[0]["end"] == "2019-02-28"


def test_a_single_day_is_one_window():
    windows = ts.monthly_windows("2018-08-15", "2018-08-15")
    assert len(windows) == 1
    assert windows[0]["start"] == windows[0]["end"] == "2018-08-15"


def test_no_window_reaches_outside_the_dates_asked_for():
    for w in ts.monthly_windows("2018-01-20", "2018-12-05"):
        assert "2018-01-20" <= w["start"] <= w["end"] <= "2018-12-05"


def test_windows_never_overlap_and_leave_no_gaps():
    from datetime import date, timedelta
    windows = ts.monthly_windows("2018-01-20", "2018-12-05")
    for a, b in zip(windows, windows[1:]):
        assert date.fromisoformat(a["end"]) + timedelta(days=1) == date.fromisoformat(b["start"])


def test_more_than_twelve_months_is_refused_with_the_way_out():
    with pytest.raises(ts.InvalidSeries) as raised:
        ts.monthly_windows("2018-01-01", "2019-06-30")
    message = str(raised.value)
    assert "18 months" in message
    assert "cached" in message, "should say that splitting costs nothing"


@pytest.mark.parametrize("start,end", [("2018-08-31", "2018-08-01"),
                                       ("not a date", "2018-08-01"),
                                       ("2018-08-01", None)])
def test_bad_ranges_are_refused_by_name(start, end):
    with pytest.raises(ts.InvalidSeries):
        ts.monthly_windows(start, end)


# ----------------------------------------------------------------- points

WINDOW = {"start": "2018-08-01", "end": "2018-08-31", "label": "2018-08"}


def result(value=620.3, coverage=0.95, method="Sentinel-1 VV+VH at -20.0 dB",
           orbit=63, sensor="sentinel-1", request_id="abc"):
    return {
        "request_id": request_id,
        "observation": {"sensor_used": sensor, "relative_orbit": orbit,
                        "coverage_fraction": coverage, "observable_area_km2": 35000.0,
                        "scenes_used": 4},
        "evidence": [
            {"id": "E1", "quantity": "flood_extent", "value": value, "unit": "km2",
             "method": method},
            {"id": "E2", "quantity": "flood_extent_fraction", "value": 1.79,
             "unit": "percent"},
        ],
    }


def test_a_point_is_read_from_its_own_evidence_record():
    point = ts.point_from(WINDOW, result())
    assert point["value"] == 620.3
    assert point["evidence_id"] == "E1"
    assert point["request_id"] == "abc"
    assert point["fraction"] == {"value": 1.79, "unit": "percent", "evidence_id": "E2"}
    assert point["flags"] == []


def test_an_unobserved_month_is_a_gap_not_a_zero():
    """The failure this whole module is built around."""
    no_data = {"unobserved": {"reason": "no_usable_imagery",
                              "notes": ["No Sentinel-1 scenes"]}, "evidence": []}
    point = ts.point_from(WINDOW, no_data)

    assert point["observed"] is False
    assert point["value"] is None, "a gap must never be recorded as 0"
    assert "not a month without water" in point["flags"][0]["text"]
    assert "No Sentinel-1 scenes" in point["flags"][0]["text"]


def test_a_missing_result_is_also_a_gap():
    assert ts.point_from(WINDOW, None)["value"] is None


def test_low_coverage_is_flagged_and_points_to_the_percentage():
    point = ts.point_from(WINDOW, result(coverage=0.4))
    flag = point["flags"][0]
    assert flag["kind"] == "low_coverage"
    assert "40%" in flag["text"]
    assert "percentage" in flag["text"]


def test_the_coverage_threshold_matches_the_single_analysis_warning():
    """Two definitions of 'partial' would let a month pass one check and fail
    the other."""
    import inspect

    from core import evidence
    default = inspect.signature(evidence.coverage_warning).parameters["threshold"].default
    assert ts.LOW_COVERAGE == default


# --------------------------------------------------------- comparability

def series(*results):
    windows = [{"start": f"2018-{m:02d}-01", "end": f"2018-{m:02d}-28",
                "label": f"2018-{m:02d}"} for m in range(6, 6 + len(results))]
    return ts.build(windows, list(results), sensor="sentinel-1")


def kinds(point):
    return {f["kind"] for f in point["flags"]}


def test_a_uniform_series_is_comparable():
    out = series(result(), result(value=700.0), result(value=450.0))
    assert out["comparability"]["comparable"] is True
    assert out["comparability"]["notes"] == []


def test_one_odd_orbit_is_flagged_not_every_normal_month():
    out = series(result(), result(orbit=136), result())
    assert kinds(out["points"][1]) == {"orbit_differs"}
    assert kinds(out["points"][0]) == set()
    assert out["comparability"]["comparable"] is False
    assert out["comparability"]["differently_measured"] == ["2018-07"]


def test_a_method_change_is_flagged():
    """Dual-pol missing one month: VV alone at -17 dB is a different rule."""
    out = series(result(), result(method="Sentinel-1 VV at -17.0 dB"), result())
    assert "method_differs" in kinds(out["points"][1])


def test_a_sensor_change_is_flagged():
    out = series(result(), result(), result(sensor="sentinel-2", orbit=None))
    assert "sensor_differs" in kinds(out["points"][2])


def test_gaps_and_partial_months_do_not_make_a_series_incomparable():
    """They are caveats on single points, not a change of what is measured -
    so they are listed, but the rest of the series still stands."""
    no_data = {"unobserved": {"notes": ["cloud"]}, "evidence": []}
    out = series(result(), no_data, result(coverage=0.5))
    c = out["comparability"]
    assert c["comparable"] is True
    assert c["gaps"] == ["2018-07"]
    assert c["partial"] == ["2018-08"]
    assert any("no value is interpolated" in n for n in c["notes"])


def test_the_series_adds_no_numbers_of_its_own():
    """Every value in the series is a value from some month's evidence."""
    results = [result(value=v, request_id=f"r{i}") for i, v in enumerate((1.5, 2.5, 3.5))]
    out = series(*results)
    for point, source in zip(out["points"], results):
        assert point["value"] == source["evidence"][0]["value"]
        assert point["request_id"] == source["request_id"]
    assert "series adds no figures" in out["provenance"]["note"]


def test_a_series_needs_one_result_per_window():
    with pytest.raises(ts.InvalidSeries):
        ts.build([WINDOW, WINDOW], [result()])


# ------------------------------------------------ end to end, real endpoint

@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("ee")
    from fastapi.testclient import TestClient

    import flood_scenario as S
    import main
    from core import cache
    from detection import sar

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    region = S.install(monkeypatch)
    monkeypatch.setattr(main, "resolve_area_or_fail",
                        lambda payload: (region, dict(S.META)))

    # Per month: which orbit, whether imagery exists, how much is seen.
    months = {
        "2018-06": {"orbit": 63},
        "2018-07": {"orbit": 63, "no_imagery": True},
        "2018-08": {"orbit": 136},
        "2018-09": {"orbit": 63, "half_seen": True},
    }

    class Collection(S.FakeCollection):
        def __init__(self, month):
            super().__init__(0 if months[month].get("no_imagery") else 3)
            self.month = month

    monkeypatch.setattr(sar, "get_collection",
                        lambda region, start, end, *a, **k: Collection(start[:7]))
    monkeypatch.setattr(sar, "dominant_orbit",
                        lambda c: S.Info(months[c.month]["orbit"]))

    real_detect = S.fake_sar_detect

    def detect(region, start_date, end_date, **kwargs):
        mask, composite, info = real_detect(region, S.POST[0], end_date, **kwargs)
        if months[start_date[:7]].get("half_seen"):
            half = S.grid(S.SHAPE, (0, 0, 20, 10))
            composite = S.FakeImage(composite.data, half)
            mask = S.FakeImage(mask.data, half)
        return mask, composite, info

    monkeypatch.setattr(sar, "detect_water", detect)
    return TestClient(main.app)


def test_the_endpoint_runs_one_real_analysis_per_month(client):
    response = client.post("/analyze/series", json={
        "region": "testland", "start": "2018-06-01", "end": "2018-09-30",
    })
    assert response.status_code == 200, response.text
    body = response.json()

    assert [p["label"] for p in body["points"]] == ["2018-06", "2018-07", "2018-08", "2018-09"]
    june, july, august, september = body["points"]

    assert june["value"] == 64.0 and june["flags"] == []
    assert july["value"] is None and kinds(july) == {"unobserved"}
    assert kinds(august) == {"orbit_differs"}
    assert kinds(september) == {"low_coverage"}

    c = body["comparability"]
    assert c["gaps"] == ["2018-07"]
    assert c["partial"] == ["2018-09"]
    assert c["differently_measured"] == ["2018-08"]
    assert c["comparable"] is False


def test_every_observed_month_can_be_opened_and_exported_on_its_own(client):
    body = client.post("/analyze/series", json={
        "region": "testland", "start": "2018-06-01", "end": "2018-06-30",
    }).json()
    request_id = body["points"][0]["request_id"]

    single = client.get(f"/analyze/{request_id}")
    assert single.status_code == 200
    assert single.json()["evidence"][0]["value"] == body["points"][0]["value"]

    pytest.importorskip("reportlab")
    assert client.get(f"/analyze/{request_id}/report.pdf").status_code == 200


def test_a_repeated_series_is_served_from_the_cache(client, monkeypatch):
    """Completed months are cached, which is what makes 'run it in parts' free."""
    from detection import sar

    payload = {"region": "testland", "start": "2018-06-01", "end": "2018-06-30"}
    first = client.post("/analyze/series", json=payload).json()

    def must_not_run(*_, **__):
        raise AssertionError("a cached month was recomputed")

    monkeypatch.setattr(sar, "detect_water", must_not_run)
    second = client.post("/analyze/series", json=payload).json()
    assert second["points"][0]["value"] == first["points"][0]["value"]


def test_months_are_not_sent_to_the_language_model(client, monkeypatch):
    from pipeline import report

    def must_not_call(*_, **__):
        raise AssertionError("a series month asked for an LLM report")

    monkeypatch.setattr(report, "generate_llm_report", must_not_call)
    response = client.post("/analyze/series", json={
        "region": "testland", "start": "2018-06-01", "end": "2018-06-30",
    })
    assert response.status_code == 200


def test_letting_cloud_pick_the_sensor_is_refused(client):
    response = client.post("/analyze/series", json={
        "region": "testland", "start": "2018-06-01", "end": "2018-08-31", "sensor": "auto",
    })
    assert response.status_code == 400
    assert "same instrument" in response.json()["detail"]


def test_an_overlong_range_is_a_400(client):
    response = client.post("/analyze/series", json={
        "region": "testland", "start": "2017-01-01", "end": "2018-12-31",
    })
    assert response.status_code == 400
    assert "24 months" in response.json()["detail"]
