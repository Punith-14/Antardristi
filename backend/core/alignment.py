"""
Does the answer address the question that was asked?

This is a different check from the one in verification.py, and the difference
is the whole point.

`verify_report` reads the prose and confirms every number in it appears in the
evidence record. It is very good at that. It is structurally incapable of
noticing that the evidence record measured the wrong thing, because it only
ever compares the report against the evidence — never either against the
question.

The failure that made this obvious: asked

    "How much did flooding increase in Kerala in August 2018
     compared to May 2018?"

the router put May in both the post and the pre window. The system measured
May against May, `net_new_water` came out at exactly 0.0 km2, the report stated
"the same extent was present in the baseline period", and verification passed
**16 of 16 claims with completeness 1.0**. Every number was faithful to an
evidence record that answered a question nobody asked.

So this module compares the *route* against the *question*: the dates named,
the place named, and whether a question asking for change actually produced a
comparison. It reads the question text directly rather than trusting the
router's own summary of it, because the router is the component under suspicion.

What it deliberately does NOT do: decide whether the analysis type is right, or
whether the numbers are plausible. Those need judgement. These checks are
arithmetic on what the user typed, which is why they can run on every request
without a model and without a doubt about their own reliability.
"""

import re
from datetime import date

# Month names as people write them, mapped to numbers. Short forms included
# because "Aug 2018" is at least as common as "August 2018".
MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# Words that mean the user wants a change, not a snapshot. Kept in step with
# the comparison keywords in pipeline/routing.py - if a question uses one of
# these and the route came back with no baseline, something was dropped.
CHANGE_WORDS = {
    "increase", "increased", "decrease", "decreased", "change", "changed",
    "compared", "comparison", "versus", "vs", "before", "after", "since",
    "grown", "growth", "shrunk", "shrank", "expansion", "expanded", "loss",
    "lost", "gained", "trend", "more than", "less than",
}

MONTH_YEAR = re.compile(
    r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b[\s,]*((?:19|20)\d{2})",
    re.IGNORECASE,
)
BARE_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


def months_named(question):
    """Every (year, month) pair the question names, in the order written.

    "August 2018 compared to May 2018" -> [(2018, 8), (2018, 5)]

    Order is preserved because it carries meaning: the first period named is
    usually the one being asked about, and the second is what it is compared
    against.
    """
    found = []
    for name, year in MONTH_YEAR.findall(question or ""):
        found.append((int(year), MONTHS[name.lower()]))
    return found


def years_named(question):
    """Bare years, excluding those already captured as part of a month."""
    text = question or ""
    consumed = {year for _, year in MONTH_YEAR.findall(text)}
    return [int(y) for y in BARE_YEAR.findall(text) if y not in consumed]


def wants_change(question):
    """Whether the question asks for a change rather than a snapshot."""
    lowered = (question or "").lower()
    return any(
        re.search(rf"\b{re.escape(word)}\b", lowered) for word in CHANGE_WORDS
    )


def _window_month(start):
    """(year, month) of an ISO date string, or None."""
    if not start:
        return None
    try:
        parsed = date.fromisoformat(start[:10])
    except (TypeError, ValueError):
        return None
    return (parsed.year, parsed.month)


def check(question, route):
    """Compare a route against the question it came from.

    Returns a dict shaped like the other verification blocks:

        {
          "passed": bool,
          "checks": [{"name", "passed", "detail"}, ...],
          "failures": [str, ...],
          "checked_by": "question_route_alignment_v1",
        }

    A failure means the analysis is about to measure something other than what
    was asked. That is worth surfacing even when every number in the eventual
    report will be perfectly faithful - especially then, because faithful
    numbers are exactly what makes the wrong answer convincing.
    """
    checks = []

    def record(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    post = _window_month(route.get("post_start"))
    pre = _window_month(route.get("pre_start"))
    named = months_named(question)
    asked_for_change = wants_change(question)

    # --- 1. a comparison question produced a comparison ----------------------
    has_baseline = bool(route.get("pre_start") and route.get("pre_end"))
    record(
        "change_question_has_baseline",
        not asked_for_change or has_baseline,
        "The question asks for a change but no baseline window was set, so the "
        "answer will describe a single moment rather than a difference."
        if asked_for_change and not has_baseline
        else "ok",
    )

    # --- 2. the two windows differ -------------------------------------------
    # The May-vs-May failure. routing.validate now rejects this too; keeping it
    # here as well is deliberate - this check sees the final route, after every
    # merge and fallback, which is the thing that actually gets measured.
    distinct = not has_baseline or (
        (route.get("pre_start"), route.get("pre_end"))
        != (route.get("post_start"), route.get("post_end"))
    )
    record(
        "windows_are_distinct",
        distinct,
        "The baseline window is identical to the analysis window, so any "
        "change figure will be exactly zero by construction."
        if not distinct else "ok",
    )

    # --- 3. every month named appears somewhere in the route ----------------
    # The check that would have caught August being dropped.
    windows = {w for w in (post, pre) if w}
    missing = [ym for ym in named if ym not in windows]
    record(
        "named_periods_are_used",
        not missing,
        "The question names "
        + ", ".join(f"{m:02d}/{y}" for y, m in missing)
        + " but the analysis does not cover "
        + ("it." if len(missing) == 1 else "them.")
        if missing else "ok",
    )

    # --- 4. when two periods are named, the later one is the subject ---------
    # "August compared to May" means August is the subject and May the
    # baseline. Reversing them flips the sign of every change figure.
    ordered = True
    if len(named) >= 2 and post and pre:
        ordered = pre < post
    record(
        "baseline_precedes_the_event",
        ordered,
        "The baseline window is later than the analysis window, which inverts "
        "the sign of every change figure."
        if not ordered else "ok",
    )

    # --- 5. a year named without a month still lands in range ---------------
    route_years = {w[0] for w in windows}
    stray = [y for y in years_named(question) if route_years and y not in route_years]
    record(
        "named_years_are_used",
        not stray,
        f"The question names {', '.join(str(y) for y in stray)} but the "
        "analysis covers " + ", ".join(str(y) for y in sorted(route_years)) + "."
        if stray else "ok",
    )

    failures = [c["detail"] for c in checks if not c["passed"]]
    return {
        "passed": not failures,
        "checks": checks,
        "failures": failures,
        "checked_by": "question_route_alignment_v1",
    }


def summarise(result):
    """One line for a log or a terminal."""
    if result["passed"]:
        return f"alignment ok ({len(result['checks'])} checks)"
    return f"alignment FAILED: {'; '.join(result['failures'])}"
