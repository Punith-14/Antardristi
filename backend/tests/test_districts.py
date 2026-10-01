"""
Flooding per district (geo/districts.py): the state table, the drawn-area
overlap, and how both reach the result and the report.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

pytest.importorskip("ee")

from geo import districts as D  # noqa: E402

KM2 = 1e6

RAW = [
    {"name": "Alappuzha", "state": "Kerala", "flooded_m2": 120 * KM2,
     "observed_m2": 1400 * KM2, "in_region_m2": 1415 * KM2},
    {"name": "Wayanad", "state": "Kerala", "flooded_m2": 4 * KM2,
     "observed_m2": 900 * KM2, "in_region_m2": 2130 * KM2},      # half outside the swath
    {"name": "Thrissur", "state": "Kerala", "flooded_m2": 60 * KM2,
     "observed_m2": 3000 * KM2, "in_region_m2": 3032 * KM2},
    {"name": "Edge", "state": "Kerala", "flooded_m2": 0, "observed_m2": 0,
     "in_region_m2": 0},                                          # touches only the border
]


# ---------------------------------------------------------------- the table

def test_districts_are_ranked_worst_first():
    rows = D.build_rows(RAW)
    assert [r["name"] for r in rows] == ["Alappuzha", "Thrissur", "Wayanad"]
    assert [r["rank"] for r in rows] == [1, 2, 3]


def test_the_flooded_share_is_of_what_was_seen_not_of_the_district():
    alappuzha = D.build_rows(RAW)[0]
    assert alappuzha["flooded_km2"] == 120.0
    assert alappuzha["flooded_pct"] == pytest.approx(100 * 120 / 1400, abs=0.01)


def test_a_half_observed_district_is_flagged_not_quietly_ranked_low():
    """Wayanad shows little flooding partly because half of it was not seen."""
    wayanad = next(r for r in D.build_rows(RAW) if r["name"] == "Wayanad")
    assert wayanad["low_coverage"] is True
    assert wayanad["observed_pct"] == pytest.approx(42.3, abs=0.1)


def test_a_district_that_only_touches_the_border_is_left_out():
    assert "Edge" not in [r["name"] for r in D.build_rows(RAW)]


def test_people_are_matched_on_name_and_state():
    """India reuses district names. Aurangabad in Bihar must not get the
    population of Aurangabad in Maharashtra."""
    raw = [
        {"name": "Aurangabad", "state": "Bihar", "flooded_m2": 10 * KM2,
         "observed_m2": 100 * KM2, "in_region_m2": 100 * KM2},
        {"name": "Aurangabad", "state": "Maharashtra", "flooded_m2": 5 * KM2,
         "observed_m2": 100 * KM2, "in_region_m2": 100 * KM2},
    ]
    people = {D.district_key("Aurangabad", "Bihar"): {"low": 900, "high": 1100},
              D.district_key("Aurangabad", "Maharashtra"): {"low": 40, "high": 60}}
    rows = {r["state"]: r for r in D.build_rows(raw, people)}
    assert rows["Bihar"]["people"]["low"] == 900
    assert rows["Maharashtra"]["people"]["low"] == 40


# ------------------------------------------------------------- sum check

def test_districts_that_add_up_pass_the_check():
    rows = D.build_rows(RAW)
    check = D.sum_check(rows, 184.0)
    assert check["districts_total_km2"] == 184.0
    assert check["within_tolerance"] is True


def test_a_mismatch_is_reported_not_hidden():
    """The 2015 state and district boundaries are separate layers and do not
    align exactly. A real gap must show as a gap."""
    check = D.sum_check(D.build_rows(RAW), 220.0)
    assert check["difference_km2"] == -36.0
    assert check["within_tolerance"] is False


# --------------------------------------------------------- drawn areas

def test_a_drawn_area_lists_its_share_in_each_district():
    rows = D.add_shares(D.build_rows(RAW[:2]))
    shares = {r["name"]: r["share_of_area_pct"] for r in rows}
    assert shares["Alappuzha"] == pytest.approx(100 * 1415 / 3545, abs=0.1)
    assert sum(shares.values()) == pytest.approx(100, abs=0.2)


# ------------------------------------------------------------------ note

def test_partly_observed_districts_get_one_sentence():
    note = D.coverage_note(D.build_rows(RAW))
    assert note.startswith("1 of 3 districts")
    assert "Wayanad" in note
    assert note.count(". ") == 0


def test_no_note_when_every_district_was_seen():
    assert D.coverage_note(D.build_rows(RAW[:1])) is None


# ------------------------------------------------------- which breakdown

class FakeCollection:
    def __init__(self):
        self.ops = []

    def filter(self, f):
        self.ops.append(("filter", f))
        return self

    def filterBounds(self, g):
        self.ops.append(("bounds", g))
        return self


@pytest.mark.parametrize("level,kind", [("state", "state_breakdown"),
                                        ("custom", "drawn_area_overlap"),
                                        ("district", None)])
def test_the_breakdown_depends_on_what_was_analysed(monkeypatch, level, kind):
    import ee
    monkeypatch.setattr(ee, "FeatureCollection", lambda _id: FakeCollection())
    monkeypatch.setattr(ee.Filter, "eq", lambda *a: a)
    features, got = D.districts_for({"admin_level": level, "name": "Kerala"}, "REGION")
    assert got == kind
    assert (features is None) == (kind is None)


# --------------------------------------------------- through the flood path

FAKE = {
    "kind": "state_breakdown",
    "rows": D.build_rows(RAW, {D.district_key("Alappuzha", "Kerala"): {"low": 41000, "high": 52000}}),
    "sum_check": {"districts_total_km2": 184.0, "region_total_km2": 64.0,
                  "difference_km2": 120.0, "within_tolerance": False},
    "note": D.coverage_note(D.build_rows(RAW)),
    "boundary_source": "FAO/GAUL/2015/level2 (2015)",
}


def run_with(monkeypatch, value):
    import flood_scenario as S
    from pipeline import analysis

    region = S.install(monkeypatch)
    seen = {}

    def fake(flood_mask, valid, region, region_meta, scale, flood_km2, event_date=None):
        seen.update(meta=region_meta, scale=scale, flood_km2=flood_km2, date=event_date)
        return value

    monkeypatch.setattr(D, "breakdown", fake)
    return analysis.analyse_flood(region, dict(S.META), *S.POST,
                                  force_sensor="sentinel-1", scale=200), seen


def test_the_flood_path_asks_with_its_own_total_and_scale(monkeypatch):
    _, seen = run_with(monkeypatch, FAKE)
    assert seen["meta"]["admin_level"] == "state"
    assert seen["scale"] == 200
    assert seen["flood_km2"] == 64.0
    assert seen["date"] == "2018-08-01"


def test_the_table_and_its_coverage_note_reach_the_result(monkeypatch):
    result, _ = run_with(monkeypatch, FAKE)
    assert result["districts"]["rows"][0]["name"] == "Alappuzha"
    assert FAKE["note"] in result["unobserved"]["notes"]


def test_the_report_names_the_worst_districts_and_still_verifies(monkeypatch):
    from pipeline import report

    result, _ = run_with(monkeypatch, FAKE)
    text, verification = report.build_report(result, prefer_llm=False)
    assert "The most affected districts are Alappuzha (120.0 km2)" in text["text"]
    assert verification["passed"], verification["unsupported_claims"]
    assert verification["caveats"]["completeness"] == 1.0


def test_the_model_sees_districts_and_their_coverage(monkeypatch):
    from pipeline import report

    result, _ = run_with(monkeypatch, FAKE)
    prompt = report._build_prompt(result)
    assert "DISTRICTS (worst first)" in prompt
    assert "Wayanad" in prompt and "PARTLY OBSERVED" in prompt
    assert "people living in flooded area 41000 to 52000" in prompt


def test_a_breakdown_failure_never_costs_the_flood_result(monkeypatch):
    result, _ = run_with(monkeypatch, {"error": "district breakdown could not be computed: x"})
    assert any(e["quantity"] == "flood_extent" for e in result["evidence"])
    assert "error" in result["districts"]
