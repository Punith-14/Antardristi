"""
A person's past analyses, for the Home dashboard and the History page.

Built from the jobs table: every analysis, question and monthly series runs as
a job, and a finished job keeps its full result. This turns each one into a
short row - what, where, when, the headline figure - without sending the whole
result (zones, evidence, map tiles) to a list that only needs a line.

The headline is read from the evidence record, never recomputed, so the
number on a history card is the number in the report.
"""

KINDS = {"analyze": "Analysis", "surface": "Analysis", "ask": "Question", "series": "Monthly series"}


def _evidence(result, quantity=None, unit=None):
    for item in result.get("evidence") or []:
        if quantity and item.get("quantity") != quantity:
            continue
        if unit and item.get("unit") != unit:
            continue
        if isinstance(item.get("value"), (int, float)):
            return item
    return None


def headline(result):
    """{label, value, unit} for the figure a card leads with, or None.

    Flood extent when there is one; otherwise the first area in km2 (the
    surface analyses report their measured class area that way).
    """
    if not isinstance(result, dict):
        return None
    item = _evidence(result, "flood_extent") or _evidence(result, unit="km2")
    if item is None:
        return None
    label = "Flooded" if item["quantity"] == "flood_extent" else \
        item["quantity"].replace("_", " ").capitalize()
    return {"label": label, "value": item["value"], "unit": item["unit"]}


def people(result):
    """{low, high} people living in the flooded area, or None."""
    found = ((result or {}).get("population") or {}).get("people_in_flood")
    if isinstance(found, dict) and found.get("low") is not None:
        return {"low": found.get("low"), "high": found.get("high")}
    return None


def _period(result, request):
    post = ((result or {}).get("period") or {}).get("post") or {}
    if post.get("start"):
        return {"start": post.get("start"), "end": post.get("end")}
    start = request.get("post_start") or request.get("start")
    end = request.get("post_end") or request.get("end")
    return {"start": start, "end": end} if start else None


def _place(result, request):
    region = (result or {}).get("region") or {}
    if region.get("name"):
        return region["name"]
    if request.get("region"):
        return str(request["region"]).title()
    if request.get("question"):
        return None
    return "Drawn area"


def summarise(job, request=None):
    """One history row for a job (with its result), or None for unfinished jobs."""
    if job.get("status") != "done":
        return None
    request = request or {}
    result = job.get("result") or {}
    kind = job.get("kind")
    row = {
        "job_id": job["id"],
        "kind": kind,
        "kind_label": KINDS.get(kind, kind),
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
        "request_id": result.get("request_id"),
        "ask_id": result.get("ask_id"),
        "analysis": result.get("analysis_label")
                    or ("Flood extent" if kind in ("analyze", "series") else None),
        "place": _place(result, request),
        "period": _period(result, request),
        "question": request.get("question"),
        "headline": headline(result),
        "people": people(result),
        "verified": bool((result.get("verification") or {}).get("passed")),
    }
    if kind == "series":
        points = result.get("points") or []
        row["months"] = len(points)
        row["request_id"] = None
        observed = [p for p in points if isinstance(p.get("value"), (int, float))]
        if observed:
            top = max(observed, key=lambda p: p["value"])
            row["headline"] = {"label": f"Peak ({top.get('label')})", "value": top["value"],
                               "unit": top.get("unit") or "km2"}
    return row
