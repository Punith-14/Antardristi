"""
The verification layer is the project's contribution, so it gets the most tests.

Several of these are regression tests named after bugs that actually shipped
during development. Each one cost real debugging time; none should return.
"""

import pytest

from core.verification import check_caveats, normalise, summarise, verify_report


# ------------------------------------------------------------ core behaviour

def test_supported_claim_passes(evidence):
    text = "Flooding covered 187.4 km2 [E1]."
    result = verify_report(text, evidence)
    assert result["faithfulness_rate"] == 1.0
    assert result["claims_supported"] == 1
    assert result["passed"]


def test_invented_number_is_flagged(evidence):
    text = "Flooding covered 187.4 km2 [E1] and displaced 45000 people."
    result = verify_report(text, evidence)
    assert not result["passed"]
    assert "45000" in [c["claim"] for c in result["unsupported_claims"]]


def test_citation_to_nonexistent_evidence_is_flagged(evidence):
    result = verify_report("There were 22 casualties [E99].", evidence)
    assert "E99" in result["invalid_citations"]
    assert not result["passed"]


def test_the_hallucinated_report_we_tested_with(evidence):
    """The exact fabrication used during development. Four invented figures
    and one invalid citation - all five must be caught."""
    text = (
        "Flooding affected 187.4 km2 of Kendrapara district [E1], displacing "
        "approximately 45000 residents and submerging 312 villages. Water depth "
        "reached 3.5 metres. The district recorded 22 casualties [E9]."
    )
    result = verify_report(text, evidence)

    flagged = {c["claim"] for c in result["unsupported_claims"]}
    assert {"45000", "312", "3.5", "22"} <= flagged
    assert result["invalid_citations"] == ["E9"]
    assert result["faithfulness_rate"] < 0.5


def test_empty_report_is_vacuously_faithful(evidence):
    result = verify_report("", evidence)
    assert result["faithfulness_rate"] == 1.0
    assert result["claims_total"] == 0


# --------------------------------------------------------------- regressions

def test_unicode_dashes_do_not_break_matching():
    """REGRESSION: the model wrote typographically correct output - en-dashes
    for minus signs - and the verifier read '-10.99' as positive 10.99, then
    reported a perfectly correct statement as a hallucination.

    A verifier that cries wolf gets ignored, and then it misses the real one.
    """
    evidence = [
        {"id": "E1", "quantity": "threshold", "value": -10.99, "unit": "dB"},
    ]
    for dash in ["-", "–", "—", "−", "‐"]:
        text = f"Otsu returned {dash}10.99 dB [E1]."
        result = verify_report(text, evidence)
        assert result["passed"], f"failed on U+{ord(dash):04X}"


def test_negative_numbers_are_parsed_with_their_sign():
    """REGRESSION: SAR thresholds are negative. Dropping the minus made -17
    match evidence of +17, and fail to match evidence of -17."""
    evidence = [{"id": "E1", "quantity": "threshold", "value": -17.0, "unit": "dB"}]

    assert verify_report("Threshold -17.0 dB [E1].", evidence)["passed"]
    assert not verify_report("Threshold 17.0 dB [E1].", evidence)["passed"]


def test_sensor_names_are_not_read_as_claims():
    """REGRESSION: 'Sentinel-1 radar' reported a spurious unsupported claim of
    '1', because the digit in an identifier looked like a measurement."""
    evidence = [{"id": "E1", "quantity": "area", "value": 486.4, "unit": "km2"}]
    text = (
        "Measured over 486.4 km2 [E1] using Sentinel-1 VV from COPERNICUS/S1_GRD, "
        "cross-checked against Sentinel-2 and JRC/GSW1_4."
    )
    assert verify_report(text, evidence)["passed"]


def test_iso_dates_are_not_read_as_claims():
    evidence = [{"id": "E1", "quantity": "area", "value": 486.4, "unit": "km2"}]
    text = "Between 2018-08-15 and 2018-08-25, 486.4 km2 was flooded [E1]."
    assert verify_report(text, evidence)["passed"]


def test_plain_years_are_ignored():
    evidence = [{"id": "E1", "quantity": "area", "value": 486.4, "unit": "km2"}]
    assert verify_report("In 2018, 486.4 km2 flooded [E1].", evidence)["passed"]


# ---------------------------------------------------------------- tolerance

@pytest.mark.parametrize("written", ["187.4", "187.40", "187,4".replace(",", "."), "187.42"])
def test_rounding_in_prose_is_tolerated(written):
    evidence = [{"id": "E1", "quantity": "area", "value": 187.42, "unit": "km2"}]
    assert verify_report(f"{written} km2 [E1].", evidence)["passed"]


def test_thousands_separators_are_understood():
    evidence = [{"id": "E1", "quantity": "area", "value": 49002.0, "unit": "km2"}]
    assert verify_report("Vegetation covered 49,002.0 km2 [E1].", evidence)["passed"]


def test_a_genuinely_different_number_is_not_tolerated():
    evidence = [{"id": "E1", "quantity": "area", "value": 187.4, "unit": "km2"}]
    assert not verify_report("200.0 km2 [E1].", evidence)["passed"]


# ---------------------------------------------------------- extra values

def test_pipeline_computed_values_can_be_quoted(evidence):
    """Coverage and scene counts are computed by the pipeline, so quoting them
    is verifiable even though they are not evidence items."""
    text = "Only 91.2% of the region was observed."
    assert not verify_report(text, evidence)["passed"]
    assert verify_report(text, evidence, extra_values={"coverage": 91.2})["passed"]


# ------------------------------------------------------------- completeness

def test_completeness_catches_omission():
    """REGRESSION and finding: a report scored 100% faithfulness while silently
    dropping the caveat that the measurement was unreliable.

    Fabrication and omission are different failures. Faithfulness alone lets a
    report mislead by silence.
    """
    notes = [
        "The adaptive threshold was overruled by a physical bound, so the "
        "extent is less reliable."
    ]

    kept = (
        "Flooding covered 187.4 km2. The adaptive threshold was overruled by a "
        "physical bound, so the extent is less reliable."
    )
    dropped = "Flooding covered 187.4 km2."

    assert check_caveats(kept, notes)["completeness"] == 1.0
    assert check_caveats(dropped, notes)["completeness"] == 0.0
    assert not check_caveats(dropped, notes)["passed"]


def test_no_caveats_required_is_complete():
    assert check_caveats("Anything.", [])["completeness"] == 1.0
    assert check_caveats("Anything.", None)["passed"]


def test_partial_caveat_coverage_is_measured():
    notes = [
        "Radar shadow behind terrain can read as water.",
        "Flooded vegetation is frequently missed under canopy.",
    ]
    text = "Note that radar shadow behind terrain can read as water."
    result = check_caveats(text, notes)
    assert result["caveats_required"] == 2
    assert result["caveats_mentioned"] == 1
    assert result["completeness"] == 0.5


# ------------------------------------------------------------------ helpers

def test_normalise_preserves_length():
    """Offsets in the result must stay meaningful, so normalisation is
    character-for-character."""
    text = "–10.99 — and −17 with a space"
    assert len(normalise(text)) == len(text)


def test_summarise_reads_sensibly(evidence):
    good = verify_report("187.4 km2 [E1].", evidence)
    assert "traced to evidence" in summarise(good)

    bad = verify_report("999 km2 [E1].", evidence)
    assert "unsupported" in summarise(bad)
