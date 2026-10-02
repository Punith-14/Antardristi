"""
Report generation.

Two generators, one contract: both receive the evidence array and nothing else.

  render_template_report()  - deterministic, no model. Always available.
  generate_llm_report()     - Groq. Better prose, must be verified.

The template path is not a poor relation. It is the outage fallback, and it is
the ablation baseline that tells you whether the LLM is earning its place.
"""

import os
import re

from core.verification import check_caveats, verify_report

PIPELINE_VERSION = "0.4.0"

SYSTEM_PROMPT = """You write short factual summaries of satellite Earth observation results for Indian district administrators.

ABSOLUTE RULES:
1. You may ONLY state numbers that appear in the EVIDENCE list you are given. Never calculate, estimate, round differently, or infer a new figure.
2. Every number you write must be followed by its evidence id in square brackets, like: 187.4 km2 [E1].
3. If the evidence does not support something, do not say it. Say nothing rather than guess.
4. Never describe damage, casualties, population affected, water depth, or cause. The satellite cannot measure these.
5. Every item listed under LIMITATIONS must appear in your report. These are not optional context. Omitting a limitation makes the report misleading even if every number in it is correct. Put them in the final sentences.

STYLE: 3-5 sentences, plain English, no headings, no bullet points, no preamble. Write for someone deciding where to send relief, not for a scientist."""


# ---------------------------------------------------------------- template

def _find(evidence, quantity):
    for item in evidence:
        if item.get("quantity") == quantity:
            return item
    return None


def _fmt(value, unit):
    if unit == "km2":
        return f"{value:,.1f} km2"
    if unit == "percent":
        return f"{value:.2f}%"
    if unit in ("zones", "people"):
        return f"{value:,.0f}"
    return f"{value:,.2f} {unit}".strip()


def render_template_report(payload):
    """Deterministic report. Same inputs, same words, every time."""
    evidence = payload.get("evidence") or []
    region = (payload.get("region") or {}).get("name", "the selected region")
    observation = payload.get("observation") or {}
    unobserved = payload.get("unobserved") or {}
    zones = payload.get("zones") or []

    if not evidence:
        reason = unobserved.get("reason")
        if reason == "no_usable_imagery":
            return (
                f"No usable satellite imagery was available for {region} in the "
                "requested period, so no measurement could be made. This is not a "
                "finding of 'no water' - the ground could not be observed."
            )
        return f"No measurements were produced for {region}."

    sentences = []

    # The primary quantity is whatever the analysis measured. Flood, vegetation,
    # built-up and the rest all take the same sentence shape.
    PRIMARY = (
        "flood_extent", "water_area", "vegetated_area", "dense_vegetation_area",
        "built_up_area", "bare_area", "moisture_stressed_area",
    )
    LABELS = {
        "flood_extent": "Flood water",
        "water_area": "Open water",
        "vegetated_area": "Healthy vegetation",
        "dense_vegetation_area": "Dense green cover",
        "built_up_area": "Built-up land",
        "bare_area": "Bare ground",
        "moisture_stressed_area": "Moisture-stressed vegetation",
    }

    extent = next((_find(evidence, q) for q in PRIMARY if _find(evidence, q)), None)
    fraction = _find(evidence, f"{extent['quantity']}_fraction") if extent else None

    if extent:
        label = LABELS.get(extent["quantity"], "The measured area")
        lead = (
            f"{label} covered {_fmt(extent['value'], extent['unit'])} of "
            f"{region} [{extent['id']}]"
        )
        if fraction:
            lead += (
                f", equal to {_fmt(fraction['value'], fraction['unit'])} of the "
                f"area that could be observed [{fraction['id']}]"
            )
        sentences.append(lead + ".")

    gained = _find(evidence, "area_gained")
    lost = _find(evidence, "area_lost")
    net = _find(evidence, "net_change")
    if net:
        sentences.append(
            f"Compared with the earlier period the net change is "
            f"{_fmt(net['value'], net['unit'])} [{net['id']}]."
        )
    if gained and lost:
        sentences.append(
            f"{_fmt(gained['value'], gained['unit'])} was gained [{gained['id']}] "
            f"and {_fmt(lost['value'], lost['unit'])} lost [{lost['id']}]."
        )

    baseline = _find(evidence, "baseline_water_extent")
    net_new = _find(evidence, "net_new_water")
    if baseline and net_new:
        sentences.append(
            f"Against a baseline of {_fmt(baseline['value'], baseline['unit'])} "
            f"[{baseline['id']}], that is {_fmt(net_new['value'], net_new['unit'])} "
            f"of newly inundated land [{net_new['id']}]."
        )

    zone_count = _find(evidence, "zone_count")
    largest_zone = _find(evidence, "largest_zone_area")
    if zone_count:
        # "The water falls into..." was wrong for vegetation, built-up and the
        # rest. Keep the subject generic.
        text = (
            f"The detected area falls into "
            f"{_fmt(zone_count['value'], zone_count['unit'])} "
            f"distinct {'zone' if zone_count['value'] == 1 else 'zones'} [{zone_count['id']}]"
        )
        if largest_zone:
            text += (
                f"; the largest covers "
                f"{_fmt(largest_zone['value'], largest_zone['unit'])} "
                f"[{largest_zone['id']}]"
            )
            if zones:
                lat = zones[0]["centroid"][1]
                lon = zones[0]["centroid"][0]
                text += f", centred near {lat:.2f} N {lon:.2f} E"
        sentences.append(text + ".")

        severe = [z for z in zones if z.get("severity") == "high"]
        if len(severe) > 1:
            # No bare "50" here: the severity cut-off is a configuration value
            # that never reaches the evidence record, so stating it produces a
            # number the verifier cannot trace.
            sentences.append(
                f"{len(severe)} of these are classed high severity and should be "
                "treated as priority areas."
            )

    people = [item for item in evidence
              if item.get("quantity", "").startswith("population_in_flood_extent_")]
    if people:
        values = sorted(people, key=lambda item: item["value"])
        cites = "".join(f"[{item['id']}]" for item in values)
        if len(values) > 1 and values[0]["value"] != values[-1]["value"]:
            sentences.append(
                f"An estimated {_fmt(values[0]['value'], 'people')} to "
                f"{_fmt(values[-1]['value'], 'people')} people live in the flooded "
                f"area {cites}."
            )
        else:
            sentences.append(
                f"An estimated {_fmt(values[0]['value'], 'people')} people live in "
                f"the flooded area {cites}."
            )

    districts = payload.get("districts") or {}
    worst = [r for r in (districts.get("rows") or []) if r.get("flooded_km2", 0) > 0][:3]
    if worst and districts.get("kind") == "state_breakdown":
        listed = ", ".join(f"{r['name']} ({_fmt(r['flooded_km2'], 'km2')})" for r in worst)
        sentences.append(f"The most affected districts are {listed}.")

    permanent = _find(evidence, "permanent_water_area")
    if permanent:
        sentences.append(
            f"Rivers and ponds present year-round, totalling "
            f"{_fmt(permanent['value'], permanent['unit'])}, were excluded "
            f"[{permanent['id']}]."
        )

    terrain = _find(evidence, "water_excluded_by_terrain")
    if terrain:
        sentences.append(
            f"Dark ground too high above the nearest drainage or too steep to hold "
            f"flood water, totalling {_fmt(terrain['value'], terrain['unit'])}, was "
            f"not counted [{terrain['id']}]."
        )

    # Name what actually produced the number. The classifier uses both sensors,
    # so "Measured using Sentinel-2 optical" was wrong on every classified
    # result - and the provenance block said otherwise two lines later.
    method = (payload.get("method") or {}).get("used")
    sensor = observation.get("sensor_used")

    if method == "classifier":
        sentences.append("Measured using Sentinel-2 optical and Sentinel-1 radar.")
    elif sensor == "sentinel-1":
        sentences.append("Measured using Sentinel-1 radar.")
    elif sensor:
        sentences.append("Measured using Sentinel-2 optical.")

    for note in unobserved.get("notes") or []:
        sentences.append(note)

    return " ".join(sentences)


# --------------------------------------------------------------------- llm

class LLMUnavailable(RuntimeError):
    pass


# qwen and similar emit their chain of thought inline. It must never reach the
# report, both because it is not a report and because the numbers inside it
# would be scored by the verifier.
THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_thinking(text):
    text = THINK_BLOCK.sub("", text)

    # An unterminated block means the model never stopped reasoning, so there is
    # no way to tell where the answer begins. Return nothing and let the caller
    # fall back to the template - emitting chain-of-thought as a flood report
    # would be worse than emitting no LLM report at all.
    if "<think>" in text.lower():
        return ""

    return text.strip()


def _build_prompt(payload):
    """Everything the model is allowed to see. Nothing else goes in."""
    region = payload.get("region") or {}
    period = payload.get("period") or {}
    observation = payload.get("observation") or {}
    unobserved = payload.get("unobserved") or {}

    lines = [
        f"REGION: {region.get('name', 'unknown')}"
        f" ({region.get('admin_level', 'area')}, {region.get('state', '')})".rstrip(", )") + ")",
    ]

    if period.get("post"):
        lines.append(
            f"PERIOD: {period['post'].get('start')} to {period['post'].get('end')}"
        )
    if period.get("pre"):
        lines.append(
            f"BASELINE PERIOD: {period['pre'].get('start')} to {period['pre'].get('end')}"
        )

    lines.append(f"SENSOR: {observation.get('sensor_used', 'unknown')}")

    coverage = observation.get("coverage_fraction")
    if coverage is not None and coverage < 0.98:
        lines.append(
            f"COVERAGE: {coverage * 100:.1f}% of the region was observed."
        )

    zones = payload.get("zones") or []
    if zones:
        lines.append("")
        lines.append("ZONES (largest first, coordinates are lon/lat):")
        for zone in zones[:8]:
            people = zone.get("population") or {}
            living = (f", people living there {people['low']} to {people['high']}"
                      if people.get("low") is not None else "")
            lines.append(
                f"  {zone['id']} rank {zone['rank']}: {zone['area_km2']} km2, "
                f"centred {zone['centroid'][1]:.2f} N {zone['centroid'][0]:.2f} E, "
                f"severity {zone['severity']}{living}"
            )
        if len(zones) > 8:
            lines.append(f"  ... and {len(zones) - 8} smaller zones")

    district_rows = ((payload.get("districts") or {}).get("rows")) or []
    if district_rows:
        kind = (payload.get("districts") or {}).get("kind")
        lines.append("")
        lines.append("DISTRICTS (worst first)" if kind == "state_breakdown"
                     else "DISTRICTS THE DRAWN AREA FALLS IN")
        for row in district_rows[:6]:
            people = row.get("people") or {}
            living = (f", people living in flooded area {people['low']} to {people['high']}"
                      if people.get("low") is not None else "")
            partial = ", PARTLY OBSERVED" if row.get("low_coverage") else ""
            lines.append(
                f"  {row['name']}: {row['flooded_km2']} km2 flooded, "
                f"{row['observed_pct']}% of district observed{living}{partial}"
            )

    lines.append("")
    lines.append("EVIDENCE:")
    for item in payload.get("evidence") or []:
        parts = [
            f"  [{item['id']}] {item['quantity']} = {item['value']} {item.get('unit', '')}".rstrip()
        ]
        if item.get("method"):
            parts.append(f"        method: {item['method']}")
        if item.get("denominator"):
            parts.append(f"        denominator: {item['denominator']}")
        if item.get("note"):
            parts.append(f"        note: {item['note']}")
        lines.extend(parts)

    notes = unobserved.get("notes") or []
    if notes:
        lines.append("")
        lines.append("LIMITATIONS (every one of these MUST appear in your report):")
        lines.extend(f"  - {n}" for n in notes)

    return "\n".join(lines)


def generate_llm_report(payload, model=None, client=None, timeout=20):
    """Generate via Groq. Raises LLMUnavailable rather than returning bad text."""
    model = model or os.environ.get("REPORT_MODEL", "openai/gpt-oss-120b")

    if client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise LLMUnavailable("GROQ_API_KEY is not set.")
        try:
            from groq import Groq
        except ImportError as exc:
            raise LLMUnavailable("groq package not installed.") from exc
        client = Groq(api_key=api_key, timeout=timeout)

    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_prompt(payload)},
        ],
        "temperature": 0.2,
        "max_tokens": 1200,
    }

    # gpt-oss models reason before answering. Without this, Groq may spend the
    # whole token budget on hidden reasoning and return empty content.
    if "gpt-oss" in model:
        request["reasoning_effort"] = "low"

    try:
        response = client.chat.completions.create(**request)
    except Exception as exc:
        # Models disagree on what reasoning_effort accepts: gpt-oss takes
        # "low", qwen only takes "none" or "default". Retry without it.
        if "reasoning_effort" in request and "reasoning_effort" in str(exc):
            request.pop("reasoning_effort")
            try:
                response = client.chat.completions.create(**request)
            except Exception as retry_exc:
                raise LLMUnavailable(f"Groq request failed: {retry_exc}") from retry_exc
        else:
            raise LLMUnavailable(f"Groq request failed: {exc}") from exc

    message = response.choices[0].message
    text = (getattr(message, "content", None) or "").strip()

    # Some reasoning models return the answer in a separate field.
    if not text:
        text = (getattr(message, "reasoning", None) or "").strip()

    text = _strip_thinking(text)

    if not text:
        finish = getattr(response.choices[0], "finish_reason", "unknown")
        raise LLMUnavailable(
            f"Model returned no content (finish_reason={finish}). "
            "If this is a reasoning model, the token budget may have been "
            "consumed by reasoning; try a larger max_tokens or a different model."
        )
    return text, model


# ------------------------------------------------------------------ facade

def collect_extra_values(payload):
    """Numbers that are legitimate to quote but do not live in `evidence`.

    Coverage, scene counts, zone areas and the unobserved fraction are all
    computed by the pipeline and are therefore verifiable - they just are not
    evidence items. Without this the verifier flags them as hallucinations.
    """
    observation = payload.get("observation") or {}
    unobserved = payload.get("unobserved") or {}
    region = payload.get("region") or {}

    extra = {
        "coverage_percent": (observation.get("coverage_fraction") or 0) * 100,
        "coverage_fraction": observation.get("coverage_fraction"),
        "scenes_used": observation.get("scenes_used"),
        "scenes_available": observation.get("scenes_available"),
        "scenes_rejected": observation.get("scenes_rejected_by_cloud_filter"),
        "observable_area_km2": observation.get("observable_area_km2"),
        "region_area_km2": region.get("area_km2"),
        "masked_area_km2": unobserved.get("masked_area_km2"),
    }

    cloud = observation.get("cloud_fraction")
    if cloud is not None:
        extra["cloud_percent"] = cloud * 100

    coverage = observation.get("coverage_fraction")
    if coverage is not None:
        extra["unobserved_percent"] = (1 - coverage) * 100

    masked = unobserved.get("masked_area_km2")
    area = region.get("area_km2")
    if masked is not None and area:
        extra["masked_percent"] = masked / area * 100

    for zone in payload.get("zones") or []:
        zone_id = zone.get("id")
        extra[f"zone_{zone_id}_area"] = zone.get("area_km2")
        extra[f"zone_{zone_id}_rank"] = zone.get("rank")
        # Centroids are quoted in reports ("centred near 30.78 N 75.36 E") and
        # are pipeline-computed, so they are verifiable.
        centroid = zone.get("centroid") or []
        if len(centroid) == 2:
            extra[f"zone_{zone_id}_lon"] = centroid[0]
            extra[f"zone_{zone_id}_lat"] = centroid[1]
        for index, value in enumerate(zone.get("bbox") or []):
            extra[f"zone_{zone_id}_bbox{index}"] = value
        people = zone.get("population") or {}
        for bound in ("low", "high"):
            if people.get(bound) is not None:
                extra[f"zone_{zone_id}_people_{bound}"] = people[bound]

    # District figures are computed by the pipeline, so quoting them is
    # verifiable - the same reasoning as zone areas above.
    for index, row in enumerate(((payload.get("districts") or {}).get("rows")) or []):
        for field in ("flooded_km2", "flooded_pct", "observed_pct", "share_of_area_pct"):
            if row.get(field) is not None:
                extra[f"district{index}_{field}"] = row[field]
        for bound in ("low", "high"):
            if (row.get("people") or {}).get(bound) is not None:
                extra[f"district{index}_people_{bound}"] = row["people"][bound]

    # Numbers inside pipeline-written notes are verifiable by construction: the
    # pipeline computed them. Without this, a report that faithfully repeats a
    # caveat like "Otsu returned -10.99 dB" is penalised for doing the right
    # thing.
    for index, note in enumerate(unobserved.get("notes") or []):
        for match in re.finditer(r"-?\d+\.?\d*", note):
            try:
                extra[f"note{index}_{match.start()}"] = float(match.group(0))
            except ValueError:
                continue

    return {k: v for k, v in extra.items() if v is not None}



def build_report(payload, prefer_llm=True, model=None, client=None):
    """Generate, verify, and fall back if either step fails.

    An LLM report that fails verification is discarded in favour of the
    template. Unverifiable prose is worse than plain prose.
    """
    extra = collect_extra_values(payload)
    evidence = payload.get("evidence") or []

    notes = (payload.get("unobserved") or {}).get("notes") or []

    def assess(text):
        result = verify_report(text, evidence, extra_values=extra)
        result["caveats"] = check_caveats(text, notes)
        # A report is only acceptable if it invents nothing AND omits nothing.
        # Numeric faithfulness alone lets a report mislead by silence.
        result["passed"] = result["passed"] and result["caveats"]["passed"]
        return result

    rejected = None

    if prefer_llm:
        try:
            text, used_model = generate_llm_report(payload, model=model, client=client)
            verification = assess(text)
            if verification["passed"]:
                return {
                    "text": text,
                    "generator_model": used_model,
                    "fallback_used": False,
                    "fallback_reason": None,
                    "rejected_attempt": None,
                }, verification

            # When a report both fabricates and omits, report the fabrication:
            # inventing a casualty figure is a worse failure than dropping a
            # caveat, and the reason should name the worse one.
            fallback_reason = (
                "llm_output_failed_verification"
                if verification["unsupported_claims"] or verification["invalid_citations"]
                else "llm_omitted_caveats"
            )
            # Keep the rejected output. Discarding it silently would throw away
            # exactly the data needed to characterise how models fail, which is
            # the point of measuring this at all.
            rejected = {
                "text": text,
                "model": used_model,
                "faithfulness_rate": verification["faithfulness_rate"],
                "completeness": verification["caveats"]["completeness"],
                "unsupported_claims": verification["unsupported_claims"],
                "invalid_citations": verification["invalid_citations"],
                "omitted_caveats": verification["caveats"]["omitted_caveats"],
            }
        except LLMUnavailable as exc:
            fallback_reason = f"llm_unavailable: {exc}"
    else:
        fallback_reason = "llm_disabled"

    text = render_template_report(payload)
    return {
        "text": text,
        "generator_model": None,
        "fallback_used": True,
        "fallback_reason": fallback_reason,
        "rejected_attempt": rejected,
    }, assess(text)
