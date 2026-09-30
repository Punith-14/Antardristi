"""
PDF export of a finished analysis.

The PDF is the one form of an answer that leaves the system. Once it is
attached to an email nobody can click a citation, open the evidence panel or
see the verification badge - so everything that makes a number trustworthy on
screen has to be printed next to the number on paper, or it is lost.

Three rules follow from that:

1. **Nothing is recomputed.** Every figure comes from the stored response for
   a request_id, exactly as the API returned it. The PDF is a view of the
   evidence record, not a second analysis that could disagree with it.

2. **A failure is printed louder than a success.** If verification did not
   pass, the question and the route did not align, or the LLM report was
   replaced by the template fallback, that goes in a banner above the finding
   - not in a footnote. A clean-looking PDF of an unverified answer is the
   single most misleading thing this module could produce.

3. **The caveats travel with the numbers.** Validation scores, the unobserved
   area, the boundary vintage and the known confusions are printed in full.
   On screen they are one click away; in a PDF they are either there or gone.

Split in two so the first half is testable without parsing a PDF:

    outline(result) -> plain dict of what the document says
    render(result)  -> PDF bytes drawn from that outline

Tests assert on the outline (does the failure banner appear, is every
evidence row present, are the caveats there). One test renders and extracts
text to confirm the drawing layer does not drop any of it.
"""

import html
import math
from datetime import datetime, timezone
from io import BytesIO

# Characters the report generator emits that the PDF's built-in fonts cannot
# draw. The LLM writes "486.4 km²" and "Sentinel‑1"; Helvetica has
# no glyph for a narrow no-break space or a non-breaking hyphen, and reportlab
# renders a missing glyph as a black box. Nobody reading a printed report can
# tell "486.4 km²" from "486.4■km²" is a font problem rather than a typo in
# the number, so these are normalised before drawing.
_REPLACEMENTS = {
    " ": " ",   # narrow no-break space
    " ": " ",   # no-break space
    " ": " ",   # thin space
    "‑": "-",   # non-breaking hyphen
    "‐": "-",   # hyphen
    "−": "-",   # minus sign - matters: "-20 dB" must not lose its sign
    "≈": "~",   # almost equal
    "≤": "<=",
    "≥": ">=",
    "→": "->",
    "←": "<-",
    "×": "x",
}

TITLE = "Antardrishti analysis report"


# --------------------------------------------------------------- text safety

def safe(text):
    """Text the PDF's built-in fonts can draw, character for character.

    Known substitutions first, then anything else outside Windows-1252 becomes
    '?' - visible, rather than silently dropped. A dropped character in a
    number is a different number.
    """
    if text is None:
        return ""
    text = str(text)
    for bad, good in _REPLACEMENTS.items():
        text = text.replace(bad, good)
    return text.encode("cp1252", errors="replace").decode("cp1252")


def para(text):
    """Safe text, escaped for reportlab's Paragraph mini-markup.

    Paragraph parses its input as XML-ish markup. A report sentence such as
    "recall < 0.6 & precision > 0.7" would otherwise either raise or silently
    swallow everything after the '<' - and the part swallowed could be the
    caveat.
    """
    return html.escape(safe(text), quote=False)


# -------------------------------------------------------------- formatting

UNIT_LABELS = {"km2": "km²", "percent": "%", "dB": "dB", "count": ""}


def format_value(value, unit):
    """A number with its unit, as the evidence record states it."""
    if value is None:
        return "—"
    label = UNIT_LABELS.get(unit, unit or "")
    if isinstance(value, float):
        # Printed as stored. Rounding here would make the PDF disagree with
        # the report text it sits beside, which quotes the stored value.
        shown = f"{value:,}" if abs(value) >= 1000 else repr(value)
    else:
        shown = f"{value:,}" if isinstance(value, int) else str(value)
    if unit == "percent":
        return f"{shown}%"
    return f"{shown} {label}".strip()


def _period_text(period):
    if not period:
        return "period not recorded"
    post = period.get("post") or {}
    text = f"{post.get('start', '?')} to {post.get('end', '?')}"
    pre = period.get("pre")
    if pre:
        text += f", compared with {pre.get('start', '?')} to {pre.get('end', '?')}"
    return text


# ------------------------------------------------------------- the outline

def _status(result):
    """What the reader must know before believing the finding.

    Returns {"level": "ok" | "warn" | "none", "headline": str, "lines": [...]}.
    "none" means there is nothing to verify - no report was generated - which
    is different from a report that passed and must not be printed as one.
    """
    lines = []
    report = result.get("report") or {}
    verification = result.get("verification")
    alignment = result.get("alignment")

    if verification is None:
        return {
            "level": "none",
            "headline": "Not verified: no report was generated for this analysis.",
            "lines": ["The figures below are the evidence record alone."],
        }

    passed = bool(verification.get("passed"))
    rate = verification.get("faithfulness_rate")
    total = verification.get("claims_total")
    supported = verification.get("claims_supported")
    caveats = verification.get("caveats") or {}

    if supported is not None and total is not None:
        lines.append(
            f"Numbers traced to evidence: {supported} of {total}"
            + (f" (faithfulness {rate})" if rate is not None else "")
        )
    if caveats:
        lines.append(
            f"Caveats kept: {caveats.get('caveats_mentioned', '?')} of "
            f"{caveats.get('caveats_required', '?')} "
            f"(completeness {caveats.get('completeness', '?')})"
        )

    problems = []
    for claim in verification.get("unsupported_claims") or []:
        problems.append(f"Unsupported number in the text: {claim.get('claim', claim)}")
    for cite in verification.get("invalid_citations") or []:
        problems.append(f"Citation to evidence that does not exist: {cite}")
    for note in caveats.get("omitted_caveats") or []:
        problems.append(f"Caveat left out of the text: {note}")

    if alignment and not alignment.get("passed", True):
        passed = False
        for failure in alignment.get("failures") or []:
            problems.append(f"Question and analysis do not match: {failure}")

    if report.get("fallback_used"):
        # Not a failure - the template report is deterministic and verified -
        # but the reader should know the prose was not written by the model
        # named in the provenance.
        lines.append(
            "Report text is the deterministic template, not the language model"
            + (f" ({report['fallback_reason']})" if report.get("fallback_reason") else "")
            + "."
        )

    uncited = verification.get("uncited_evidence") or []
    if uncited:
        lines.append(
            "Evidence recorded but not cited in the text: " + ", ".join(uncited)
        )

    if passed and not problems:
        return {
            "level": "ok",
            "headline": "Verified: every number in the finding is in the evidence record.",
            "lines": lines,
        }
    return {
        "level": "warn",
        "headline": "FAILED VERIFICATION - read the problems below before using this finding.",
        "lines": problems + lines,
    }


def _evidence_rows(evidence):
    rows = []
    for item in evidence or []:
        detail = (item.get("method") or item.get("note") or "").rstrip()
        if item.get("derived_from"):
            source = "Derived from " + ", ".join(item["derived_from"]) + "."
            if detail and not detail.endswith("."):
                detail += "."
            detail = f"{detail} {source}".strip()
        rows.append({
            "id": item.get("id", ""),
            "quantity": (item.get("quantity") or "").replace("_", " "),
            "value": format_value(item.get("value"), item.get("unit")),
            "confidence": (
                "" if item.get("confidence") is None else str(item["confidence"])
            ),
            "detail": detail,
        })
    return rows


def _coverage_rows(result):
    observation = result.get("observation") or {}
    unobserved = result.get("unobserved") or {}
    rows = []

    def add(label, value):
        if value is not None and value != "":
            rows.append((label, str(value)))

    add("Sensor", observation.get("sensor_used"))
    reason = observation.get("sensor_reason")
    if reason:
        # "forced_by_request:sentinel-1" -> "forced by request: sentinel-1"
        reason = reason.replace("_", " ").replace(":", ": ")
    add("Why this sensor", reason)
    add(
        "Scenes used",
        f"{observation.get('scenes_used')} of {observation.get('scenes_available')} available"
        if observation.get("scenes_used") is not None else None,
    )
    if observation.get("observable_area_km2") is not None:
        add("Area observed", format_value(observation["observable_area_km2"], "km2"))
    if unobserved.get("region_area_km2") is not None:
        add("Area of region", format_value(unobserved["region_area_km2"], "km2"))
    if unobserved.get("masked_area_km2") is not None:
        add("Area not observed", format_value(unobserved["masked_area_km2"], "km2"))
    if observation.get("coverage_fraction") is not None:
        add("Coverage", f"{round(observation['coverage_fraction'] * 100, 1)}%")
    return rows


def _region_rows(region):
    region = region or {}
    rows = [("Area", region.get("name") or region.get("slug") or "unnamed")]

    level = region.get("admin_level")
    if level:
        rows.append(("Kind", "drawn by the user" if level == "custom" else level))
    if region.get("state"):
        rows.append(("State", region["state"]))
    rows.append(("Boundary source", region.get("boundary_source") or "not recorded"))
    # Printed even when None, because "no vintage" is itself the fact the
    # reader needs for a drawn area: it is not a district of any year.
    rows.append(("Boundary vintage", region.get("boundary_vintage") or "none (not an administrative boundary)"))

    shape = region.get("footprint") or {}
    if shape.get("approx_area_km2") is not None:
        rows.append(("Drawn area", format_value(shape["approx_area_km2"], "km2")))
    if region.get("note"):
        rows.append(("Note", region["note"]))
    return rows


def _zone_outlines(result, limit=50):
    """Zone polygons as lists of (lon, lat), for the schematic map."""
    outlines = []
    features = (result.get("zones_geojson") or {}).get("features") or []
    for feature in features[:limit]:
        geometry = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        rings = []
        if geometry.get("type") == "Polygon":
            rings = [geometry["coordinates"][0]]
        elif geometry.get("type") == "MultiPolygon":
            rings = [poly[0] for poly in geometry["coordinates"]]
        for ring in rings:
            if len(ring) >= 3:
                outlines.append({
                    "id": props.get("id"),
                    "rank": props.get("rank"),
                    "severity": props.get("severity"),
                    "ring": [(float(p[0]), float(p[1])) for p in ring],
                })
    return outlines


def _zones(result):
    zones = result.get("zones") or []
    summary = result.get("zones_summary") or {}
    rows = [
        {
            "rank": z.get("rank"),
            "id": z.get("id"),
            "area": format_value(z.get("area_km2"), "km2"),
            "severity": z.get("severity") or "",
            "where": (
                f"{z['centroid'][1]:.3f} N, {z['centroid'][0]:.3f} E"
                if isinstance(z.get("centroid"), (list, tuple)) and len(z["centroid"]) == 2
                else ""
            ),
        }
        for z in zones
    ]

    note = None
    if summary.get("truncated"):
        note = (
            f"{summary.get('count')} zones were found; the {summary.get('listed')} "
            "largest are listed. The rest are included in the totals above "
            "but not in this table."
        )
    markers = [
        {k: z.get(k) for k in ("id", "rank", "area_km2", "severity", "centroid")}
        for z in zones
    ]
    return {"rows": rows, "note": note, "outlines": _zone_outlines(result),
            "markers": markers}


def outline(result, exported_at=None):
    """Everything the PDF will say, as plain data. No drawing.

    Kept separate from render() so tests can check the content - is the
    failure banner there, is every evidence row there - without parsing PDF
    bytes.
    """
    result = result or {}
    region = result.get("region") or {}
    label = result.get("analysis_label") or (
        (result.get("analysis_type") or "flood_extent").replace("_", " ").capitalize()
    )
    unobserved = result.get("unobserved") or {}
    provenance = result.get("provenance") or {}
    report = result.get("report") or {}
    exported_at = exported_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    nothing_seen = unobserved.get("reason") == "no_usable_imagery"

    return {
        "title": f"{label} - {region.get('name') or 'unnamed area'}",
        "period": _period_text(result.get("period")),
        "request_id": result.get("request_id") or "not recorded",
        "schema_version": result.get("schema_version") or "?",
        "generated_at": result.get("generated_at") or "not recorded",
        "exported_at": exported_at,
        "question": (result.get("routing") or {}).get("question") or result.get("question"),
        "nothing_observed": nothing_seen,
        "status": _status(result),
        "finding": report.get("text") or "",
        "generator": report.get("generator_model") or report.get("written_by") or (
            "deterministic template" if report.get("fallback_used") else None
        ),
        "evidence": _evidence_rows(result.get("evidence")),
        "coverage": _coverage_rows(result),
        "caveats": list(unobserved.get("notes") or []),
        "zones": _zones(result),
        "region": _region_rows(region),
        "alignment": result.get("alignment"),
        "provenance": {
            "pipeline_version": provenance.get("pipeline_version"),
            "datasets": provenance.get("datasets") or [],
            "models": provenance.get("models") or [],
            "stats_scale_m": provenance.get("stats_scale_m"),
            "known_confusions": provenance.get("known_confusions") or [],
        },
    }


# ---------------------------------------------------------- schematic map

def _nice_length(km):
    """A round scale-bar length no longer than `km`: 1, 2, 5, 10, 20, 50..."""
    if km <= 0:
        return 0
    exponent = 10 ** math.floor(math.log10(km))
    for step in (5, 2, 1):
        if step * exponent <= km:
            return step * exponent
    return exponent


def schematic_map(outlines, width, height, zones=None):
    """Zone outlines on a plain frame, projected so a kilometre is a kilometre.

    No basemap: the tiles live on Earth Engine and OpenStreetMap servers, and a
    PDF that fetched them at export time would depend on the network and on
    Earth Engine tile URLs that expire. So this is labelled as a schematic -
    shapes, sizes and relative positions, with a scale bar - rather than
    passing itself off as the map on screen.

    Longitude is scaled by cos(latitude), the same correction the circle and
    polygon tools make. Without it every zone is drawn stretched east-west,
    by 3% in Kerala and 20% in Ladakh.

    Outlines alone are not enough. Across a state, a 25 km2 zone is a speck
    a fraction of a millimetre wide - drawn correctly and invisible. So each
    listed zone also gets a centre marker, sized by the square root of its
    area with a floor so the smallest stays visible. The outlines carry the
    true size; the markers carry where. The caption says which is which,
    because a marker drawn bigger than the zone it marks would otherwise read
    as a claim about its size.
    """
    from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, Rect, String
    from reportlab.lib import colors

    font = "Helvetica"      # Drawing strings default to Times otherwise
    drawing = Drawing(width, height)
    drawing.add(Rect(0, 0, width, height, strokeColor=colors.HexColor("#999999"),
                     fillColor=colors.HexColor("#f7f7f5"), strokeWidth=0.5))

    zones = [z for z in (zones or [])
             if isinstance(z.get("centroid"), (list, tuple)) and len(z["centroid"]) == 2]

    points = [p for o in outlines for p in o["ring"]]
    points += [tuple(z["centroid"]) for z in zones]
    if not points:
        drawing.add(String(width / 2, height / 2, "No zone outlines in this analysis",
                           textAnchor="middle", fontSize=8, fontName=font,
                           fillColor=colors.HexColor("#777777")))
        return drawing

    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    lat_mid = math.radians((min(lats) + max(lats)) / 2)
    squash = math.cos(lat_mid)

    x_span = max((max(lons) - min(lons)) * squash, 1e-6)
    y_span = max(max(lats) - min(lats), 1e-6)
    pad = 16
    scale = min((width - 2 * pad) / x_span, (height - 2 * pad - 14) / y_span)

    x_off = (width - x_span * scale) / 2
    y_off = (height - y_span * scale) / 2 + 6

    def project(lon, lat):
        return (x_off + (lon - min(lons)) * squash * scale,
                y_off + (lat - min(lats)) * scale)

    fills = {"high": "#d7301f", "moderate": "#fc8d59", "low": "#fdcc8a"}
    for item in outlines:
        flat = []
        for lon, lat in item["ring"]:
            flat.extend(project(lon, lat))
        drawing.add(Polygon(
            flat,
            fillColor=colors.HexColor(fills.get(item.get("severity"), "#58a6ff")),
            fillOpacity=0.7,
            strokeColor=colors.HexColor("#333333"),
            strokeWidth=0.3,
        ))

    # Centre markers, largest drawn first so small ones stay on top.
    if zones:
        biggest = max((z.get("area_km2") or 0) for z in zones) or 1
        for zone in sorted(zones, key=lambda z: -(z.get("area_km2") or 0)):
            x, y = project(*zone["centroid"])
            radius = max(2.0, 7.0 * math.sqrt((zone.get("area_km2") or 0) / biggest))
            drawing.add(Circle(
                x, y, radius,
                fillColor=colors.HexColor(fills.get(zone.get("severity"), "#58a6ff")),
                fillOpacity=0.55,
                strokeColor=colors.HexColor("#222222"),
                strokeWidth=0.5,
            ))

        # Label the five largest so the map can be matched to the zone table.
        for zone in sorted(zones, key=lambda z: z.get("rank") or 10**6)[:5]:
            x, y = project(*zone["centroid"])
            drawing.add(String(x + 8, y - 2, str(zone.get("id") or zone.get("rank")),
                               fontSize=6.5, fontName=font, fillColor=colors.black))

    # Scale bar: a degree of latitude is 111.32 km everywhere.
    km_per_point = 111.32 / scale
    bar_km = _nice_length(km_per_point * (width * 0.25))
    if bar_km:
        bar = bar_km / km_per_point
        drawing.add(Line(pad, 8, pad + bar, 8, strokeWidth=1.2))
        drawing.add(Line(pad, 5, pad, 11, strokeWidth=1))
        drawing.add(Line(pad + bar, 5, pad + bar, 11, strokeWidth=1))
        drawing.add(String(pad + bar + 4, 5.5, f"{bar_km:g} km", fontSize=6.5,
                           fontName=font))

    # North marker.
    drawing.add(Polygon([width - 14, height - 8, width - 18, height - 18,
                         width - 10, height - 18], fillColor=colors.black,
                        strokeColor=None))
    drawing.add(String(width - 14, height - 27, "N", fontSize=7, fontName=font,
                       textAnchor="middle"))

    caption = "Schematic, no basemap. Outlines to scale"
    if zones:
        caption += "; circles mark zone centres, sized by area, not to scale"
    drawing.add(String(width - pad, 5.5, caption + ".",
                       fontSize=6, fontName=font, textAnchor="end",
                       fillColor=colors.HexColor("#666666")))
    return drawing


# --------------------------------------------------------------- rendering

def render(result, exported_at=None):
    """The analysis as PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    doc_data = outline(result, exported_at=exported_at)

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10.5,
                           textColor=colors.HexColor("#444444"))
    cell = ParagraphStyle("cell", parent=body, fontSize=8, leading=10, alignment=TA_LEFT)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=15, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11.5,
                        spaceBefore=10, spaceAfter=4)

    story = []
    story.append(Paragraph(para(doc_data["title"]), h1))
    story.append(Paragraph(para(doc_data["period"]), body))
    if doc_data["question"]:
        story.append(Paragraph("<i>Asked:</i> " + para(doc_data["question"]), small))
    story.append(Spacer(1, 6))

    # --- status banner -----------------------------------------------------
    status = doc_data["status"]
    tone = {
        "ok": ("#e8f5e9", "#2e7d32"),
        "warn": ("#fdecea", "#c62828"),
        "none": ("#fff8e1", "#8d6e00"),
    }[status["level"]]
    banner_rows = [[Paragraph(f"<b>{para(status['headline'])}</b>", body)]]
    for line in status["lines"]:
        banner_rows.append([Paragraph(para(line), small)])
    banner = Table(banner_rows, colWidths=[174 * mm])
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(tone[0])),
        ("BOX", (0, 0), (-1, -1), 1.2 if status["level"] == "warn" else 0.6,
         colors.HexColor(tone[1])),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(banner)

    # --- finding -----------------------------------------------------------
    story.append(Paragraph("Finding", h2))
    if doc_data["nothing_observed"]:
        story.append(Paragraph(
            "<b>Nothing could be observed.</b> This is not a finding of absence - "
            "the satellite did not see the ground.", body))
    if doc_data["finding"]:
        story.append(Paragraph(para(doc_data["finding"]), body))
        if doc_data["generator"]:
            story.append(Paragraph(
                f"Written by {para(doc_data['generator'])}. Bracketed IDs such as "
                "[E1] refer to rows of the evidence table below.", small))
    else:
        story.append(Paragraph("No report text was generated.", small))

    # --- evidence ----------------------------------------------------------
    if doc_data["evidence"]:
        story.append(Paragraph("Evidence record", h2))
        rows = [[Paragraph(f"<b>{h}</b>", cell) for h in
                 ("ID", "Quantity", "Value", "Conf.", "Method / derivation")]]
        for row in doc_data["evidence"]:
            rows.append([
                Paragraph(para(row["id"]), cell),
                Paragraph(para(row["quantity"]), cell),
                Paragraph(para(row["value"]), cell),
                Paragraph(para(row["confidence"]), cell),
                Paragraph(para(row["detail"]), cell),
            ])
        table = Table(rows, colWidths=[11 * mm, 38 * mm, 27 * mm, 13 * mm, 85 * mm],
                      repeatRows=1)
        table.setStyle(_grid())
        story.append(table)

    # --- coverage and caveats ---------------------------------------------
    if doc_data["coverage"] or doc_data["caveats"]:
        block = [Paragraph("What was and was not observed", h2)]
        if doc_data["coverage"]:
            block.append(_key_value_table(doc_data["coverage"], cell))
        for note in doc_data["caveats"]:
            block.append(Spacer(1, 3))
            block.append(Paragraph("&bull; " + para(note), small))
        story.append(KeepTogether(block))

    # --- zones -------------------------------------------------------------
    zones = doc_data["zones"]
    if zones["rows"] or zones["outlines"]:
        # Heading and map kept together: a heading stranded at the foot of a
        # page with its map on the next was the first thing the render showed.
        head = [Paragraph("Zones", h2)]
        if zones["outlines"] or zones["markers"]:
            head.append(schematic_map(zones["outlines"], 174 * mm, 80 * mm,
                                      zones=zones["markers"]))
            head.append(Spacer(1, 5))
        story.append(KeepTogether(head))
        if zones["note"]:
            story.append(Paragraph(para(zones["note"]), small))
            story.append(Spacer(1, 3))
        if zones["rows"]:
            rows = [[Paragraph(f"<b>{h}</b>", cell) for h in
                     ("Rank", "ID", "Area", "Severity", "Centre")]]
            for z in zones["rows"]:
                rows.append([Paragraph(para(v), cell) for v in
                             (z["rank"], z["id"], z["area"], z["severity"], z["where"])])
            table = Table(rows, colWidths=[14 * mm, 16 * mm, 32 * mm, 24 * mm, 88 * mm],
                          repeatRows=1)
            table.setStyle(_grid())
            story.append(table)

    # --- area and boundary -------------------------------------------------
    story.append(KeepTogether([
        Paragraph("Area and boundary", h2),
        _key_value_table(doc_data["region"], cell),
    ]))

    # --- alignment ---------------------------------------------------------
    alignment = doc_data["alignment"]
    if alignment and alignment.get("checks"):
        block = [Paragraph("Does the analysis answer the question?", h2)]
        rows = [(
            c["name"].replace("_", " "),
            "passed" if c["passed"] else "FAILED - " + c["detail"],
        ) for c in alignment["checks"]]
        block.append(_key_value_table(rows, cell))
        story.append(KeepTogether(block))

    # --- provenance --------------------------------------------------------
    prov = doc_data["provenance"]
    block = [Paragraph("Provenance", h2)]
    rows = []
    if prov["pipeline_version"]:
        rows.append(("Pipeline version", prov["pipeline_version"]))
    if prov["stats_scale_m"]:
        rows.append(("Statistics computed at", f"{prov['stats_scale_m']} m"))
    for dataset in prov["datasets"]:
        rows.append((dataset.get("role", "dataset").capitalize(), dataset.get("id", "")))
    for model in prov["models"]:
        rows.append(("model", model if isinstance(model, str) else str(model)))
    rows.append(("Request ID", doc_data["request_id"]))
    rows.append(("Analysis generated", doc_data["generated_at"]))
    rows.append(("PDF exported", doc_data["exported_at"]))
    block.append(_key_value_table(rows, cell))
    story.append(KeepTogether(block))

    if prov["known_confusions"]:
        story.append(Paragraph("Known ways this method can be wrong", h2))
        for note in prov["known_confusions"]:
            story.append(Paragraph("&bull; " + para(note), small))

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=18 * mm,
        title=safe(f"{TITLE}: {doc_data['title']}"),
        author="Antardrishti",
        subject=safe(f"request {doc_data['request_id']}"),
    )

    footer = safe(
        f"Request {doc_data['request_id']} - every figure is from the evidence "
        f"record at GET /analyze/{doc_data['request_id']}"
    )

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(18 * mm, 10 * mm, footer)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()


def _grid():
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle

    return TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ])


def _key_value_table(rows, cell):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    table = Table(
        [[Paragraph(f"<b>{para(k)}</b>", cell), Paragraph(para(v), cell)] for k, v in rows],
        colWidths=[48 * mm, 126 * mm],
    )
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def filename_for(result):
    """A download name a person can find again: area, period, short id."""
    region = ((result or {}).get("region") or {}).get("slug") or "area"
    post = ((result or {}).get("period") or {}).get("post") or {}
    start = (post.get("start") or "").replace("-", "")
    rid = ((result or {}).get("request_id") or "")[:8]
    parts = ["antardrishti", region, start, rid]
    name = "_".join(p for p in parts if p)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
    return f"{safe_name}.pdf"
