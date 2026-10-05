"""Day ranges in questions (core/dateparse.py), the router and the question check.

Found live: "How much of Assam was flooded between 20 and 31 July 2026?" was
measured as all of July, and the question check passed it 5 of 5.
"""

from datetime import date

import pytest

from core import alignment, dateparse
from pipeline import routing

D = date


@pytest.mark.parametrize("text,expected", [
    ("between 20 and 31 July 2026", (D(2026, 7, 20), D(2026, 7, 31))),
    ("from 20th to 31st july 2026", (D(2026, 7, 20), D(2026, 7, 31))),
    ("20-31 Jul 2026", (D(2026, 7, 20), D(2026, 7, 31))),
    ("20 July to 5 August 2026", (D(2026, 7, 20), D(2026, 8, 5))),
    ("20 July 2026 - 5 August 2026", (D(2026, 7, 20), D(2026, 8, 5))),
    ("July 20 to 31, 2026", (D(2026, 7, 20), D(2026, 7, 31))),
    ("July 20 - August 5, 2026", (D(2026, 7, 20), D(2026, 8, 5))),
    ("28 Dec to 3 Jan 2026", (D(2025, 12, 28), D(2026, 1, 3))),
])
def test_day_ranges_as_people_write_them(text, expected):
    assert dateparse.day_ranges(text) == [expected]


@pytest.mark.parametrize("text", [
    "flooding in Kerala in August 2018",
    "Kerala in August 2018 compared to May 2018",
    "between 2018 and 2019",
    "July 2026 to August 2026",
    "31 to 30 June 2026",
    "kharif 2023",
])
def test_months_years_and_nonsense_are_not_day_ranges(text):
    assert dateparse.day_ranges(text) == []


def test_two_ranges_keep_their_order():
    got = dateparse.day_ranges("20-31 July 2026 compared to 1 to 10 June 2026")
    assert got == [(D(2026, 7, 20), D(2026, 7, 31)), (D(2026, 6, 1), D(2026, 6, 10))]


def test_the_router_reads_days_not_the_whole_month():
    today = D(2026, 10, 5)
    assert routing.detect_dates("How much of Assam was flooded between 20 and 31 July 2026?", today) == \
        ("2026-07-20", "2026-07-31")
    assert routing.detect_dates("Show flooding in Kerala in August 2018", today) == \
        ("2018-08-01", "2018-08-31"), "months still work"


def test_the_question_check_fails_a_whole_month_for_a_day_range():
    q = "How much of Assam was flooded between 20 and 31 July 2026?"
    wrong = alignment.check(q, {"post_start": "2026-07-01", "post_end": "2026-07-31"})
    assert not wrong["passed"]
    assert any("20 Jul 2026 to 31 Jul 2026" in f for f in wrong["failures"])
    right = alignment.check(q, {"post_start": "2026-07-20", "post_end": "2026-07-31"})
    assert right["passed"], right["failures"]
