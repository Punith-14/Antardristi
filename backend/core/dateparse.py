"""
Day ranges as people write them in a question.

"How much of Assam was flooded between 20 and 31 July 2026?" was read as the
whole of July: the rules only knew months, and they take precedence over the
model for dates. The analysis measured 1-31 July (3,054 km2 instead of 2,243)
and the question check passed it, because it too compared months only.

Recognised, with or without ordinals (20th) and with -, to, and, till, until:

    20 to 31 July 2026          between 20 and 31 July 2026
    20 July to 5 August 2026    20 July 2026 - 5 August 2026
    July 20 to 31, 2026         July 20 - August 5, 2026

Pure arithmetic on the text, so both the router and the question check can use
it without either trusting the other's reading.
"""

import re
from datetime import date

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_M = "(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b\.?"
_D = r"(\d{1,2})(?!\d)(?:st|nd|rd|th)?"
_Y = r"(?:,?\s*((?:19|20)\d{2}))?"
_SEP = r"\s*(?:-|–|—|to|and|till|until|through)\s*"

# 20 [July [2026]] to 31 July [2026]
DAY_FIRST = re.compile(rf"\b{_D}(?:\s+{_M})?{_Y}{_SEP}{_D}\s+{_M}{_Y}", re.IGNORECASE)
# July 20 [, 2026] to [August] 31 [, 2026]
MONTH_FIRST = re.compile(rf"\b{_M}\s+{_D}{_Y}{_SEP}(?:{_M}\s+)?{_D}\b{_Y}", re.IGNORECASE)
ANY_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


def _make(year, month, day):
    try:
        return date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None


def _range(d1, m1, y1, d2, m2, y2, default_year):
    first_year_written = bool(y1)
    m2 = MONTHS[m2.lower()]
    m1 = MONTHS[m1.lower()] if m1 else m2
    y2 = int(y2 or y1 or default_year)
    y1 = int(y1 or y2)
    start, end = _make(y1, m1, d1), _make(y2, m2, d2)
    if start and end and start > end and not first_year_written:
        start = _make(y1 - 1, m1, d1)                    # 28 Dec to 3 Jan 2026
    if not start or not end or start > end:
        return None
    return start, end


def day_ranges(text, default_year=None):
    """[(start, end)] dates for every day range in the text, in order written.

    default_year: the year when none is written next to the range; the first
    year anywhere in the text if not given, else this year.
    """
    text = text or ""
    if default_year is None:
        found = ANY_YEAR.search(text)
        default_year = int(found.group(1)) if found else date.today().year

    hits = []
    for m in DAY_FIRST.finditer(text):
        d1, m1, y1, d2, m2, y2 = m.groups()
        r = _range(d1, m1, y1, d2, m2, y2, default_year)
        if r:
            hits.append((m.start(), r))
    for m in MONTH_FIRST.finditer(text):
        m1, d1, y1, m2, d2, y2 = m.groups()
        r = _range(d1, m1, y1, d2, m2 or m1, y2, default_year)
        if r and not any(abs(pos - m.start()) < 3 for pos, _ in hits):
            hits.append((m.start(), r))
    return [r for _, r in sorted(hits)]
