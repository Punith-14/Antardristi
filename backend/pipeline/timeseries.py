"""
A quantity over time: one full analysis per month, each with its own evidence.

The obvious way to build a flood time series is to compute a number per month
and draw a line through them. The line is where it goes wrong, because a line
asserts that every point measured the same thing, and in satellite data that
is often false without anything in the numbers showing it:

- **A month with no imagery is not a month with no water.** Drawn as zero, it
  becomes a dry month; drawn by joining its neighbours, it becomes an
  interpolated one. It is neither. It is a gap, and it is kept as one.

- **Less ground seen means fewer km2, not less water.** If August saw 90% of
  a state and September 40%, September's extent is smaller partly because it
  covers less. Such a point is flagged, and the percentage-of-observed-area
  series is offered beside the km2 one for exactly this reason.

- **A different method is a different quantity.** Where dual-polarisation
  scenes are missing, the SAR path falls back to VV alone at a different
  threshold. A jump between those months is partly the method changing.

- **A different orbit is a different view.** Sentinel-1 picks the dominant
  relative orbit per window. From another orbit, radar shadow and layover
  fall differently, so part of any change is viewing geometry.

- **A different sensor is a different instrument.** Automatic routing can
  pick optical one month and radar the next. The series therefore requires
  the sensor to be named, and refuses to let cloud cover decide per month.

Every point is a complete analysis cached under its own request_id, so any
single month can be opened, checked against its evidence record and exported
as a PDF on its own. The series adds no numbers of its own; it only arranges
and flags numbers that already exist in those records.
"""

from calendar import monthrange
from collections import Counter
from datetime import date

# Twelve months of Sentinel-1 analyses is roughly twenty minutes of Earth
# Engine time on a first run. More than that should be a batch job, not an
# HTTP request someone is waiting on.
MAX_WINDOWS = 12

# Below this share of the region observed, a point's km2 figure covers too
# little ground to sit on the same axis as its neighbours without a warning.
# Matches coverage_warning() in core/evidence.py so the two never disagree
# about what counts as partial.
LOW_COVERAGE = 0.9

SERIES_QUANTITY = "flood_extent"
FRACTION_QUANTITY = "flood_extent_fraction"


class InvalidSeries(ValueError):
    """The requested series cannot be built, and says why."""


# ------------------------------------------------------------- the windows

def _parse(text, name):
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError) as exc:
        raise InvalidSeries(f"{name} must be a date like 2018-06-01, got {text!r}.") from exc


def monthly_windows(start, end, limit=MAX_WINDOWS):
    """Calendar months from start to end, clipped to the range.

    2018-06-15 to 2018-08-10 gives:
        2018-06-15 .. 2018-06-30
        2018-07-01 .. 2018-07-31
        2018-08-01 .. 2018-08-10

    Calendar months, not 30-day blocks, so a point labelled "August" means
    August and matches the month a person asking about August has in mind.
    The first and last windows are clipped rather than extended, so no
    analysis reaches outside the dates that were asked for.
    """
    first = _parse(start, "start")
    last = _parse(end, "end")
    if last < first:
        raise InvalidSeries(f"end ({last}) is before start ({first}).")

    windows = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        month_start = date(year, month, 1)
        month_end = date(year, month, monthrange(year, month)[1])
        windows.append({
            "start": max(month_start, first).isoformat(),
            "end": min(month_end, last).isoformat(),
            "label": f"{year}-{month:02d}",
        })
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    if len(windows) > limit:
        raise InvalidSeries(
            f"That range is {len(windows)} months, over the {limit}-month limit "
            f"for one request (each month is a full Earth Engine analysis). "
            "Ask for a shorter range, or run it in parts - completed months "
            "are cached, so the parts join up for free."
        )
    return windows


# --------------------------------------------------------------- the points

def _evidence(result, quantity):
    for item in result.get("evidence") or []:
        if item.get("quantity") == quantity:
            return item
    return None


def point_from(window, result):
    """One month's point, read from that month's own result. No arithmetic.

    `result` is whatever /analyze returned for the window, including its
    no-imagery response. A missing result (None) is treated the same way: the
    month was not observed.
    """
    result = result or {}
    observation = result.get("observation") or {}
    unobserved = result.get("unobserved") or {}
    extent = _evidence(result, SERIES_QUANTITY)
    fraction = _evidence(result, FRACTION_QUANTITY)

    point = {
        "window": {"start": window["start"], "end": window["end"]},
        "label": window["label"],
        "request_id": result.get("request_id"),
        "observed": extent is not None,
        "value": extent["value"] if extent else None,
        "unit": extent["unit"] if extent else None,
        "evidence_id": extent["id"] if extent else None,
        "fraction": (
            {"value": fraction["value"], "unit": fraction["unit"],
             "evidence_id": fraction["id"]}
            if fraction else None
        ),
        "method": extent.get("method") if extent else None,
        "sensor": observation.get("sensor_used"),
        "relative_orbit": observation.get("relative_orbit"),
        "observable_area_km2": observation.get("observable_area_km2"),
        "coverage_fraction": observation.get("coverage_fraction"),
        "scenes_used": observation.get("scenes_used"),
        "flags": [],
    }

    if not point["observed"]:
        reason = "; ".join(unobserved.get("notes") or []) or "no result"
        point["flags"].append({
            "kind": "unobserved",
            "text": (
                f"{window['label']} was not observed ({reason}). This is a gap "
                "in the series, not a month without water."
            ),
        })
        return point

    coverage = point["coverage_fraction"]
    if coverage is not None and coverage < LOW_COVERAGE:
        point["flags"].append({
            "kind": "low_coverage",
            "text": (
                f"Only {coverage * 100:.0f}% of the area was observed in "
                f"{window['label']}, so its km² figure covers less ground than "
                "fuller months. Compare the percentage of observed area instead."
            ),
        })
    return point


def _most_common(values):
    present = [v for v in values if v is not None]
    return Counter(present).most_common(1)[0][0] if present else None


def check_comparability(points):
    """Flag points that measured something different from the rest.

    "Different" is judged against the most common value across observed
    points, so one odd month is flagged rather than every normal one.
    """
    observed = [p for p in points if p["observed"]]
    usual = {
        "sensor": _most_common(p["sensor"] for p in observed),
        "method": _most_common(p["method"] for p in observed),
        "relative_orbit": _most_common(p["relative_orbit"] for p in observed),
    }

    for point in observed:
        if usual["sensor"] and point["sensor"] != usual["sensor"]:
            point["flags"].append({
                "kind": "sensor_differs",
                "text": (
                    f"{point['label']} was measured by {point['sensor']}, the "
                    f"others by {usual['sensor']}. A change here is partly a "
                    "change of instrument."
                ),
            })
        if usual["method"] and point["method"] != usual["method"]:
            point["flags"].append({
                "kind": "method_differs",
                "text": (
                    f"{point['label']} used a different method ({point['method']}). "
                    "A change here is partly a change of method."
                ),
            })
        if (usual["relative_orbit"] is not None and point["relative_orbit"] is not None
                and point["relative_orbit"] != usual["relative_orbit"]):
            point["flags"].append({
                "kind": "orbit_differs",
                "text": (
                    f"{point['label']} was viewed from relative orbit "
                    f"{point['relative_orbit']}, the others mostly from "
                    f"{usual['relative_orbit']}. Radar shadow and layover fall "
                    "differently, so part of any change is viewing geometry."
                ),
            })

    breaking = {"sensor_differs", "method_differs", "orbit_differs"}
    notes = []
    gaps = [p["label"] for p in points if not p["observed"]]
    if gaps:
        notes.append(
            f"Not observed: {', '.join(gaps)}. Shown as gaps; no value is "
            "interpolated or assumed for them."
        )
    partial = [p["label"] for p in points
               if any(f["kind"] == "low_coverage" for f in p["flags"])]
    if partial:
        notes.append(
            f"Partly observed: {', '.join(partial)}. Their km² figures cover "
            "less ground; the percentage series is the fairer comparison."
        )
    broken = [p["label"] for p in points
              if any(f["kind"] in breaking for f in p["flags"])]
    if broken:
        notes.append(
            f"Measured differently from the rest: {', '.join(broken)}. "
            "Differences involving these months are not purely change on the ground."
        )

    return {
        "comparable": not broken,
        "usual": usual,
        "gaps": gaps,
        "partial": partial,
        "differently_measured": broken,
        "notes": notes,
    }


def build(windows, results, region=None, sensor=None):
    """The series response. `results` is one per window, in the same order."""
    if len(windows) != len(results):
        raise InvalidSeries("one result is needed per window")

    points = [point_from(w, r) for w, r in zip(windows, results)]
    comparability = check_comparability(points)

    region = region or next(
        (r.get("region") for r in results if r and r.get("region")), None
    )

    observed = [p for p in points if p["observed"]]
    return {
        "schema_version": "1.0",
        "kind": "time_series",
        "quantity": SERIES_QUANTITY,
        "unit": observed[0]["unit"] if observed else "km2",
        "region": region,
        "sensor": sensor,
        "windows": len(points),
        "observed_windows": len(observed),
        "points": points,
        "comparability": comparability,
        "provenance": {
            "note": (
                "Each point is a complete analysis with its own evidence record "
                "and request_id. The series adds no figures of its own; fetch "
                "any month with GET /analyze/{request_id}."
            ),
        },
    }
