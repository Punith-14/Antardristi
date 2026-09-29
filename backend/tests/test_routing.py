"""
Query routing.

The deterministic half is tested exhaustively here. The model half is tested
against the benchmark in `benchmark_routing.py`, because it needs a key and
costs tokens.
"""

from datetime import date

import pytest

import routing


TODAY = date(2026, 9, 20)


# ------------------------------------------------------------ analysis type

@pytest.mark.parametrize(
    "question,expected",
    [
        ("Show flooded areas in Kerala", "flood_extent"),
        ("Was Bihar inundated", "flood_extent"),
        ("Vegetation health in Punjab", "vegetation_health"),
        ("Is there drought stress in Marathwada", "crop_stress"),
        ("Reservoir levels in Telangana", "water_extent"),
        ("Urban expansion in Surat", "built_up"),
        ("Forest cover in Uttarakhand", "green_cover"),
        ("Bare ground in Jaisalmer", "bare_ground"),
    ],
)
def test_keywords_pick_the_right_analysis(question, expected):
    assert routing.detect_analysis(question)[0] == expected


def test_specific_keywords_win_over_general_ones():
    """'Flood water' must route to flood, not to water. 'Crop stress' must
    route to stress, not to vegetation. Ordering is load-bearing."""
    assert routing.detect_analysis("flood water in Kerala")[0] == "flood_extent"
    assert routing.detect_analysis("crop stress in Punjab")[0] == "crop_stress"
    assert routing.detect_analysis("forest and vegetation")[0] == "green_cover"


def test_keywords_respect_word_boundaries():
    """REGRESSION, caught by the benchmark: 'How many houses were damaged in
    Assam' routed to water_extent, because 'dam' is a substring of 'damaged'.
    A question the satellite cannot answer was being answered."""
    assert routing.detect_analysis("How many houses were damaged in Assam")[0] is None
    assert routing.detect_analysis("Reservoir behind the dam")[0] == "water_extent"


def test_stem_keywords_still_match_their_inflections():
    """Boundary is required at the start only, so 'inundat' still matches
    'inundated' and 'urbanis' matches 'urbanisation'."""
    assert routing.detect_analysis("Was Bihar inundated")[0] == "flood_extent"
    assert routing.detect_analysis("rapid urbanisation")[0] == "built_up"


def test_unrecognised_questions_return_nothing():
    """Guessing an analysis for an out-of-scope question is worse than
    admitting the question was not understood."""
    assert routing.detect_analysis("Write me a poem")[0] is None
    assert routing.detect_analysis("")[0] is None
    assert routing.detect_analysis(None)[0] is None


# -------------------------------------------------------------- comparison

@pytest.mark.parametrize(
    "question",
    [
        "How much has Bangalore grown since 2019",
        "Compare flooding before and after the monsoon",
        "Flood extent this year versus last year",
        "Has deforestation increased over time",
        "Change in cropland in Punjab",
    ],
)
def test_comparison_questions_are_detected(question):
    assert routing.detect_comparison(question)


@pytest.mark.parametrize(
    "question",
    ["Show flooded areas in Kerala", "Reservoir levels in Telangana"],
)
def test_snapshot_questions_are_not_comparisons(question):
    assert not routing.detect_comparison(question)


# -------------------------------------------------------------------- dates

def test_explicit_iso_dates_are_used_verbatim():
    start, end = routing.detect_dates("from 2018-08-15 to 2018-08-25", TODAY)
    assert (start, end) == ("2018-08-15", "2018-08-25")


def test_month_and_year_resolve_to_that_month():
    start, end = routing.detect_dates("flooding in August 2018", TODAY)
    assert start == "2018-08-01"
    assert end == "2018-08-31"


def test_month_without_year_uses_the_current_year():
    start, _ = routing.detect_dates("flooding in August", TODAY)
    assert start.startswith("2026-08")


def test_bare_year_becomes_the_whole_year():
    start, end = routing.detect_dates("water in Assam 2022", TODAY)
    assert (start, end) == ("2022-01-01", "2022-12-31")


def test_kharif_resolves_to_the_monsoon_crop_window():
    """An Indian cropping season is not something a generic parser knows."""
    start, end = routing.detect_dates("kharif 2023", TODAY)
    assert start == "2023-06-01"
    assert end == "2023-10-31"


def test_rabi_wraps_into_the_following_year():
    start, end = routing.detect_dates("rabi 2023", TODAY)
    assert start == "2023-11-01"
    assert end == "2024-03-31"


def test_a_season_that_has_not_started_means_the_last_one():
    """REGRESSION, caught by the benchmark: asked about 'rabi season' in
    September, the parser chose the rabi beginning that November - a window
    entirely in the future, with no imagery in it."""
    start, end = routing.detect_dates("greenness in rabi season", TODAY)
    assert start == "2025-11-01"
    assert end == "2026-03-31"
    assert end < TODAY.isoformat()


def test_a_season_already_underway_uses_the_current_year():
    start, _ = routing.detect_dates("kharif season", TODAY)
    assert start == "2026-06-01"


def test_no_date_yields_nothing_rather_than_a_guess():
    assert routing.detect_dates("Show flooded areas in Kerala", TODAY) == (None, None)


def test_default_window_is_the_last_three_months():
    start, end = routing.default_window(TODAY)
    assert end == "2026-09-20"
    assert start == "2026-06-22"


def test_february_month_end_is_handled():
    _, end = routing.detect_dates("February 2024", TODAY)
    assert end == "2024-02-29"


def test_baseline_is_the_same_window_a_year_earlier():
    assert routing.baseline_for("2023-08-01", "2023-08-31") == (
        "2022-08-01",
        "2022-08-31",
    )


# ---------------------------------------------------------- rule routing

def test_rules_produce_a_usable_route():
    result = routing.route_deterministic("Flooding in August 2018", TODAY)
    assert result["analysis_type"] == "flood_extent"
    assert result["post_start"] == "2018-08-01"
    assert result["pre_start"] is None
    assert result["method"] == "rules"


def test_rules_add_a_baseline_for_comparisons():
    result = routing.route_deterministic("Compare cropland in 2023", TODAY)
    assert result["pre_start"] == "2022-01-01"
    assert result["pre_end"] == "2022-12-31"


def test_rules_never_guess_a_region():
    """Guessing a place name is how you analyse the wrong district."""
    assert routing.route_deterministic("Flooding in Kerala", TODAY)["region"] is None


# -------------------------------------------------------------- validation

def test_a_valid_route_has_no_errors():
    route = {
        "analysis_type": "flood_extent",
        "post_start": "2018-08-15",
        "post_end": "2018-08-25",
        "confidence": 0.9,
    }
    assert routing.validate(route, TODAY) == []


def test_an_invented_analysis_type_is_rejected():
    errors = routing.validate({"analysis_type": "earthquake_damage",
                               "post_start": "2020-01-01",
                               "post_end": "2020-02-01"}, TODAY)
    assert any("unknown analysis_type" in e for e in errors)


def test_dates_before_the_satellite_record_are_rejected():
    """Sentinel-2 launched in 2015. A model asked about 'the 1998 floods' will
    cheerfully produce 1998 dates."""
    errors = routing.validate({"analysis_type": "flood_extent",
                               "post_start": "1998-07-01",
                               "post_end": "1998-08-01"}, TODAY)
    assert any("predates" in e for e in errors)


def test_future_dates_are_rejected():
    errors = routing.validate({"analysis_type": "flood_extent",
                               "post_start": "2027-01-01",
                               "post_end": "2027-02-01"}, TODAY)
    assert any("future" in e for e in errors)


def test_reversed_windows_are_rejected():
    errors = routing.validate({"analysis_type": "flood_extent",
                               "post_start": "2020-06-01",
                               "post_end": "2020-01-01"}, TODAY)
    assert any("after" in e for e in errors)


def test_half_a_baseline_is_rejected():
    errors = routing.validate({"analysis_type": "flood_extent",
                               "post_start": "2020-01-01",
                               "post_end": "2020-02-01",
                               "pre_start": "2019-01-01"}, TODAY)
    assert any("incomplete" in e for e in errors)


def test_confidence_outside_zero_to_one_is_rejected():
    errors = routing.validate({"analysis_type": "flood_extent",
                               "post_start": "2020-01-01",
                               "post_end": "2020-02-01",
                               "confidence": 7}, TODAY)
    assert any("confidence" in e for e in errors)


def test_garbage_dates_are_rejected_not_crashed_on():
    errors = routing.validate({"analysis_type": "flood_extent",
                               "post_start": "last summer",
                               "post_end": "2020-02-01"}, TODAY)
    assert any("not a date" in e for e in errors)


# ------------------------------------------------------ json extraction

def test_plain_json_is_parsed():
    assert routing._extract_json('{"a": 1}') == {"a": 1}


def test_fenced_json_is_parsed():
    """Models wrap output in code fences despite being told not to."""
    assert routing._extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_json_buried_in_prose_is_recovered():
    assert routing._extract_json('Here you go: {"a": 1} hope that helps') == {"a": 1}


def test_unparseable_output_raises_rather_than_returning_junk():
    with pytest.raises(routing.RoutingError):
        routing._extract_json("I could not determine that.")


# ------------------------------------------------------------ the facade

class FakeModel:
    """Returns whatever JSON it was constructed with."""

    def __init__(self, payload):
        self.payload = payload

    class _Completions:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **_):
            import json as _json

            class M:
                content = _json.dumps(self.outer.payload)
                reasoning = None

            class C:
                message = M()

            class R:
                choices = [C()]

            return R()

    @property
    def chat(self):
        outer = self

        class Chat:
            completions = FakeModel._Completions(outer)

        return Chat()


def test_a_valid_model_route_is_used():
    model = FakeModel({
        "analysis_type": "flood_extent",
        "region": "kendrapara",
        "post_start": "2018-08-15",
        "post_end": "2018-08-25",
        "pre_start": None,
        "pre_end": None,
        "confidence": 0.95,
    })
    result = routing.route("Flooding in Kendrapara", client=model, today=TODAY)

    assert result["region"] == "kendrapara"
    assert result["method"] == "model"
    assert not result["fallback_used"]


def test_an_invalid_model_route_falls_back_to_rules():
    """The model proposing 1998 must not send a 1998 query to Earth Engine."""
    model = FakeModel({
        "analysis_type": "flood_extent",
        "region": "kerala",
        "post_start": "1998-07-01",
        "post_end": "1998-08-01",
        "confidence": 0.9,
    })
    result = routing.route("The 1998 Kerala floods", client=model, today=TODAY)

    assert result["fallback_used"]
    assert "predates" in result["fallback_reason"]
    assert result["method"] == "rules"


def test_user_stated_dates_beat_the_models_reading():
    model = FakeModel({
        "analysis_type": "flood_extent",
        "region": "kerala",
        "post_start": "2020-01-01",
        "post_end": "2020-03-01",
        "confidence": 0.8,
    })
    result = routing.route(
        "Flooding in Kerala from 2018-08-15 to 2018-08-25", client=model, today=TODAY
    )
    assert result["post_start"] == "2018-08-15"


def test_rule_agreement_is_recorded():
    """When rules and model agree, confidence in the route is higher. Recording
    it makes disagreement measurable rather than invisible."""
    model = FakeModel({
        "analysis_type": "flood_extent", "region": "kerala",
        "post_start": "2024-01-01", "post_end": "2024-03-01", "confidence": 0.9,
    })
    agreeing = routing.route("Flooding in Kerala", client=model, today=TODAY)
    assert agreeing["rules_agreed"] is True

    model2 = FakeModel({
        "analysis_type": "built_up", "region": "kerala",
        "post_start": "2024-01-01", "post_end": "2024-03-01", "confidence": 0.9,
    })
    disagreeing = routing.route("Flooding in Kerala", client=model2, today=TODAY)
    assert disagreeing["rules_agreed"] is False


def test_a_null_analysis_is_a_valid_refusal():
    """REGRESSION, caught by the benchmark: the schema forced one of seven
    analyses, so 'write me a poem about rivers' came back as built_up.
    Refusing must be an available answer."""
    assert routing.validate({
        "analysis_type": None,
        "post_start": "2024-01-01",
        "post_end": "2024-03-01",
        "confidence": 0.1,
    }, TODAY) == []


def test_a_refusal_does_not_need_a_date_window():
    """REGRESSION: the model correctly refused 'how many people live in
    Chennai' and returned null dates with it. Validation demanded a window,
    rejected the refusal, fell back to rules, and then told the user the
    language model had been unavailable - which was false."""
    assert routing.validate({
        "analysis_type": None,
        "post_start": None,
        "post_end": None,
        "confidence": 0.1,
    }, TODAY) == []


def test_describe_separates_unavailable_from_rejected():
    """Saying 'the model was unavailable' when its output was rejected sends
    whoever debugs this to the network instead of the output."""
    rejected = routing.describe({
        "analysis_type": "flood_extent", "region": "kerala",
        "post_start": "2024-01-01", "post_end": "2024-03-01",
        "fallback_used": True,
        "fallback_reason": "validation failed: post_start predates the satellite record",
    })
    assert "rejected" in rejected

    unavailable = routing.describe({
        "analysis_type": "flood_extent", "region": "kerala",
        "post_start": "2024-01-01", "post_end": "2024-03-01",
        "fallback_used": True,
        "fallback_reason": "GROQ_API_KEY is not set.",
    })
    assert "unavailable" in unavailable


def test_describe_says_plainly_when_a_question_is_refused():
    text = routing.describe({"analysis_type": None, "refused": True})
    assert "cannot be answered from satellite imagery" in text


def test_low_confidence_is_treated_as_refusal():
    """A model that is unsure is refusing in all but name. Running an Earth
    Engine query on a guess is worse than admitting incomprehension."""
    model = FakeModel({
        "analysis_type": "built_up", "region": "chennai",
        "post_start": "2024-01-01", "post_end": "2024-03-01",
        "confidence": 0.2,
    })
    result = routing.route("How many people live in Chennai", client=model, today=TODAY)

    assert result["analysis_type"] is None
    assert result["refused"]
    assert "confidence" in result["refusal_reason"]


def test_rules_supply_a_baseline_the_model_missed():
    """REGRESSION, caught by the benchmark: the rules detect comparison at 96%
    and the model at 89%, but taking the model wholesale discarded the rules'
    answer. 'Has deforestation increased' lost its baseline entirely."""
    model = FakeModel({
        "analysis_type": "green_cover", "region": "chhattisgarh",
        "post_start": "2024-01-01", "post_end": "2024-03-01",
        "pre_start": None, "pre_end": None, "confidence": 0.9,
    })
    result = routing.route(
        "Has deforestation increased in Chhattisgarh", client=model, today=TODAY
    )

    assert result["pre_start"] is not None
    assert result["comparison_from"] == "rules"


def test_a_baseline_the_model_supplied_is_kept():
    model = FakeModel({
        "analysis_type": "built_up", "region": "surat",
        "post_start": "2023-01-01", "post_end": "2023-03-01",
        "pre_start": "2019-01-01", "pre_end": "2019-03-01", "confidence": 0.9,
    })
    result = routing.route(
        "Urban expansion in Surat between 2019 and 2023", client=model, today=TODAY
    )

    assert result["pre_start"] == "2019-01-01"
    assert "comparison_from" not in result


def test_missing_api_key_degrades_to_rules(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = routing.route("Flooding in Kerala", today=TODAY)

    assert result["fallback_used"]
    assert result["analysis_type"] == "flood_extent"


def test_describe_is_readable():
    result = routing.route_deterministic("Flooding in August 2018", TODAY)
    result["region"] = "kendrapara"
    text = routing.describe(result)

    assert "flood extent" in text
    assert "kendrapara" in text
    assert "2018-08-01" in text


# ----------------------------------------------- a baseline that is a baseline

def base_route(**overrides):
    route = {
        "analysis_type": "flood_extent",
        "region": "kerala",
        "post_start": "2018-08-01",
        "post_end": "2018-08-31",
        "pre_start": "2018-05-01",
        "pre_end": "2018-05-31",
        "confidence": 0.9,
    }
    route.update(overrides)
    return route


def test_a_real_comparison_validates():
    assert routing.validate(base_route(), today=TODAY) == []


def test_identical_windows_are_rejected():
    """The failure that prompted this check.

    Asked to compare August 2018 against May 2018, the model dropped August and
    returned May in both slots. Nothing downstream could tell: net_new_water
    came out at exactly 0.0 km2, the report said the same extent was present in
    the baseline, and verification passed 16 of 16 claims. The numbers were
    faithful to evidence that measured the wrong thing.
    """
    errors = routing.validate(
        base_route(pre_start="2018-08-01", pre_end="2018-08-31"), today=TODAY
    )
    assert errors, "a period compared against itself must not validate"
    assert "identical" in " ".join(errors)


def test_a_baseline_after_the_event_is_rejected():
    """Swapped windows invert the sign of every change figure: a flood reads as
    water disappearing."""
    errors = routing.validate(
        base_route(pre_start="2018-11-01", pre_end="2018-11-30"), today=TODAY
    )
    assert errors
    assert "precede" in " ".join(errors)


def test_a_baseline_starting_the_same_day_is_rejected():
    """Same start, longer window - still not a before."""
    errors = routing.validate(
        base_route(pre_start="2018-08-01", pre_end="2018-09-30"), today=TODAY
    )
    assert errors


def test_overlapping_windows_are_allowed_when_the_baseline_starts_earlier():
    """Not every overlap is a mistake. A seasonal baseline can legitimately run
    into the event window, so only inversion and identity are rejected."""
    assert routing.validate(
        base_route(pre_start="2018-06-01", pre_end="2018-08-15"), today=TODAY
    ) == []


def test_single_window_routes_are_untouched():
    """No baseline, nothing to compare - these checks must not fire."""
    assert routing.validate(
        base_route(pre_start=None, pre_end=None), today=TODAY
    ) == []


def test_the_prompt_tells_the_model_which_period_is_which():
    """The validator is the safety net. This is the actual fix: the prompt
    never said that of two named periods, the later one is post."""
    prompt = routing.SYSTEM_PROMPT.lower()
    assert "later one is post" in prompt
    assert "earlier one is pre" in prompt


# ------------------------------------------- the merge must not break a route

class FakeModelRoute:
    """Stands in for route_with_model, returning a fixed proposal."""

    def __init__(self, payload):
        self.payload = payload

    def __call__(self, text, client=None, model=None, today=None):
        return dict(self.payload)


def test_the_merge_does_not_collapse_a_two_window_comparison(monkeypatch):
    """The bug this test exists for.

    The model correctly split "August 2018 compared to May 2018" into
    post=August, pre=May. validate() passed. THEN the rules overwrote the post
    window with their own single-date parse, leaving May against May - and
    nothing re-checked it. net_new_water came out at exactly 0.0 km2, the report
    stated it as fact, and verification scored 16 of 16 claims supported.
    """
    monkeypatch.setattr(
        routing,
        "route_with_model",
        FakeModelRoute({
            "analysis_type": "flood_extent",
            "region": "kerala",
            "post_start": "2018-08-01", "post_end": "2018-08-31",
            "pre_start": "2018-05-01", "pre_end": "2018-05-31",
            "confidence": 0.9,
        }),
    )

    result = routing.route(
        "How much did flooding increase in Kerala in August 2018 compared to May 2018?",
        today=TODAY,
    )

    assert (result["post_start"], result["pre_start"]) != (
        result["pre_start"], result["pre_start"]
    ), "the comparison collapsed onto a single window"
    assert result["post_start"] == "2018-08-01"
    assert result["pre_start"] == "2018-05-01"


def test_a_route_broken_by_the_merge_falls_back_rather_than_shipping(monkeypatch):
    """Belt and braces. If some future merge rule does collapse the windows,
    the second validate() has to catch it and refuse the route."""
    monkeypatch.setattr(
        routing,
        "route_with_model",
        FakeModelRoute({
            "analysis_type": "flood_extent",
            "region": "kerala",
            "post_start": "2018-05-01", "post_end": "2018-05-31",
            "pre_start": "2018-05-01", "pre_end": "2018-05-31",
            "confidence": 0.9,
        }),
    )

    result = routing.route("flooding in Kerala compared to before", today=TODAY)

    assert result["fallback_used"] is True
    assert "identical" in result["fallback_reason"]


def test_the_rules_still_win_on_dates_for_a_single_window_question(monkeypatch):
    """The exception is narrow. Where the model did NOT split the windows, the
    rules read dates better and must still override - that is a measured
    result, 100% against the model's, and the fix must not undo it."""
    monkeypatch.setattr(
        routing,
        "route_with_model",
        FakeModelRoute({
            "analysis_type": "flood_extent",
            "region": "kerala",
            "post_start": "2019-01-01", "post_end": "2019-01-31",
            "pre_start": None, "pre_end": None,
            "confidence": 0.9,
        }),
    )

    result = routing.route("flooding in Kerala in August 2018", today=TODAY)
    assert result["post_start"].startswith("2018-08")


def test_a_rejected_route_reports_why(monkeypatch):
    """errors was hardcoded to [] on the fallback path, so a rejected route
    looked identical to a clean one in the API response."""
    monkeypatch.setattr(
        routing,
        "route_with_model",
        FakeModelRoute({
            "analysis_type": "flood_extent",
            "region": "kerala",
            "post_start": "2018-05-01", "post_end": "2018-05-31",
            "pre_start": "2018-05-01", "pre_end": "2018-05-31",
            "confidence": 0.9,
        }),
    )

    result = routing.route("flooding in Kerala compared to before", today=TODAY)
    assert result["errors"], "a rejected route must say what was wrong with it"


def test_a_rules_baseline_is_derived_from_the_surviving_post_window():
    """Caught by adding the second validate().

    The rules derive their baseline from THEIR post window. Where the model's
    window wins the merge, copying the rules' dates across attaches a baseline
    to the wrong year - "has deforestation increased" ended up with a 2025
    baseline against a 2024 event, a year AFTER what it was meant to precede.
    """
    model = FakeModel({
        "analysis_type": "green_cover", "region": "chhattisgarh",
        "post_start": "2024-01-01", "post_end": "2024-03-01",
        "pre_start": None, "pre_end": None, "confidence": 0.9,
    })
    result = routing.route(
        "Has deforestation increased in Chhattisgarh", client=model, today=TODAY
    )

    assert result["comparison_from"] == "rules"
    assert result["pre_start"] < result["post_start"], (
        "the baseline must precede the window it is a baseline for"
    )
    assert result["pre_start"].startswith("2023"), (
        "a baseline for a 2024 event belongs in 2023, not a year taken from "
        "the rules' own unrelated window"
    )
