"""Report generation. The LLM path is exercised separately and needs a key."""

import pytest

from report import (
    build_report,
    collect_extra_values,
    render_template_report,
    _strip_thinking,
)
from verification import check_caveats, verify_report


def test_template_report_is_self_consistent(contract):
    """Whatever the template writes must survive its own verifier. If it does
    not, the fallback is worse than useless - it fails the check it exists to
    pass when the model is unavailable."""
    text = render_template_report(contract)
    extra = collect_extra_values(contract)

    result = verify_report(text, contract["evidence"], extra_values=extra)
    caveats = check_caveats(text, contract["unobserved"]["notes"])

    assert result["passed"], result["unsupported_claims"]
    assert caveats["passed"]


def test_template_is_deterministic(contract):
    assert render_template_report(contract) == render_template_report(contract)


def test_template_cites_every_figure(contract):
    text = render_template_report(contract)
    assert "[E1]" in text


def test_no_imagery_is_not_reported_as_no_water():
    """The distinction the whole coverage layer exists to preserve."""
    payload = {
        "region": {"name": "Kendrapara"},
        "evidence": [],
        "observation": {},
        "unobserved": {"reason": "no_usable_imagery"},
        "zones": [],
    }
    text = render_template_report(payload)
    assert "no water" in text.lower()
    assert "could not be observed" in text or "not observed" in text


def test_template_subject_is_not_hardcoded_to_water():
    """REGRESSION: a crop-stress report said 'The water falls into 144 distinct
    zones', because the sentence was written for the flood path."""
    payload = {
        "region": {"name": "Punjab"},
        "observation": {"sensor_used": "sentinel-2"},
        "unobserved": {"notes": []},
        "evidence": [
            {"id": "E1", "quantity": "vegetated_area", "value": 49002.0, "unit": "km2"},
            {"id": "E2", "quantity": "zone_count", "value": 21, "unit": "zones"},
        ],
        "zones": [],
    }
    text = render_template_report(payload)
    assert "water falls into" not in text.lower()
    assert "vegetation" in text.lower()


def test_change_reports_gained_and_lost_separately():
    """A net of zero can hide large offsetting change in different places -
    exactly what happens when a city expands into farmland."""
    payload = {
        "region": {"name": "Bangalore Urban"},
        "observation": {"sensor_used": "sentinel-2"},
        "unobserved": {"notes": []},
        "evidence": [
            {"id": "E1", "quantity": "built_up_area", "value": 2090.8, "unit": "km2"},
            {"id": "E2", "quantity": "net_change", "value": -40.1, "unit": "km2"},
            {"id": "E3", "quantity": "area_gained", "value": 15.0, "unit": "km2"},
            {"id": "E4", "quantity": "area_lost", "value": 55.1, "unit": "km2"},
        ],
        "zones": [],
    }
    text = render_template_report(payload)
    assert "gained" in text.lower()
    assert "lost" in text.lower()


# ------------------------------------------------------- extra values

def test_zone_centroids_are_verifiable(flood_payload):
    """REGRESSION: a report saying 'centred near 30.78 N 75.36 E' failed
    verification, because pipeline-computed coordinates were not whitelisted."""
    extra = collect_extra_values(flood_payload)
    assert 86.42 in extra.values()
    assert 20.51 in extra.values()


def test_numbers_inside_notes_are_verifiable():
    """A report that faithfully repeats a caveat containing a number must not
    be penalised for doing the right thing."""
    payload = {
        "region": {"name": "Kerala"},
        "observation": {"coverage_fraction": 0.91},
        "unobserved": {"notes": ["Otsu returned -10.99 dB, clamped to -12.0 dB."]},
        "evidence": [],
        "zones": [],
    }
    values = set(collect_extra_values(payload).values())
    assert -10.99 in values
    assert -12.0 in values


def test_coverage_percentage_is_available(flood_payload):
    extra = collect_extra_values(flood_payload)
    assert extra["coverage_percent"] == pytest.approx(98.3, abs=0.1)


# ------------------------------------------------------------- facade

def test_facade_falls_back_when_llm_disabled(contract):
    report, verification = build_report(contract, prefer_llm=False)
    assert report["fallback_used"]
    assert report["fallback_reason"] == "llm_disabled"
    assert verification["passed"]


def test_facade_rejects_a_hallucinating_model(contract):
    """An LLM report that fails verification is discarded. Unverifiable prose
    is worse than plain prose."""

    class Hallucinating:
        class chat:
            class completions:
                @staticmethod
                def create(**_):
                    class M:
                        content = "Flooding displaced 45000 people and killed 22."
                        reasoning = None

                    class C:
                        message = M()
                        finish_reason = "stop"

                    class R:
                        choices = [C()]

                    return R()

    report, verification = build_report(
        contract, prefer_llm=True, client=Hallucinating(), model="test"
    )
    assert report["fallback_used"]
    assert report["fallback_reason"] == "llm_output_failed_verification"
    assert report["rejected_attempt"] is not None
    assert report["rejected_attempt"]["faithfulness_rate"] < 1.0


def test_rejected_output_is_kept_for_analysis(contract):
    """Discarding a failed generation silently would throw away the data needed
    to characterise how models fail - which is the point of measuring it."""

    class Omitting:
        class chat:
            class completions:
                @staticmethod
                def create(**_):
                    class M:
                        content = "Flooding covered 187.4 km2 [E1]."
                        reasoning = None

                    class C:
                        message = M()
                        finish_reason = "stop"

                    class R:
                        choices = [C()]

                    return R()

    payload = {**contract, "unobserved": {"notes": ["Coverage was only 42 percent."]}}
    report, _ = build_report(payload, prefer_llm=True, client=Omitting(), model="t")

    assert report["fallback_reason"] == "llm_omitted_caveats"
    assert report["rejected_attempt"]["completeness"] == 0.0


# ------------------------------------------------------------- helpers

def test_thinking_blocks_are_stripped():
    """REGRESSION: qwen emits <think>...</think> inline. Left in, it lands in
    the report AND its reasoning-numbers get scored by the verifier."""
    assert _strip_thinking("<think>hmm 42</think>The answer.") == "The answer."
    assert _strip_thinking("Plain text.") == "Plain text."


def test_unterminated_thinking_yields_nothing():
    """If the model never closed the block there is no way to know where the
    answer starts. Returning empty triggers the template fallback, which is
    better than publishing chain-of-thought as a flood report."""
    assert _strip_thinking("<think>unclosed 42 and the answer is") == ""
