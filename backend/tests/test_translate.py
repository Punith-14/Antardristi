"""
D4: the report in Hindi, verified after translation.

Hindi is a translation of the VERIFIED English, and is checked against it:
every sentence back once and in order (so every caveat), the same numbers
(Devanagari digits and Indian grouping read correctly), the same [E#]
citations, and the fixed terms. A translation that fails is not shown.
"""

import json
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from pipeline import translate as T  # noqa: E402

ENGLISH = (
    "Flood water covered 663.1 km2 of Kerala [E1], equal to 1.91% of the area that could be "
    "observed [E2]. The detected area falls into 92 distinct zones [E4]; the largest covers "
    "84.5 km2 [E5], centred near 9.51 N 76.40 E. An estimated 17,800 to 109,100 people live in "
    "the flooded area [E7][E8]. Rivers and ponds present year-round, totalling 273.5 km2, were "
    "excluded [E3]. This detection method scores IoU 0.609, precision 0.807 and recall 0.713."
)

GOOD = [
    "केरल के 663.1 km2 क्षेत्र में बाढ़ का पानी था [E1], जो अवलोकन योग्य क्षेत्र का 1.91% है [E2]।",
    "पता चला बाढ़ क्षेत्र 92 अलग क्षेत्र खंडों में है [E4]; सबसे बड़ा 84.5 km2 का है [E5], जो 9.51 N 76.40 E के पास है।",
    "अनुमानित 17,800 से 1,09,100 लोग बाढ़ग्रस्त क्षेत्र में रहते हैं [E7][E8]।",
    "साल भर मौजूद नदियाँ और तालाब, कुल 273.5 km2, हटा दिए गए [E3]।",
    "यह विधि IoU 0.609, परिशुद्धता 0.807 और रिकॉल 0.713 स्कोर करती है।",
]


def marked(lines):
    return "\n".join(f"<<S{i}>> {line}" for i, line in enumerate(lines, 1))


def test_sentences_split_without_breaking_decimals_coordinates_or_citations():
    sentences = T.split_sentences(ENGLISH)
    assert len(sentences) == 5
    assert sentences[1].endswith("centred near 9.51 N 76.40 E.")
    assert "[E7][E8]" in sentences[2]


def test_numbers_read_devanagari_digits_and_indian_grouping():
    assert T.numbers_in("१,०९,१०० लोग और ८४.५ km2") == [84.5, 109100.0]
    assert T.numbers_in("109,100 people and 84.5 km2") == [84.5, 109100.0]
    assert T.numbers_in("[E12] only a citation") == []


def test_a_faithful_translation_passes():
    sentences = T.split_sentences(ENGLISH)
    assert T.check(sentences, T.parse_marked(marked(GOOD))) == []


@pytest.mark.parametrize("change,why", [
    (lambda g: [g[0].replace("663.1", "663")] + g[1:], "numbers"),
    (lambda g: g[:2] + g[3:], "missing"),
    (lambda g: [g[0].replace("[E2]", "")] + g[1:], "citations"),
    (lambda g: g[:4] + ["This method scores IoU 0.609, precision 0.807 and recall 0.713."], "not in Hindi"),
    (lambda g: g[:4] + [g[4].replace("परिशुद्धता", "सटीकता")], "'precision' should be"),
    (lambda g: [g[0].replace("बाढ़", "जल")] + g[1:], "'flood' should be"),
])
def test_each_kind_of_unfaithful_translation_is_caught(change, why):
    sentences = T.split_sentences(ENGLISH)
    lines = change(list(GOOD))
    failures = T.check(sentences, T.parse_marked(marked(lines)))
    assert any(why in f for f in failures), failures


def test_a_duplicated_or_invented_sentence_is_caught():
    sentences = T.split_sentences(ENGLISH)
    text = marked(GOOD) + "\n<<S2>> " + GOOD[1] + "\n<<S9>> अतिरिक्त वाक्य।"
    failures = T.check(sentences, T.parse_marked(text))
    assert any("appears 2 times" in f for f in failures)
    assert any("not in the English: [9]" in f for f in failures)


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.prompts.append(request["messages"][-1]["content"])
        reply = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


def test_one_retry_is_told_what_failed():
    bad = marked([GOOD[0].replace("663.1", "663")] + GOOD[1:])
    client = FakeClient([bad, marked(GOOD)])
    result = T.to_hindi(ENGLISH, client=client, model="test-model")
    assert result["available"] and result["attempts"] == 2
    assert "rejected because" in client.prompts[1] and "663.1" in client.prompts[1]
    assert result["text"].startswith("केरल के 663.1 km2")


def test_two_failures_mean_no_hindi_and_the_reason():
    bad = marked(GOOD[:3])
    result = T.to_hindi(ENGLISH, client=FakeClient([bad, bad]), model="test-model")
    assert result["available"] is False
    assert "did not pass verification" in result["reason"]
    assert "text" not in result


def test_compound_is_refused():
    with pytest.raises(ValueError, match="searches the web"):
        T.to_hindi(ENGLISH, client=FakeClient([]), model="groq/compound")


def test_the_fixed_terms_are_the_glossarys_terms():
    glossary = (BACKEND.parent / "frontend" / "src" / "lib" / "glossary.js").read_text(encoding="utf-8")
    for english, hindi in T.TERMS.items():
        assert hindi in glossary, f"{english} = {hindi} is not in glossary.js"


# ------------------------------------------------------------- endpoint, PDF

@pytest.fixture
def app(tmp_path, monkeypatch):
    pytest.importorskip("ee")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import flood_scenario as S
    import main
    from core import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    region = S.install(monkeypatch)
    monkeypatch.setattr(main, "resolve_area_or_fail", lambda payload: (region, dict(S.META)))
    client = TestClient(main.app)
    body = client.post("/analyze", json={"region": "testland", "post_start": S.POST[0],
                                         "post_end": S.POST[1], "use_llm": False}).json()
    return client, body, main


def test_the_endpoint_translates_the_stored_report_once(app, monkeypatch):
    client, body, _ = app
    english = body["report"]["text"]
    calls = []

    def fake(text, **kwargs):
        calls.append(text)
        return {"available": True, "text": "बाढ़ का पानी।", "checks": [], "model": "m", "attempts": 1}

    monkeypatch.setattr(T, "to_hindi", fake)
    first = client.get(f"/analyze/{body['request_id']}/report/hi").json()
    second = client.get(f"/analyze/{body['request_id']}/report/hi").json()
    assert first["available"] and first["text"] == "बाढ़ का पानी।"
    assert first["source"] == "machine translation of the verified English report"
    assert second == first and calls == [english], "translated once, from the stored English"


def test_an_unverified_report_is_not_translated(app, monkeypatch):
    client, body, main = app
    from core import cache
    path = cache.CACHE_DIR / f"{body['request_id']}.json"
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["response"]["verification"]["passed"] = False
    path.write_text(json.dumps(entry), encoding="utf-8")
    monkeypatch.setattr(T, "to_hindi", lambda *a, **k: pytest.fail("must not be called"))
    result = client.get(f"/analyze/{body['request_id']}/report/hi").json()
    assert result["available"] is False and "did not pass verification" in result["reason"]


def test_the_hindi_pdf_carries_the_shaped_hindi_beside_the_english(app, monkeypatch):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    from pipeline import export_pdf
    client, body, _ = app
    monkeypatch.setattr(T, "to_hindi", lambda text, **k: {
        "available": True, "text": "केरल में बाढ़ का विस्तार 64.0 km2 था [E1]।", "checks": [],
        "model": "m", "attempts": 1})
    font, problem = export_pdf.devanagari_font()
    pdf = client.get(f"/analyze/{body['request_id']}/report.pdf?lang=hi").content
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(BytesIO(pdf)).pages).replace("\n", " ")
    assert "Finding" in text, "the English finding is still there"
    if font is None:
        assert "Hindi finding not included" in text and problem.split()[0] in text
    else:
        assert "Machine translation of the verified English finding" in text
        assert "????" not in text, "Devanagari replaced by '?' - the code-page filter ran on it"
        assert b"Devanagari" in pdf or b"Poppins" in pdf or b"Nirmala" in pdf


def test_without_a_font_the_pdf_says_why_rather_than_printing_broken_hindi(app, monkeypatch):
    pypdf = pytest.importorskip("pypdf")
    from pipeline import export_pdf
    client, body, _ = app
    monkeypatch.setattr(export_pdf, "_HINDI_FONT", {"name": None, "reason": "no Devanagari font found"})
    monkeypatch.setattr(T, "to_hindi", lambda text, **k: {
        "available": True, "text": "बाढ़।", "checks": [], "model": "m", "attempts": 1})
    pdf = client.get(f"/analyze/{body['request_id']}/report.pdf?lang=hi").content
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(BytesIO(pdf)).pages).replace("\n", " ")
    assert "Hindi finding not included: no Devanagari font found" in text
