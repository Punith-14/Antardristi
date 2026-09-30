"""
Does the answer address the question that was asked?

The case this module exists for, stated once: asked "How much did flooding
increase in Kerala in August 2018 compared to May 2018?", the system measured
May against May. net_new_water came out at 0.0 km2, the report said the same
extent was present in the baseline, and verify_report passed 16 of 16 claims
with completeness 1.0.

Nothing was wrong with the prose. Every number in it was faithful to the
evidence record. The evidence record answered a different question, and no
check in the system compared either of them against what the user typed.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import alignment  # noqa: E402


AUGUST_VS_MAY = "How much did flooding increase in Kerala in August 2018 compared to May 2018?"


def route(post=("2018-08-01", "2018-08-31"), pre=None):
    return {
        "analysis_type": "flood_extent",
        "region": "kerala",
        "post_start": post[0], "post_end": post[1],
        "pre_start": pre[0] if pre else None,
        "pre_end": pre[1] if pre else None,
    }


# ------------------------------------------------------- reading the question

def test_months_are_read_in_the_order_written():
    """Order carries meaning: the first period named is the subject."""
    assert alignment.months_named(AUGUST_VS_MAY) == [(2018, 8), (2018, 5)]


@pytest.mark.parametrize("text,expected", [
    ("flooding in Aug 2018", [(2018, 8)]),
    ("flooding in august 2018", [(2018, 8)]),
    ("flooding in August, 2018", [(2018, 8)]),
    ("Sept 2019 and Dec 2019", [(2019, 9), (2019, 12)]),
    ("no month here", []),
])
def test_months_are_read_however_they_are_written(text, expected):
    assert alignment.months_named(text) == expected


def test_a_bare_year_is_not_double_counted_as_a_month():
    """"August 2018" must not also register 2018 as a bare year, or the year
    check fires against a period the month check already matched."""
    assert alignment.years_named("flooding in August 2018") == []
    assert alignment.years_named("flooding in 2018") == [2018]


@pytest.mark.parametrize("question", [
    "how much did flooding increase",
    "has built-up area grown since 2019",
    "compare vegetation before and after",
    "what was the loss of forest cover",
    "flooding in Kerala versus Assam",
])
def test_change_questions_are_recognised(question):
    assert alignment.wants_change(question)


@pytest.mark.parametrize("question", [
    "flood extent in Kerala in August 2018",
    "how much water is in the reservoir",
    "show built-up area in Bangalore Urban",
])
def test_snapshot_questions_are_not_mistaken_for_change(question):
    assert not alignment.wants_change(question)


def test_change_words_match_whole_words_only():
    """"damaged" must not match "aged", and "increased" must not be found
    inside an unrelated word. The same class of bug as `dam` matching
    `damaged` in the router's keyword table."""
    assert not alignment.wants_change("show me the decreaseless data")


# ------------------------------------------------------ the failure it exists for

def test_may_against_may_is_caught():
    """The whole reason this module exists.

    Identical windows make every change figure exactly zero by construction,
    and that zero is then reported as a finding.
    """
    result = alignment.check(
        AUGUST_VS_MAY,
        route(post=("2018-05-01", "2018-05-31"), pre=("2018-05-01", "2018-05-31")),
    )

    assert not result["passed"]
    joined = " ".join(result["failures"])
    assert "identical" in joined
    assert "zero by construction" in joined


def test_a_dropped_period_is_caught_even_when_the_windows_differ():
    """The subtler version: August is dropped, May becomes the subject, and
    some other window becomes the baseline. The windows differ, so an
    identity check alone would pass it."""
    result = alignment.check(
        AUGUST_VS_MAY,
        route(post=("2018-05-01", "2018-05-31"), pre=("2017-05-01", "2017-05-31")),
    )

    assert not result["passed"]
    assert any("08/2018" in f for f in result["failures"])


def test_the_correct_route_passes():
    result = alignment.check(
        AUGUST_VS_MAY,
        route(post=("2018-08-01", "2018-08-31"), pre=("2018-05-01", "2018-05-31")),
    )
    assert result["passed"], result["failures"]


# ------------------------------------------------------------- the other checks

def test_a_change_question_without_a_baseline_is_caught():
    result = alignment.check("has deforestation increased in Chhattisgarh", route())
    assert not result["passed"]
    assert any("no baseline" in f for f in result["failures"])


def test_a_snapshot_question_needs_no_baseline():
    result = alignment.check("flood extent in Kerala in August 2018", route())
    assert result["passed"], result["failures"]


def test_an_inverted_comparison_is_caught():
    """Baseline after the event inverts the sign of every change figure - a
    flood reads as water disappearing."""
    result = alignment.check(
        AUGUST_VS_MAY,
        route(post=("2018-05-01", "2018-05-31"), pre=("2018-08-01", "2018-08-31")),
    )
    assert not result["passed"]
    assert any("inverts" in f for f in result["failures"])


def test_a_year_the_analysis_never_covers_is_caught():
    result = alignment.check(
        "flooding in Kerala in 2019",
        route(post=("2018-08-01", "2018-08-31")),
    )
    assert not result["passed"]
    assert any("2019" in f for f in result["failures"])


# ------------------------------------------------------------------ the shape

def test_every_check_reports_itself_whether_it_passed_or_not():
    """A verification block that only lists failures cannot be distinguished
    from one that ran no checks at all."""
    result = alignment.check("flood extent in Kerala in August 2018", route())

    assert len(result["checks"]) == 5
    for check in result["checks"]:
        assert set(check) == {"name", "passed", "detail"}
        assert check["detail"]


def test_failures_read_as_sentences_a_person_can_act_on():
    result = alignment.check(
        AUGUST_VS_MAY,
        route(post=("2018-05-01", "2018-05-31"), pre=("2018-05-01", "2018-05-31")),
    )
    for failure in result["failures"]:
        assert len(failure) > 40, f"too terse to act on: {failure!r}"
        assert failure[0].isupper()


def test_it_survives_a_question_or_route_it_cannot_parse():
    """Runs on every request, so it must never be the thing that raises."""
    for question in ("", None, "?????", "🙂"):
        for r in ({}, route(), {"post_start": "not-a-date"}):
            result = alignment.check(question, r)
            assert isinstance(result["passed"], bool)


def test_it_names_its_own_method():
    result = alignment.check("flood extent in Kerala", route())
    assert result["checked_by"] == "question_route_alignment_v1"


def test_summarise_says_what_happened():
    ok = alignment.check("flood extent in Kerala in August 2018", route())
    assert "ok" in alignment.summarise(ok)

    bad = alignment.check(
        AUGUST_VS_MAY,
        route(post=("2018-05-01", "2018-05-31"), pre=("2018-05-01", "2018-05-31")),
    )
    assert "FAILED" in alignment.summarise(bad)


# ------------------------------------------------- wired into the live path

def _endpoint_body(source, header):
    """Everything from a function header to the next @app decorator.

    A fixed character count was used here first, and adding one line to /ask
    silently truncated the slice so the assertions stopped seeing the code
    they were checking - a test that passes for the wrong reason, then fails
    for the wrong reason.
    """
    after = source.split(header)[1]
    end = after.find("@app.")
    return after if end == -1 else after[:end]


def test_ask_runs_the_alignment_check_before_spending_quota():
    """A mismatch should be visible without paying for an Earth Engine
    reduction that answers the wrong question."""
    main_source = (BACKEND / "main.py").read_text(encoding="utf-8")
    block = _endpoint_body(main_source, "def ask(payload: AskRequest):")

    assert "alignment.check(payload.question, decision)" in block
    assert block.index("alignment.check") < block.index("analyze(")


def test_the_result_carries_the_alignment_block():
    main_source = (BACKEND / "main.py").read_text(encoding="utf-8")
    block = _endpoint_body(main_source, "def ask(payload: AskRequest):")
    assert 'result["alignment"] = alignment_result' in block


def test_dry_run_reports_alignment_too():
    """dry_run exists to inspect routing without running anything. Alignment
    is part of what there is to inspect."""
    main_source = (BACKEND / "main.py").read_text(encoding="utf-8")
    block = _endpoint_body(main_source, "def ask(payload: AskRequest):")
    dry = block.split("if payload.dry_run:")[1][:300]
    assert "alignment" in dry
