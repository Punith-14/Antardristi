"""
"What does the flood look like now?" - and an honest answer to "now".

Radar does not watch continuously. Sentinel-1 passes over a given place every
few days, and Earth Engine may take a day or two more to ingest a scene. So
"now" means "the most recent pass", and the answer must say how old that is.

Pure functions here, so the date arithmetic is tested without Earth Engine:

    window(date)              the single day of the latest pass
    extended_window(date, n)  that day and the n before it, when one pass did
                              not cover enough of the area
    age_days(last, today)     how old the newest image is, worked out when the
                              result is SHOWN - a result cached last week must
                              not still say "1 day old"
    next_pass_estimate(days)  when the next pass is likely, from the gaps
                              between recent passes - an estimate, labelled so
    asks_for_latest(text)     whether a question means "now"

Earth Engine's filterDate end is exclusive, so a one-day window ends the
following day.
"""

import re
from datetime import date, datetime, timedelta, timezone
from statistics import median

LOOKBACK_DAYS = 60

# A Sentinel-1 orbit repeats every 12 days per satellite; with two in
# operation a site is revisited roughly every 6. Used only to size the
# extended window, never to claim when a pass will happen.
EXTEND_DAYS = 6

LOW_COVERAGE = 0.9

NOW_WORDS = (
    "now", "right now", "currently", "current", "latest", "today", "at present",
    "at the moment", "presently", "this week", "most recent", "recent",
)


def _day(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def window(day):
    """(start, end) covering just the day of the latest pass."""
    d = _day(day)
    return d.isoformat(), (d + timedelta(days=1)).isoformat()


def extended_window(day, days=EXTEND_DAYS):
    """(start, end) from `days` before the latest pass through its day."""
    d = _day(day)
    return (d - timedelta(days=days)).isoformat(), (d + timedelta(days=1)).isoformat()


def days_from_ms(times_ms):
    """Distinct UTC days, sorted, from Earth Engine millisecond timestamps."""
    days = {
        datetime.fromtimestamp(t / 1000, tz=timezone.utc).date()
        for t in times_ms if t is not None
    }
    return sorted(d.isoformat() for d in days)


def age_days(last_day, today=None):
    """Whole days between the newest image and today. Never negative."""
    today = today or datetime.now(timezone.utc).date()
    return max(0, (today - _day(last_day)).days)


def describe_age(days):
    if days == 0:
        return "today"
    if days == 1:
        return "1 day old"
    return f"{days} days old"


def next_pass_estimate(days, minimum=3):
    """When the next pass is likely, from the typical gap between recent ones.

    Returns None with fewer than `minimum` passes: two dates give one gap,
    which is not a pattern. Labelled an estimate wherever it is shown -
    acquisition plans change and Earth Engine ingestion adds delay.
    """
    distinct = sorted({_day(d) for d in days})
    if len(distinct) < minimum:
        return None
    gaps = [(b - a).days for a, b in zip(distinct, distinct[1:]) if (b - a).days > 0]
    if not gaps:
        return None
    typical = int(round(median(gaps)))
    return {
        "expected_on": (distinct[-1] + timedelta(days=typical)).isoformat(),
        "typical_gap_days": typical,
        "based_on_passes": len(distinct),
        "note": "estimate from recent passes; not a published acquisition plan",
    }


def asks_for_latest(text):
    """Whether a question means 'now' - and names no date of its own.

    "Is Assam flooded right now?" -> True. "Flooding in Assam in July 2022" ->
    False, even though it has no "now" word to trip on. "Latest flooding in
    Assam in 2022" -> False: an explicit year wins over a loose "latest".
    """
    lowered = (text or "").lower()
    if re.search(r"\b(19|20)\d{2}\b", lowered):
        return False
    return any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in NOW_WORDS)


def freshness(acquisition, today=None):
    """The acquisition block with its age filled in for TODAY.

    Called when a result is returned, never when it is cached.
    """
    if not acquisition or not acquisition.get("last"):
        return acquisition
    out = dict(acquisition)
    out["age_days"] = age_days(out["last"], today)
    out["age_text"] = describe_age(out["age_days"])
    return out
