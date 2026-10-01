"""
Latest-pass mode: the newest radar image, its date and its age.

The date arithmetic is pure and tested directly. The endpoint is tested
through the real /analyze with Earth Engine replaced by the numpy flood
scenario, including the two rules that matter most: "latest" becomes real
dates before the cache is consulted, and an image's age is worked out when the
result is shown, never when it was cached.
"""

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from pipeline import latest as L  # noqa: E402


# ------------------------------------------------------------------ dates

def test_the_window_is_the_day_of_the_pass_with_an_exclusive_end():
    assert L.window("2026-09-28") == ("2026-09-28", "2026-09-29")


def test_the_extended_window_reaches_back_and_still_includes_the_pass():
    assert L.extended_window("2026-09-28", 6) == ("2026-09-22", "2026-09-29")


def test_timestamps_become_distinct_sorted_days():
    ms = lambda y, m, d, h: int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)
    days = L.days_from_ms([ms(2026, 9, 28, 12), ms(2026, 9, 16, 0), ms(2026, 9, 28, 1)])
    assert days == ["2026-09-16", "2026-09-28"]


@pytest.mark.parametrize("last,today,age,text", [
    ("2026-09-28", date(2026, 10, 1), 3, "3 days old"),
    ("2026-10-01", date(2026, 10, 1), 0, "today"),
    ("2026-09-30", date(2026, 10, 1), 1, "1 day old"),
    ("2026-10-05", date(2026, 10, 1), 0, "today"),       # clock skew never goes negative
])
def test_age_is_whole_days_and_never_negative(last, today, age, text):
    assert L.age_days(last, today) == age
    assert L.describe_age(age) == text


def test_age_is_worked_out_when_shown_not_when_cached():
    """The same cached acquisition, shown on two days, reports two ages."""
    acquisition = {"days": ["2026-09-28"], "first": "2026-09-28", "last": "2026-09-28"}
    assert L.freshness(acquisition, date(2026, 9, 29))["age_days"] == 1
    assert L.freshness(acquisition, date(2026, 10, 9))["age_days"] == 11
    assert "age_days" not in acquisition, "the stored block must not be changed"


# --------------------------------------------------------------- next pass

def test_the_next_pass_is_estimated_from_the_typical_gap():
    estimate = L.next_pass_estimate(["2026-09-04", "2026-09-16", "2026-09-28"])
    assert estimate["typical_gap_days"] == 12
    assert estimate["expected_on"] == "2026-10-10"
    assert "estimate" in estimate["note"]


def test_too_few_passes_give_no_estimate_rather_than_a_guess():
    assert L.next_pass_estimate(["2026-09-16", "2026-09-28"]) is None


# ----------------------------------------------------------------- "now"

@pytest.mark.parametrize("question", [
    "Is Assam flooded right now?",
    "Show the latest flooding in Kerala",
    "current flood situation in Bihar",
    "How much of Kendrapara is under water today",
])
def test_now_questions_are_recognised(question):
    assert L.asks_for_latest(question)


@pytest.mark.parametrize("question", [
    "Flooding in Kerala in August 2018",
    "Latest flooding in Assam in 2022",        # an explicit year wins
    "How did flooding in Bihar change",
    "flood extent in Kerala",
])
def test_dated_or_undated_questions_are_not_mistaken_for_now(question):
    assert not L.asks_for_latest(question)


def test_the_router_marks_now_questions_and_drops_an_invented_baseline():
    pytest.importorskip("ee")
    from pipeline import routing

    result = routing.route("Is Kerala flooded right now? compared with before",
                           prefer_model=False)
    if result.get("analysis_type") != "flood_extent":
        pytest.skip("rules did not route this as flood")
    assert result["latest"] is True
    assert result["pre_start"] is None
    assert "latest available radar pass" in routing.describe(result)


# ---------------------------------------------------------- the endpoint

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
    monkeypatch.setattr(main, "resolve_area_or_fail", lambda payload: (region, dict(S.META)))

    passes = {"found": {"date": "2018-08-01", "relative_orbit": 63,
                        "recent_passes": ["2018-07-08", "2018-07-20", "2018-08-01"],
                        "polarisations": "VV+VH", "lookback_days": 60}}
    monkeypatch.setattr(sar, "latest_acquisition", lambda region: dict(passes["found"]))

    real = S.fake_sar_detect

    def detect(region, start_date, end_date, **kwargs):
        mask, composite, info = real(region, S.POST[0], end_date, **kwargs)
        info["acquisition_days"] = [start_date]
        return mask, composite, info

    monkeypatch.setattr(sar, "detect_water", detect)
    return TestClient(main.app), passes


def test_latest_analyses_the_day_of_the_newest_pass(client):
    app, _ = client
    body = app.post("/analyze", json={"region": "testland", "latest": True,
                                      "generate_report": False}).json()
    assert body["period"]["post"] == {"start": "2018-08-01", "end": "2018-08-02"}
    assert body["latest"]["date"] == "2018-08-01"
    assert body["latest"]["next_pass"]["expected_on"] == "2018-08-13"


def test_every_result_says_how_old_its_image_is(client):
    app, _ = client
    body = app.post("/analyze", json={"region": "testland", "latest": True,
                                      "generate_report": False}).json()
    expected = (datetime.now(timezone.utc).date() - date(2018, 8, 1)).days
    assert body["acquisition"]["last"] == "2018-08-01"
    assert body["acquisition"]["age_days"] == expected


def test_a_new_pass_is_a_new_analysis_not_yesterdays_cache(client):
    """'latest' becomes real dates before the cache key is built."""
    app, passes = client
    first = app.post("/analyze", json={"region": "testland", "latest": True,
                                       "generate_report": False}).json()
    passes["found"] = {**passes["found"], "date": "2018-08-13"}
    second = app.post("/analyze", json={"region": "testland", "latest": True,
                                        "generate_report": False}).json()
    assert first["request_id"] != second["request_id"]
    assert second["period"]["post"]["start"] == "2018-08-13"


def test_low_coverage_brings_an_offer_to_extend_with_dates_stated(client, monkeypatch):
    import flood_scenario as S
    app, _ = client
    monkeypatch.setattr(S, "SAR_VALID", S.grid(S.SHAPE, (0, 0, 20, 8), value=False))
    body = app.post("/analyze", json={"region": "testland", "latest": True,
                                      "generate_report": False}).json()
    offer = body["latest"]["extend_offer"]
    assert offer == {**offer, "post_start": "2018-07-26", "post_end": "2018-08-02"}
    assert "60%" in offer["reason"]


def test_no_offer_when_the_pass_covered_enough(client):
    app, _ = client
    body = app.post("/analyze", json={"region": "testland", "latest": True,
                                      "generate_report": False}).json()
    assert "extend_offer" not in body["latest"]


def test_no_dates_and_no_latest_is_a_clear_400(client):
    app, _ = client
    response = app.post("/analyze", json={"region": "testland"})
    assert response.status_code == 400
    assert "latest" in response.json()["detail"]


def test_no_pass_in_the_lookback_window_is_a_404_that_says_so(client, monkeypatch):
    from detection import sar
    app, _ = client

    def none_found(region):
        raise sar.NoSarImagery("2026-08-01", "2026-10-01")

    monkeypatch.setattr(sar, "latest_acquisition", none_found)
    response = app.post("/analyze", json={"region": "testland", "latest": True})
    assert response.status_code == 404
    assert "last 60 days" in response.json()["detail"]


def test_old_requests_keep_their_cache_keys():
    """latest is a late field: unset, it is not in the key."""
    pytest.importorskip("fastapi")
    import main
    key = main.FloodRequest(region="kerala", post_start="2018-08-01",
                            post_end="2018-08-31").cache_key()
    assert "latest" not in key
