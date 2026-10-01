"""
Group A together, through the real endpoint: latest pass, people, districts
and the India clause in one flood answer - then the verified report and the
PDF a district official would forward.

Earth Engine is replaced by the numpy flood scenario and by fixed population
and district figures, so every number below is known in advance.
"""

import json
import sys
from io import BytesIO
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

pytest.importorskip("ee")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

POPULATION = {
    "people_in_flood": {"low": 8900, "high": 12400,
                        "by_source": {"ghsl": 8900, "worldpop": 12400}},
    "per_zone": {"Z1": {"low": 3100, "high": 4500,
                        "by_source": {"ghsl": 3100, "worldpop": 4500}}},
    "sources": [{"key": "ghsl", "label": "GHSL", "year": 2020, "id": "JRC/GHSL/P2023A/GHS_POP"},
                {"key": "worldpop", "label": "WorldPop", "year": 2018, "id": "WorldPop/GP/100m/pop"}],
    "scale_note": "summed on each population dataset's own grid",
}


@pytest.fixture
def app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import flood_scenario as S
    import main
    from core import cache
    from detection import sar
    from geo import districts as D
    from geo import population as P

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    region = S.install(monkeypatch)
    monkeypatch.setattr(main, "resolve_area_or_fail", lambda payload: (region, dict(S.META)))

    # Latest pass found on 1 Aug 2018, with the passes before it.
    monkeypatch.setattr(sar, "latest_acquisition", lambda region: {
        "date": "2018-08-01", "relative_orbit": 63,
        "recent_passes": ["2018-07-08", "2018-07-20", "2018-08-01"],
        "polarisations": "VV+VH", "lookback_days": 60})

    # Scale-aware rule, with an India figure filled in as notebook 08 would.
    india = {"event": "Sen1Floods11 India event (2016)", "chips": 21,
             "iou": 0.58, "precision": 0.8, "recall": 0.68, "ci95": [0.47, 0.67]}
    real = S.fake_sar_detect

    def detect(region, start_date, end_date, **kwargs):
        mask, composite, info = real(region, S.POST[0], end_date, **kwargs)
        info.update(threshold=-18.5, threshold_scale_m=200, threshold_scale_exact=True,
                    acquisition_days=[start_date],
                    validation=dict(sar.SCALE_RULES[200]["validation"], india=india))
        return mask, composite, info

    monkeypatch.setattr(sar, "detect_water", detect)

    def exposure(flood_mask, region, zones, event_date, scale, missed_fraction=None):
        return dict(POPULATION, caveat=P.caveat(
            [{"label": "GHSL", "year": 2020}, {"label": "WorldPop", "year": 2018}],
            missed_fraction))

    monkeypatch.setattr(P, "exposure", exposure)

    km2 = 1e6
    raw = [
        {"name": "Alappuzha", "state": "Testland", "flooded_m2": 40 * km2,
         "observed_m2": 200 * km2, "in_region_m2": 200 * km2},
        {"name": "Thrissur", "state": "Testland", "flooded_m2": 24 * km2,
         "observed_m2": 160 * km2, "in_region_m2": 200 * km2},
    ]
    people = {D.district_key("Alappuzha", "Testland"): {"low": 6000, "high": 8000}}

    def breakdown(flood_mask, valid, region, meta, scale, flood_km2, event_date=None):
        rows = D.build_rows(raw, people)
        return {"kind": "state_breakdown", "rows": rows,
                "sum_check": D.sum_check(rows, flood_km2),
                "note": D.coverage_note(rows), "boundary_source": "FAO/GAUL/2015/level2 (2015)"}

    monkeypatch.setattr(D, "breakdown", breakdown)
    return TestClient(main.app)


def ask_latest(app):
    response = app.post("/analyze", json={"region": "testland", "latest": True,
                                          "scale": 200, "use_llm": False})
    assert response.status_code == 200, response.text
    return response.json()


def test_one_answer_carries_all_four_group_a_features(app):
    body = ask_latest(app)

    # A3: the newest pass, its date and its age
    assert body["period"]["post"]["start"] == "2018-08-01"
    assert body["acquisition"]["last"] == "2018-08-01"
    assert body["acquisition"]["age_days"] > 2900
    assert body["latest"]["next_pass"]["expected_on"] == "2018-08-13"

    # A1: people, overall and per zone
    assert body["population"]["people_in_flood"] == {
        "low": 8900, "high": 12400, "by_source": {"ghsl": 8900, "worldpop": 12400}}
    assert body["zones"][0]["population"]["high"] == 4500

    # A2: districts, worst first, with the partly seen one flagged
    names = [r["name"] for r in body["districts"]["rows"]]
    assert names == ["Alappuzha", "Thrissur"]
    assert body["districts"]["rows"][1]["low_coverage"] is True

    # A9: the India clause inside the accuracy note
    assert any("held-out Indian chips (21 chips" in n for n in body["unobserved"]["notes"])

    # The scale-aware threshold, named in the method
    extent = next(e for e in body["evidence"] if e["quantity"] == "flood_extent")
    assert "-18.5 dB for 200 m analysis" in extent["method"]


def test_the_report_covering_all_of_it_still_verifies(app):
    body = ask_latest(app)
    text = body["report"]["text"]
    assert "8,900 to 12,400 people live in the flooded area" in text
    assert "The most affected districts are Alappuzha (40.0 km2)" in text
    assert body["verification"]["passed"], body["verification"]["unsupported_claims"]
    assert body["verification"]["caveats"]["completeness"] == 1.0


def test_the_pdf_carries_people_districts_and_image_date(app):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    body = ask_latest(app)

    pdf = app.get(f"/analyze/{body['request_id']}/report.pdf")
    assert pdf.status_code == 200
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(BytesIO(pdf.content)).pages)

    assert "People living in the flooded area" in text
    assert "8,900 to 12,400 people" in text
    assert "Districts, worst first" in text
    assert "Alappuzha" in text and "partly seen" in text
    assert "When the images were taken" in text
    assert "2018-08-01" in text
    assert "held-out Indian chips" in text


def test_what_the_visual_check_caught_stays_fixed(app):
    """Found by rendering the PDF and reading it: singular grammar, the zone
    table's missing People column, and a '5e-05 km' scale bar."""
    pypdf = pytest.importorskip("pypdf")
    body = ask_latest(app)
    text = body["report"]["text"]
    assert "1 of 2 districts was less than" in text
    assert "falls into 1 distinct zone [" in text

    pdf = app.get(f"/analyze/{body['request_id']}/report.pdf").content
    pages = "\n".join(p.extract_text() for p in pypdf.PdfReader(BytesIO(pdf)).pages)
    assert "3,100 to 4,500" in pages, "zone people in the PDF zone table"
    assert "e-0" not in pages, "no scientific-notation scale bar"
