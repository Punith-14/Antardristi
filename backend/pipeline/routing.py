"""
Natural-language query routing.

This is what makes the project's title honest. Until now `query_parser.py`
matched keywords against three lists, while the report layer used a language
model - so the system could *write* fluently but could not *understand*.

Design follows the same rule as the report layer: the model does one narrow,
checkable job, and everything it returns is validated before use. It extracts
structure; it never decides what the answer is.

A deterministic parser runs first. The model is only consulted when the rules
cannot settle the query, which keeps the common cases free, fast and testable.
"""

import json
import os
import re
from datetime import date, datetime, timedelta

ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "openai/gpt-oss-20b")

# Below this, the model's own uncertainty is treated as a refusal. Running an
# expensive Earth Engine query on a guess is worse than saying "I did not
# understand that".
CONFIDENCE_FLOOR = 0.4

# Analyses the router may choose. Kept here rather than imported so routing can
# be tested without Earth Engine on the path.
ANALYSIS_TYPES = [
    "flood_extent",
    "vegetation_health",
    "crop_stress",
    "water_extent",
    "built_up",
    "bare_ground",
    "green_cover",
]

# Strong, unambiguous signals. When one of these fires the model is not needed.
#
# A trailing * marks a stem that should also match its inflections, so
# "inundat*" catches "inundated" and "inundation". Everything else is matched as
# a WHOLE WORD. Without that, "dam" matched "damaged" and routed "how many
# houses were damaged" - which no satellite can answer - to water extent.
KEYWORDS = {
    "flood_extent": ["flood*", "inundat*", "deluge", "submerg*"],
    "crop_stress": ["stress*", "drought*", "dry spell", "wilting",
                    "moisture deficit"],
    "built_up": ["built-up", "built up", "construction", "urban expansion",
                 "urbanis*", "urbaniz*", "encroach*", "concrete"],
    "green_cover": ["forest*", "deforest*", "tree cover", "canopy",
                    "green cover"],
    # "water" alone is deliberately absent: it appears in questions the
    # satellite cannot answer ("how deep was the water"), so the model decides.
    "water_extent": ["reservoir*", "lake*", "waterbody", "water body", "dam",
                     "dams", "backwater*", "river extent"],
    "bare_ground": ["bare", "barren", "desert*", "degrad*", "erosion"],
    "vegetation_health": ["vegetation", "crop", "crops", "cropland",
                          "greenness", "ndvi", "harvest*", "sowing",
                          "kharif", "rabi"],
}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Indian cropping seasons, so "kharif 2023" resolves without asking the model.
SEASONS = {
    "kharif": (6, 10),      # monsoon crop
    "rabi": (11, 3),        # winter crop
    "monsoon": (6, 9),
    "summer": (3, 5),
    "winter": (12, 2),
    "pre-monsoon": (3, 5),
    "post-monsoon": (10, 11),
}

SYSTEM_PROMPT = """You convert a question about Indian satellite imagery into a structured query. You do not answer the question.

Return ONLY a JSON object, no prose, with these keys:

  analysis_type : one of flood_extent, vegetation_health, crop_stress, water_extent, built_up, bare_ground, green_cover, or null
  region        : an Indian state or district name, lowercase, or null if none is named
  post_start    : YYYY-MM-DD
  post_end      : YYYY-MM-DD
  pre_start     : YYYY-MM-DD or null
  pre_end       : YYYY-MM-DD or null
  confidence    : 0.0 to 1.0
  reasoning     : one short sentence

Rules:
- pre_start and pre_end are ONLY set when the question compares two periods ("before and after", "compared to last year", "since 2019", "change", "increased", "grown", "shrunk", "expansion", "loss", "over time").
- When the question names TWO periods, the LATER one is post and the EARLIER one is pre. "August 2018 compared to May 2018" means post = August 2018 and pre = May 2018. Never put the same period in both: a window compared against itself measures nothing.
- Never invent a region. If none is named, return null.
- If no date is given, use the last 3 months ending today.
Choosing between the analyses:
- flood_extent   : flooding, inundation, submerged land
- water_extent   : reservoirs, lakes, dams, rivers, backwaters. "Reservoir levels", "has the dam shrunk" and "how full is the lake" all mean SURFACE EXTENT seen from above, and are measurable.
- vegetation_health : crops, cropland, farmland, greenness, NDVI, general vegetation
- crop_stress    : drought, moisture stress, dry spells
- green_cover    : FOREST and tree canopy ONLY. General greenness and cropland are vegetation_health, not this.
- built_up       : buildings, concrete, urban growth, construction
- bare_ground    : bare or barren land, desert, land degradation, erosion

Regions:
- Return the MOST SPECIFIC place the question names. If it says Bhopal, return "bhopal", never "madhya pradesh". If it says Gurgaon, return "gurgaon", never "haryana". Do not substitute the state that contains a named district, even if you are unsure the district exists.
- Strip directional and descriptive qualifiers: "western Rajasthan" is rajasthan, "coastal Odisha" is odisha.
- Copy the spelling from the question. Do not correct or transliterate it.
- Return the name even when no district is mentioned but a state is, and null only when no place at all is named.

REFUSING IS CORRECT. Return analysis_type: null and confidence below 0.3 when the question is not something a satellite image can measure. Refuse these:
- weather, forecasts, temperature, rainfall predictions
- population, demographics, how many people
- how deep the water is, water depth, height of anything
- damage, casualties, houses destroyed, people affected
- anything that is not about observing the land surface

But do NOT refuse a question about the AREA or EXTENT of something visible from above, even when it is phrased loosely. "Reservoir levels", "has the lake shrunk", "how much forest is left" are all measurable and must be routed, not refused.

The test is simple: can you see it from space and measure how much ground it covers? Extent yes, depth no. Land cover yes, people no. Now yes, tomorrow no."""


class RoutingError(RuntimeError):
    pass


# ----------------------------------------------------------- deterministic

def _contains(haystack, keyword):
    """Whole-word match, unless the keyword ends in * (a stem).

    Plain substring matching routed "How many houses were damaged in Assam" to
    water_extent, because "dam" sits inside "damaged". A boundary at the start
    alone does not help - "damaged" starts a word too. Both ends are needed.
    """
    if keyword.endswith("*"):
        return re.search(rf"\b{re.escape(keyword[:-1])}", haystack) is not None
    return re.search(rf"\b{re.escape(keyword)}\b", haystack) is not None


def detect_analysis(text):
    """Keyword match, returning (type, matched_phrase) or (None, None).

    Ordered by specificity: "flood" must win over "water", and "crop stress"
    over "crop", so the more specific keys are tested first.
    """
    lowered = (text or "").lower()
    for analysis in ["flood_extent", "crop_stress", "built_up", "green_cover",
                     "water_extent", "bare_ground", "vegetation_health"]:
        for keyword in KEYWORDS[analysis]:
            if _contains(lowered, keyword):
                return analysis, keyword
    return None, None


def detect_comparison(text):
    """Does the question ask for change rather than a snapshot?"""
    lowered = (text or "").lower()
    # Explicit comparisons, and verbs that only make sense against an earlier
    # state. "Has deforestation increased" is a comparison even though it names
    # no second period - added after the benchmark showed these were being
    # missed and the query ran as a snapshot.
    signals = [
        # explicit
        "compare", "comparison", "before and after", "before/after",
        "versus", " vs ", "difference", "last year", "previous year",
        "over time", "trend", "since",
        # change verbs
        "change", "changed", "increase", "increased", "decrease", "decreased",
        "grown", "grew", "growth", "shrunk", "shrank", "shrink", "shrinking",
        "expansion", "expanded", "expanding", "loss", "lost", "gained",
        "reduction", "reduced", "declined", "decline",
    ]
    return any(signal in lowered for signal in signals)


def detect_dates(text, today=None):
    """Explicit dates, month-year pairs, bare years and Indian seasons.

    Returns (start, end) as ISO strings, or (None, None).
    """
    today = today or date.today()
    lowered = (text or "").lower()

    explicit = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", lowered)
    if len(explicit) >= 2:
        return explicit[0], explicit[1]

    year_match = re.search(r"\b(19|20)\d{2}\b", lowered)
    year = int(year_match.group(0)) if year_match else None

    for name, (start_month, end_month) in SEASONS.items():
        if name in lowered:
            base = year or today.year
            start = date(base, start_month, 1)

            # With no year given, a season that has not begun yet means the most
            # recent one, not the one still months away. Asked about "rabi" in
            # September, a user means the rabi that just ended.
            if year is None and start > today:
                base -= 1
                start = date(base, start_month, 1)

            # A season that wraps the new year ends in the following one.
            end_year = base if end_month >= start_month else base + 1
            return start.isoformat(), _month_end(end_year, end_month).isoformat()

    for name, number in MONTHS.items():
        if re.search(rf"\b{name}\b", lowered):
            base = year or today.year
            return (
                date(base, number, 1).isoformat(),
                _month_end(base, number).isoformat(),
            )

    if year:
        return date(year, 1, 1).isoformat(), date(year, 12, 31).isoformat()

    return None, None


def _month_end(year, month):
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def default_window(today=None):
    """Last three months, when the question gives no period."""
    today = today or date.today()
    return (today - timedelta(days=90)).isoformat(), today.isoformat()


def baseline_for(post_start, post_end):
    """The same window one year earlier - the natural comparison."""
    start = datetime.fromisoformat(post_start).date()
    end = datetime.fromisoformat(post_end).date()
    return (
        start.replace(year=start.year - 1).isoformat(),
        end.replace(year=end.year - 1).isoformat(),
    )


def route_deterministic(text, today=None):
    """Rule-based routing. Returns a partial route; region is left to the model
    or to the caller, because guessing a place name is how you analyse the
    wrong district."""
    analysis, keyword = detect_analysis(text)
    start, end = detect_dates(text, today)

    if start is None:
        start, end = default_window(today)
        dated = False
    else:
        dated = True

    pre_start = pre_end = None
    if detect_comparison(text):
        pre_start, pre_end = baseline_for(start, end)

    return {
        "analysis_type": analysis,
        "region": None,
        "post_start": start,
        "post_end": end,
        "pre_start": pre_start,
        "pre_end": pre_end,
        "matched_keyword": keyword,
        "dates_explicit": dated,
        "method": "rules",
    }


# ------------------------------------------------------------------- model

def _client():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RoutingError("GROQ_API_KEY is not set.")
    try:
        from groq import Groq
    except ImportError as exc:
        raise RoutingError("groq package not installed.") from exc
    return Groq(api_key=api_key, timeout=15)


def _extract_json(text):
    """Models sometimes wrap JSON in prose or a code fence despite instructions."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RoutingError(f"Model did not return JSON: {text[:160]}") from exc


def route_with_model(text, client=None, model=None, today=None):
    """Ask the model to structure the query, then validate everything it says."""
    model = model or ROUTER_MODEL
    client = client or _client()
    today = today or date.today()

    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Today is {today.isoformat()}.\n\nQuestion: {text}"},
        ],
        "temperature": 0.0,
        "max_tokens": 800,
    }
    if "gpt-oss" in model:
        request["reasoning_effort"] = "low"

    try:
        response = client.chat.completions.create(**request)
    except Exception as exc:
        if "reasoning_effort" in request and "reasoning_effort" in str(exc):
            request.pop("reasoning_effort")
            response = client.chat.completions.create(**request)
        else:
            raise RoutingError(f"Router request failed: {exc}") from exc

    message = response.choices[0].message
    content = getattr(message, "content", None) or getattr(message, "reasoning", None)
    parsed = _extract_json(content)
    parsed["method"] = "model"
    parsed["model"] = model
    return parsed


# -------------------------------------------------------------- validation

def validate(route, today=None):
    """Reject anything the model invented. Never trust a route unchecked.

    Same principle as the report layer: the model's output is a proposal, and
    a proposal that fails validation is discarded rather than acted on.
    """
    today = today or date.today()
    errors = []

    analysis = route.get("analysis_type")
    # null is a legitimate answer: the question cannot be answered from
    # satellite imagery, and refusing is correct.
    if analysis is not None and analysis not in ANALYSIS_TYPES:
        errors.append(f"unknown analysis_type {analysis!r}")

    # A refusal needs no date window. Demanding one rejected the model's
    # correct refusal of "how many people live in Chennai" for "missing
    # post_start", which then fell back to rules and reported - falsely - that
    # the language model had been unavailable.
    if analysis is None:
        return errors

    for key in ("post_start", "post_end"):
        value = route.get(key)
        if not value:
            errors.append(f"missing {key}")
            continue
        try:
            parsed = datetime.fromisoformat(value).date()
        except (TypeError, ValueError):
            errors.append(f"{key} is not a date: {value!r}")
            continue
        # Sentinel-2 launched 2015; nothing earlier is analysable.
        if parsed.year < 2015:
            errors.append(f"{key} predates the satellite record: {value}")
        if parsed > today + timedelta(days=1):
            errors.append(f"{key} is in the future: {value}")

    start, end = route.get("post_start"), route.get("post_end")
    if start and end and not errors and start > end:
        errors.append("post_start is after post_end")

    pre_start, pre_end = route.get("pre_start"), route.get("pre_end")
    if bool(pre_start) != bool(pre_end):
        errors.append("baseline window is incomplete")
    if pre_start and pre_end and pre_start > pre_end:
        errors.append("pre_start is after pre_end")

    # A baseline has to be a DIFFERENT, EARLIER window, and neither was checked.
    #
    # Asked "how much did flooding increase in Kerala in August 2018 compared to
    # May 2018", the model dropped August and returned May in both slots. Every
    # downstream guard passed: net_new_water came out at exactly 0.0 km2, the
    # report stated that the same extent was present in the baseline period, and
    # verification scored 16 of 16 claims supported. The arithmetic was faithful
    # to evidence that measured the wrong thing, which is the one failure the
    # verifier cannot see - so it has to be caught here.
    if pre_start and pre_end and start and end and not errors:
        if (pre_start, pre_end) == (start, end):
            errors.append(
                "baseline window is identical to the post window - a period "
                "compared against itself measures nothing"
            )
        elif pre_start >= start:
            errors.append(
                f"baseline window ({pre_start}) does not precede the post "
                f"window ({start})"
            )

    confidence = route.get("confidence")
    if confidence is not None and not 0 <= float(confidence) <= 1:
        errors.append(f"confidence out of range: {confidence}")

    return errors


# ----------------------------------------------------------------- facade

def route(text, client=None, model=None, today=None, prefer_model=True):
    """Turn a plain-language question into a structured query, then mark it
    as a "latest pass" question when it asks about now and names no date."""
    from pipeline.latest import asks_for_latest

    result = _route(text, client=client, model=model, today=today,
                    prefer_model=prefer_model)
    if result.get("analysis_type") == "flood_extent" and asks_for_latest(text):
        # Flood only: the latest-pass mode is a Sentinel-1 feature. A baseline
        # the router derived from its default window would describe a period
        # nobody asked about, so it is dropped.
        result["latest"] = True
        result["pre_start"] = result["pre_end"] = None
    return result


def _route(text, client=None, model=None, today=None, prefer_model=True):
    """Turn a plain-language question into a structured query.

    Rules run first and settle most queries. The model is consulted when the
    rules cannot name the analysis or cannot find the region, and its answer is
    validated before it is used. A route that fails validation falls back to
    the rules rather than being acted on.
    """
    today = today or date.today()
    rules = route_deterministic(text, today)

    # The rules can never supply a region, so the model is needed whenever one
    # is expected - which is almost always.
    needs_model = prefer_model and (rules["analysis_type"] is None or True)

    if not needs_model:
        return {**rules, "confidence": 0.9 if rules["analysis_type"] else 0.3,
                "fallback_used": False, "errors": []}

    errors = []
    try:
        proposed = route_with_model(text, client=client, model=model, today=today)
        errors = validate(proposed, today)

        if not errors:
            # Take each part from whichever side is measurably better.
            #
            # Benchmarked on 45 queries: the rules read dates (100%) and detect
            # comparison (96%) better than the model, which reads regions (87%
            # against 27%) far better. Using the model wholesale threw away the
            # rules' strengths - "has deforestation increased" lost its baseline
            # because the model did not set one.

            # ... with one exception. The rules parse a SINGLE window, so when
            # the question names two periods and the model split them correctly,
            # letting the rules overwrite the post window collapses the
            # comparison onto whichever date the rules happened to pick.
            #
            # "How much did flooding increase in Kerala in August 2018 compared
            # to May 2018" returned post=August pre=May from the model, the
            # rules overwrote post with May, and the result was May measured
            # against May: net_new_water exactly 0.0, reported as fact, with
            # verification scoring 16 of 16.
            model_split_the_windows = bool(proposed.get("pre_start"))
            if rules["dates_explicit"] and not model_split_the_windows:
                proposed["post_start"] = rules["post_start"]
                proposed["post_end"] = rules["post_end"]

            if rules["pre_start"] and not proposed.get("pre_start"):
                # Take the rules' DETECTION that this is a comparison - they are
                # right 96% of the time - but not their arithmetic. The rules
                # derived their baseline from their own post window, and where
                # the model's window won, copying those dates across attaches a
                # baseline to the wrong year: "has deforestation increased" put
                # a 2025 baseline against a 2024 event, a year AFTER what it was
                # supposed to precede. Recompute from the window that survived.
                proposed["pre_start"], proposed["pre_end"] = baseline_for(
                    proposed["post_start"], proposed["post_end"]
                )
                proposed["comparison_from"] = "rules"

            # Validate AGAIN, because the merge above can invalidate a route
            # that was sound a moment ago - it rewrites the very fields the
            # first check passed. The thing that gets acted on is what has to
            # be valid, not the proposal it was built from.
            errors = validate(proposed, today)

        if not errors:

            # A model that is unsure is refusing in all but name.
            confidence = proposed.get("confidence")
            if confidence is not None and float(confidence) < CONFIDENCE_FLOOR:
                proposed["analysis_type"] = None
                proposed["refused"] = True
                proposed["refusal_reason"] = (
                    f"confidence {confidence} is below the {CONFIDENCE_FLOOR} floor"
                )

            proposed["refused"] = proposed.get("analysis_type") is None
            proposed["fallback_used"] = False
            proposed["errors"] = []
            proposed["rules_agreed"] = (
                rules["analysis_type"] == proposed.get("analysis_type")
            )
            return proposed

        reason = f"validation failed: {'; '.join(errors)}"
    except RoutingError as exc:
        reason = str(exc)

    return {
        **rules,
        "confidence": 0.5 if rules["analysis_type"] else 0.2,
        "fallback_used": True,
        "fallback_reason": reason,
        # Carry the failures out instead of dropping them. Returning [] here
        # made a rejected route look identical to a clean one in the API
        # response, which is how a broken comparison went unnoticed.
        "errors": errors,
    }


def describe(route_result):
    """One line explaining what the system understood, for the interface."""
    analysis = (route_result.get("analysis_type") or "unknown").replace("_", " ")
    period = f"{route_result.get('post_start')} to {route_result.get('post_end')}"

    # A drawn area has no region name, and saying "no region identified" would
    # read as a failure when the caller deliberately supplied a shape instead.
    if route_result.get("region_source") == "user_defined_area":
        region = "the area you drew"
    else:
        region = route_result.get("region") or "no region identified"

    if route_result.get("refused") or route_result.get("analysis_type") is None:
        return "this question cannot be answered from satellite imagery"

    if route_result.get("latest"):
        period = "the latest available radar pass"
    text = f"{analysis} for {region}, {period}"
    if route_result.get("pre_start"):
        text += f", compared with {route_result['pre_start']} to {route_result['pre_end']}"

    if route_result.get("fallback_used"):
        # Distinguish "the model could not be reached" from "the model answered
        # and we rejected it". Reporting the wrong one sends whoever debugs this
        # looking at the network when the problem is the output.
        reason = route_result.get("fallback_reason", "")
        if "validation failed" in reason:
            text += " (the language model's reading was rejected; understood by rules)"
        else:
            text += " (understood by rules; the language model was unavailable)"

    return text
