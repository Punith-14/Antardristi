"""
People living in the flooded area (geo/population.py) and how they reach the
evidence record, the zones and the report.
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

from geo import population as P  # noqa: E402


# ------------------------------------------------------------ year choice

@pytest.mark.parametrize("year,epoch", [(2016, 2015), (2018, 2020), (2020, 2020),
                                        (2026, 2025), (2017, 2015), (1990, 1990)])
def test_ghsl_uses_the_nearest_epoch(year, epoch):
    assert P.ghsl_epoch(year) == epoch


@pytest.mark.parametrize("year,used", [(2016, 2016), (2026, 2021), (1995, 2000)])
def test_worldpop_uses_the_event_year_within_what_it_publishes(year, used):
    assert P.worldpop_year(year) == used


def test_a_year_the_catalogue_lacks_falls_back_to_the_nearest_it_has():
    """A 2026 flood asked WorldPop for a year it did not hold; the people count
    and the district table both failed (found live on Assam, July 2026)."""
    assert P.pick_year(2021, [2000, 2010, 2020]) == 2020
    assert P.pick_year(2025, [2015, 2020, 2025, 2030]) == 2025
    assert P.pick_year(2025, [2015, 2020, 2030]) == 2020, "ties prefer the earlier, published year"
    assert P.pick_year(2021, []) == 2021, "catalogue unknown: use the documented year"
    assert P.ee_date_year(1577836800000) == 2020


def test_one_population_model_failing_does_not_cost_the_other(monkeypatch):
    class Value:
        def __init__(self, v): self.v = v
        def get(self, _): return self
        def getInfo(self): return self.v

    class Image:
        def __init__(self, ok): self.ok = ok
        def projection(self): return None
        def bandNames(self): return Value(["b"])

    monkeypatch.setattr(P.earth_engine, "initialize", lambda: None)
    monkeypatch.setattr(P, "sources_for", lambda year: [("ghsl", "GHSL", 2025, Image(False)),
                                                        ("worldpop", "WorldPop", 2020, Image(True))])
    monkeypatch.setattr(P, "flooded_population", lambda img, mask, scale: img)

    def sum_over(img, region, proj):
        if not img.ok:
            raise RuntimeError("Image.select: Parameter 'input' is required")
        return Value(12437)
    monkeypatch.setattr(P, "sum_over", sum_over)

    out = P.exposure(None, None, [], "2026-07-25", 200)
    assert "error" not in out, out
    assert out["people_in_flood"]["low"] == out["people_in_flood"]["high"] == 12400
    assert [s["key"] for s in out["sources"]] == ["worldpop"]

    monkeypatch.setattr(P, "sources_for", lambda year: [("ghsl", "GHSL", 2025, Image(False))])
    assert "error" in P.exposure(None, None, [], "2026-07-25", 200), "all failing is still an error"


# ---------------------------------------------------------------- rounding

@pytest.mark.parametrize("raw,shown", [(12437, 12400), (1049.9, 1000), (437, 440),
                                       (42.4, 42), (0.3, 1), (0, 0), (-5, 0)])
def test_counts_are_rounded_to_what_a_model_estimate_supports(raw, shown):
    assert P.round_people(raw) == shown


def test_a_handful_of_people_is_never_rounded_to_nobody():
    assert P.round_people(0.2) == 1


# ------------------------------------------------------------------ range

def test_two_models_give_a_range():
    assert P.as_range({"ghsl": 8900, "worldpop": 12400}) == {
        "low": 8900, "high": 12400, "by_source": {"ghsl": 8900, "worldpop": 12400}}


def test_one_missing_model_still_gives_an_answer():
    assert P.as_range({"ghsl": 8900, "worldpop": None})["low"] == 8900


def test_no_model_gives_no_answer_rather_than_zero():
    assert P.as_range({"ghsl": None, "worldpop": None}) is None


# ----------------------------------------------------------------- caveat

def test_the_caveat_says_what_the_figure_is_and_which_way_it_errs():
    text = P.caveat([{"label": "GHSL", "year": 2020}, {"label": "WorldPop", "year": 2021}],
                    missed_fraction=0.29)
    assert "people living in the flooded area" in text
    assert "not of people displaced or harmed" in text
    assert "GHSL 2020 and WorldPop 2021" in text
    assert "more likely too low than too high" in text
    assert "29%" in text
    assert text.count(". ") == 0, "one sentence - every note must survive into the report"


# ------------------------------------------------- the four-fold undercount

class Recorder:
    """Stands in for an ee.Image and records how it was reduced."""

    def __init__(self):
        self.calls = []

    def reduceRegion(self, **kwargs):
        self.calls.append(("reduceRegion", kwargs))
        return {}

    def reduceRegions(self, **kwargs):
        self.calls.append(("reduceRegions", kwargs))
        return {}


@pytest.mark.parametrize("call", ["total", "per_feature"])
def test_population_is_summed_on_its_own_grid_never_at_a_scale(call):
    """Summing a 100 m count raster at the 200 m analysis scale averages four
    cells and sums the averages - about a four-fold undercount that still
    looks plausible. Every population sum must name the dataset's projection
    and must not pass a scale."""
    image, projection = Recorder(), object()
    if call == "total":
        P.sum_over(image, "geometry", projection, reducer="SUM")
    else:
        P.sum_per_feature(image, "features", projection, reducer="SUM")

    (_, kwargs), = image.calls
    assert kwargs["crs"] is projection
    assert "scale" not in kwargs
    assert "crsTransform" not in kwargs


def test_the_module_never_reduces_population_with_a_scale():
    """Belt and braces: the source must not contain a scaled reduction."""
    source = (BACKEND / "geo" / "population.py").read_text(encoding="utf-8")
    body = source.split("def sum_over")[1]
    assert "scale=" not in body.split("def exposure")[0]


# --------------------------------------------------- through the flood path

FAKE = {
    "people_in_flood": {"low": 8900, "high": 12400,
                        "by_source": {"ghsl": 8900, "worldpop": 12400}},
    "per_zone": {"Z1": {"low": 3100, "high": 4500, "by_source": {"ghsl": 3100, "worldpop": 4500}}},
    "sources": [{"key": "ghsl", "label": "GHSL", "year": 2020, "id": P.GHSL},
                {"key": "worldpop", "label": "WorldPop", "year": 2018, "id": P.WORLDPOP}],
    "caveat": P.caveat([{"label": "GHSL", "year": 2020}, {"label": "WorldPop", "year": 2018}], 0.29),
    "scale_note": "summed on each population dataset's own grid",
}


def run_with(monkeypatch, value):
    import flood_scenario as S
    from pipeline import analysis

    region = S.install(monkeypatch)
    seen = {}

    def fake_exposure(flood_mask, region, zones, event_date, analysis_scale, missed_fraction=None):
        seen.update(event_date=event_date, scale=analysis_scale, missed=missed_fraction,
                    zones=[z["id"] for z in zones])
        return value

    monkeypatch.setattr(P, "exposure", fake_exposure)
    result = analysis.analyse_flood(region, dict(S.META), *S.POST,
                                    force_sensor="sentinel-1", scale=200)
    return result, seen


def ev(result):
    return {e["quantity"]: e for e in result["evidence"]}


def test_people_become_evidence_one_record_per_model(monkeypatch):
    result, _ = run_with(monkeypatch, FAKE)
    e = ev(result)
    assert e["population_in_flood_extent_ghsl"]["value"] == 8900
    assert e["population_in_flood_extent_worldpop"]["value"] == 12400
    assert e["population_in_flood_extent_ghsl"]["unit"] == "people"
    assert e["population_in_flood_extent_ghsl"]["derived_from"] == [e["flood_extent"]["id"]]


def test_the_flood_path_asks_for_people_at_its_own_scale_and_error(monkeypatch):
    _, seen = run_with(monkeypatch, FAKE)
    assert seen["scale"] == 200
    assert seen["event_date"] == "2018-08-01"
    assert seen["zones"] == ["Z1"]
    assert seen["missed"] == pytest.approx(1 - 0.574), "from the rule's measured recall"


def test_each_zone_carries_its_people(monkeypatch):
    result, _ = run_with(monkeypatch, FAKE)
    assert result["zones"][0]["population"]["high"] == 4500
    assert "geometry" not in result["zones"][0]


def test_the_caveat_and_sources_travel_with_the_result(monkeypatch):
    result, _ = run_with(monkeypatch, FAKE)
    assert FAKE["caveat"] in result["unobserved"]["notes"]
    roles = [d["role"] for d in result["provenance"]["datasets"]]
    assert "population (GHSL 2020)" in roles
    assert result["population"]["people_in_flood"]["low"] == 8900
    assert "per_zone" not in result["population"]


def test_a_population_failure_never_costs_the_flood_result(monkeypatch):
    result, _ = run_with(monkeypatch, {"error": "population could not be computed: quota"})
    assert ev(result)["flood_extent"]["value"] == 64.0
    assert "population_in_flood_extent_ghsl" not in ev(result)
    assert "quota" in result["population"]["error"]


def test_the_template_report_states_the_range_and_passes_verification(monkeypatch):
    """The sentence a user reads, checked by the same verifier as everything else."""
    from pipeline import report

    result, _ = run_with(monkeypatch, FAKE)
    text, verification = report.build_report(result, prefer_llm=False)
    assert "8,900 to 12,400 people live in the flooded area" in text["text"]
    assert verification["passed"], verification["unsupported_claims"]
    assert verification["caveats"]["completeness"] == 1.0


def test_zone_people_given_to_the_model_are_verifiable(monkeypatch):
    from pipeline import report

    result, _ = run_with(monkeypatch, FAKE)
    assert "people living there 3100 to 4500" in report._build_prompt(result)
    extra = report.collect_extra_values(result)
    assert extra["zone_Z1_people_high"] == 4500
