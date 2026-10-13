"""The riverbed warning: most of the detected water in the river's usual bed."""
from pipeline import analysis


def test_warns_when_most_water_is_in_the_usual_bed():
    info, warning = analysis.riverbed_check(981.6, 820.0)
    assert info["share"] == round(820 / 981.6, 3)
    assert info["occurrence_range"] == [25, 90]
    assert warning and "84%" in warning and "Before and after" in warning


def test_no_warning_when_the_water_is_mostly_new():
    info, warning = analysis.riverbed_check(500.0, 100.0)
    assert info["share"] == 0.2 and warning is None


def test_nothing_without_flooding_or_a_measurement():
    assert analysis.riverbed_check(0, 10) == (None, None)
    assert analysis.riverbed_check(100, None) == (None, None)


def test_a_report_that_repeats_the_warning_passes_verification():
    """The warning is a caveat the report must carry; its numbers must verify."""
    from pipeline import report
    from core.verification import verify_report

    _, warning = analysis.riverbed_check(569.7, 397.5)
    payload = {"unobserved": {"notes": [warning, "A range like 25-90% must read as 90."]}}
    extra = report.collect_extra_values(payload)
    text = warning + " A range like 25-90% must read as 90."
    result = verify_report(text, [], extra_values=extra)
    assert result["passed"], result["unsupported_claims"]
