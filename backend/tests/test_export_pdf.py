"""
PDF export.

The PDF is the answer that leaves the system. Once it is an email attachment
nobody can click a citation or see the verification badge, so the tests here
are about what survives the trip onto paper: every evidence row, every
caveat, and above all a failure - printed louder than a success.

Most tests check export_pdf.outline(), the plain-data description of the
document, so they run without parsing PDF bytes. A few render and extract the
text, to confirm the drawing layer does not drop what the outline contains.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from pipeline import export_pdf  # noqa: E402

STORED = BACKEND / "results" / "api_response.json"


@pytest.fixture
def result():
    """A real stored response: Kerala, August 2018, Sentinel-1."""
    if not STORED.exists():
        pytest.skip("results/api_response.json not present")
    return json.loads(STORED.read_text(encoding="utf-8"))


def failed(result):
    """The same response with verification failing in every way it can."""
    bad = copy.deepcopy(result)
    bad["verification"].update(
        passed=False,
        claims_supported=11,
        faithfulness_rate=0.9167,
        unsupported_claims=[{"claim": "512.0"}],
        invalid_citations=["E9"],
    )
    bad["verification"]["caveats"] = {
        "caveats_required": 1, "caveats_mentioned": 0, "completeness": 0.0,
        "omitted_caveats": ["Roughly a quarter of detected pixels are false positives."],
        "passed": False,
    }
    return bad


# ------------------------------------------------------------ the status

def test_a_verified_analysis_says_so(result):
    status = export_pdf.outline(result)["status"]
    assert status["level"] == "ok"
    assert "Verified" in status["headline"]


def test_a_failed_verification_is_the_headline_not_a_footnote(result):
    """The most misleading thing this module could produce is a clean-looking
    PDF of an unverified answer."""
    status = export_pdf.outline(failed(result))["status"]

    assert status["level"] == "warn"
    assert "FAILED" in status["headline"]
    joined = " ".join(status["lines"])
    assert "512.0" in joined, "the unsupported number must be named"
    assert "E9" in joined, "the invalid citation must be named"
    assert "false positives" in joined, "the dropped caveat must be named"


def test_problems_are_listed_before_the_scores(result):
    """Someone skimming reads the first line. On a failure that line should
    be what went wrong, not "11 of 12 traced", which sounds fine."""
    lines = export_pdf.outline(failed(result))["status"]["lines"]
    assert lines[0].startswith("Unsupported number")


def test_a_question_mismatch_fails_the_status_even_if_the_numbers_verify(result):
    """May-vs-May: every number faithful, the answer to a different question.
    That passed 16 of 16 on screen. It must not pass on paper."""
    mismatched = copy.deepcopy(result)
    mismatched["alignment"] = {
        "passed": False,
        "failures": ["The baseline window is identical to the analysis window."],
        "checks": [],
    }
    status = export_pdf.outline(mismatched)["status"]

    assert status["level"] == "warn"
    assert any("identical" in line for line in status["lines"])


def test_no_report_is_not_printed_as_a_pass(result):
    """Nothing verified is different from verified. Printing a green banner
    over an analysis whose prose was never checked would be the same failure
    as printing one over prose that failed."""
    unreported = copy.deepcopy(result)
    unreported.pop("verification")
    unreported.pop("report")

    status = export_pdf.outline(unreported)["status"]
    assert status["level"] == "none"
    assert "Not verified" in status["headline"]


def test_the_template_fallback_is_disclosed(result):
    fell_back = copy.deepcopy(result)
    fell_back["report"]["fallback_used"] = True
    fell_back["report"]["fallback_reason"] = "rate limited"

    lines = export_pdf.outline(fell_back)["status"]["lines"]
    assert any("template" in line and "rate limited" in line for line in lines)


def test_uncited_evidence_is_mentioned(result):
    """The zone-count citation misdirection showed up here: a record that the
    text should have cited and did not."""
    lines = export_pdf.outline(result)["status"]["lines"]
    uncited = result["verification"].get("uncited_evidence") or []
    if uncited:
        assert any(all(e in line for e in uncited) for line in lines)


# ----------------------------------------------------------- the content

def test_every_evidence_record_is_in_the_table(result):
    rows = export_pdf.outline(result)["evidence"]
    assert [r["id"] for r in rows] == [e["id"] for e in result["evidence"]]


def test_values_are_printed_as_stored_not_rounded(result):
    """The finding quotes 486.4 km2 and 25.73 km2. If the table rounded to one
    decimal, 25.73 would print as 25.7 beside a sentence saying 25.73 - two
    numbers for one quantity on the same page."""
    rows = {r["id"]: r for r in export_pdf.outline(result)["evidence"]}
    for record in result["evidence"]:
        value = record["value"]
        if isinstance(value, float):
            assert repr(value) in rows[record["id"]]["value"].replace(",", ""), (
                f"{record['id']}: {value} printed as {rows[record['id']]['value']}"
            )


def test_derived_records_say_what_they_came_from(result):
    rows = {r["id"]: r for r in export_pdf.outline(result)["evidence"]}
    for record in result["evidence"]:
        for parent in record.get("derived_from") or []:
            assert parent in rows[record["id"]]["detail"]


def test_every_caveat_travels_with_the_numbers(result):
    doc = export_pdf.outline(result)
    assert doc["caveats"] == result["unobserved"]["notes"]
    assert doc["provenance"]["known_confusions"] == result["provenance"]["known_confusions"]


def test_a_truncated_zone_list_says_it_is_truncated(result):
    note = export_pdf.outline(result)["zones"]["note"]
    summary = result["zones_summary"]
    assert str(summary["count"]) in note
    assert str(summary["listed"]) in note


def test_nothing_observed_is_not_a_finding_of_absence():
    doc = export_pdf.outline({
        "region": {"name": "Assam"},
        "unobserved": {"reason": "no_usable_imagery", "notes": ["Cloud."]},
    })
    assert doc["nothing_observed"] is True


# ------------------------------------------------------- the drawn area

def test_a_drawn_area_is_not_presented_as_a_district():
    doc = export_pdf.outline({
        "region": {
            "name": "user-defined polygon",
            "admin_level": "custom",
            "boundary_source": "user-supplied",
            "boundary_vintage": None,
            "note": "This is an area drawn by the user, not an administrative boundary.",
            "footprint": {"kind": "polygon", "points": 5, "approx_area_km2": 812.4},
        },
    })
    region = dict(doc["region"])

    assert region["Kind"] == "drawn by the user"
    assert "not an administrative boundary" in region["Boundary vintage"]
    assert "State" not in region, "a drawn area has no state and must not claim one"
    assert "812.4" in region["Drawn area"]


# ----------------------------------------------------------- text safety

@pytest.mark.parametrize("raw,expected", [
    ("486.4 km²", "486.4 km²"),        # narrow no-break space, from the LLM
    ("Sentinel‑1", "Sentinel-1"),       # non-breaking hyphen, from the LLM
    ("−20 dB", "-20 dB"),               # a minus sign must keep its sign
    ("≈ 25%", "~ 25%"),
])
def test_characters_the_font_cannot_draw_are_normalised(raw, expected):
    assert export_pdf.safe(raw) == expected


def test_unknown_characters_become_visible_not_invisible():
    """A dropped character in a number is a different number. A '?' at least
    shows something is missing."""
    assert export_pdf.safe("5₿") == "5?"


def test_markup_characters_in_the_report_are_escaped():
    """reportlab's Paragraph parses markup. An unescaped '<' in
    "recall < 0.6" either raises or eats the rest of the sentence - and the
    rest of the sentence is often the caveat."""
    assert export_pdf.para("recall < 0.6 & more") == "recall &lt; 0.6 &amp; more"


def test_safe_survives_none_and_numbers():
    assert export_pdf.safe(None) == ""
    assert export_pdf.safe(0.441) == "0.441"


# ------------------------------------------------------------- rendering

reportlab = pytest.importorskip("reportlab")


def _text(pdf_bytes):
    pypdf = pytest.importorskip("pypdf")
    from io import BytesIO
    reader = pypdf.PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_it_renders_a_pdf(result):
    pdf = export_pdf.render(result, exported_at="2026-09-29 10:00 UTC")
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 5000


def test_the_rendered_pdf_contains_every_evidence_value(result):
    """The outline being right is no use if the drawing layer drops a row."""
    text = _text(export_pdf.render(result)).replace("\n", " ")
    for row in export_pdf.outline(result)["evidence"]:
        number = row["value"].split()[0].rstrip("%")
        assert number in text, f"{row['id']} value {row['value']} missing from the PDF"


def test_the_rendered_pdf_carries_the_request_id_on_every_page(result):
    """A page separated from the others must still say where it came from."""
    pypdf = pytest.importorskip("pypdf")
    from io import BytesIO
    reader = pypdf.PdfReader(BytesIO(export_pdf.render(result)))
    for number, page in enumerate(reader.pages, 1):
        assert result["request_id"] in page.extract_text(), f"page {number}"


def test_the_failure_banner_survives_rendering(result):
    text = _text(export_pdf.render(failed(result)))
    assert "FAILED VERIFICATION" in text
    # First thing after the title block, not buried.
    assert text.index("FAILED VERIFICATION") < text.index("Evidence record")


def test_markup_in_the_report_does_not_eat_the_rest_of_it(result):
    tricky = copy.deepcopy(result)
    tricky["report"]["text"] = "Recall < 0.6 & precision > 0.7. The caveat that follows."
    text = _text(export_pdf.render(tricky))
    assert "The caveat that follows" in text


def test_it_renders_with_almost_nothing():
    """A no-data response, an old cached response, a surface response with
    no zones - none of them may make the export raise."""
    for minimal in ({}, {"region": {"name": "Assam"}},
                    {"unobserved": {"reason": "no_usable_imagery"}}):
        assert export_pdf.render(minimal).startswith(b"%PDF")


def test_the_map_is_labelled_as_a_schematic(result):
    text = _text(export_pdf.render(result))
    assert "Schematic" in text
    assert "not to scale" in text, "circle markers must be disclosed as not to scale"


def test_scale_bar_lengths_are_round_numbers():
    assert export_pdf._nice_length(137) == 100
    assert export_pdf._nice_length(37) == 20
    assert export_pdf._nice_length(6.2) == 5
    assert export_pdf._nice_length(0) == 0


# ---------------------------------------------------------------- naming

def test_the_filename_says_where_and_when(result):
    name = export_pdf.filename_for(result)
    assert name.endswith(".pdf")
    assert "kerala" in name
    assert "20180815" in name


def test_the_filename_cannot_carry_a_path_or_header_break():
    """The name goes into a Content-Disposition header. A slug with a quote,
    slash or newline in it would break the header or write outside the
    download folder on a careless client."""
    name = export_pdf.filename_for({"region": {"slug": 'a/../"b\r\nX'}})
    assert all(c.isalnum() or c in "-_." for c in name), name


# ---------------------------------------------------------- the endpoint

MAIN = (BACKEND / "main.py").read_text(encoding="utf-8")


def _endpoint(header):
    after = MAIN.split(header)[1]
    end = after.find("@app.")
    return after if end == -1 else after[:end]


def test_the_endpoint_renders_from_the_stored_response_only():
    """No Earth Engine and no model call on export: the PDF is a view of the
    evidence record and must not be able to disagree with it."""
    body = _endpoint("def analysis_pdf(")
    assert "cache.get_by_id(request_id)" in body
    for forbidden in ("analyze(", "build_report(", "earth_engine", "sar."):
        assert forbidden not in body, f"export must not call {forbidden}"


def test_a_missing_reportlab_is_a_clear_501_not_a_traceback():
    body = _endpoint("def analysis_pdf(")
    assert "ImportError" in body
    assert "501" in body
    assert "pip install reportlab" in body


def test_reportlab_is_declared():
    requirements = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
    assert "reportlab" in requirements


# ------------------------------------------------ live, through FastAPI

@pytest.fixture
def client(tmp_path, monkeypatch, result):
    """The real app, with the cache pointed at an empty temporary folder."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from core import cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)

    import main

    analysis_key = {"kind": "test-analysis"}
    request_id = cache.key_for(analysis_key)
    stored = {k: v for k, v in result.items() if k not in ("routing", "alignment")}
    cache.put(analysis_key, stored)

    return TestClient(main.app), request_id, cache


def _ask_record(cache, request_id, passed):
    payload = {"kind": "ask", "question": "flood in Aug vs May", "request_id": request_id}
    cache.put(payload, {
        "question": "How much did flooding increase in August 2018 compared to May 2018?",
        "request_id": request_id,
        "routing": {},
        "alignment": {
            "passed": passed,
            "failures": [] if passed else [
                "The baseline window is identical to the analysis window, "
                "so any change figure will be exactly zero by construction."
            ],
            "checks": [],
        },
    })
    return cache.key_for(payload)


def test_the_endpoint_returns_a_named_pdf(client):
    app, request_id, _ = client
    response = app.get(f"/analyze/{request_id}/report.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


def test_an_unknown_analysis_is_a_404_that_says_what_to_do(client):
    app, _, _ = client
    response = app.get("/analyze/nope/report.pdf")
    assert response.status_code == 404
    assert "POST /analyze" in response.json()["detail"]


def test_a_mismatched_question_reaches_the_pdf(client):
    """The case that made this endpoint take ask_id at all. Without it the
    stored analysis carries no alignment, and the PDF of a May-vs-May answer
    prints a green banner."""
    app, request_id, cache = client
    ask_id = _ask_record(cache, request_id, passed=False)

    response = app.get(f"/analyze/{request_id}/report.pdf?ask_id={ask_id}")
    assert response.status_code == 200

    text = _text(response.content)
    assert "FAILED VERIFICATION" in text
    assert "identical" in text
    assert "August 2018" in text, "the question itself must be printed"


def test_without_the_question_the_pdf_does_not_claim_to_have_checked_it(client):
    app, request_id, _ = client
    text = _text(app.get(f"/analyze/{request_id}/report.pdf").content)
    assert "Does the analysis answer the question" not in text


def test_one_questions_verdict_cannot_be_printed_on_another_analysis(client):
    app, request_id, cache = client
    ask_id = _ask_record(cache, "some-other-analysis", passed=True)

    response = app.get(f"/analyze/{request_id}/report.pdf?ask_id={ask_id}")
    assert response.status_code == 400
    assert "not answered by" in response.json()["detail"]


def test_an_expired_question_is_a_404_not_a_silent_omission(client):
    app, request_id, _ = client
    response = app.get(f"/analyze/{request_id}/report.pdf?ask_id=gone")
    assert response.status_code == 404


def test_ask_stores_the_question_where_the_pdf_can_find_it():
    body = _endpoint("def ask(payload: AskRequest):")
    assert '"kind": "ask"' in body
    assert 'result["ask_id"] = ask_id' in body
    # Stored as its own record, never merged into the shared analysis entry.
    assert "cache.put(ask_payload" in body
