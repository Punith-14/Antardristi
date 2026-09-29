"""
Deterministic verification of generated report text against the evidence array.

No model is involved. A language model must never be used to check another
language model's output here - the whole point is that the check is mechanical
and reproducible.

Method:
  1. Pull every number out of the report text.
  2. Pull every [E?] citation out of the report text.
  3. A number is SUPPORTED if some evidence value matches it within tolerance.
  4. A citation is VALID if that evidence id exists.

faithfulness_rate = supported numeric claims / total numeric claims
"""

import re

# Numbers, with optional sign, thousands separators and decimals.
# The sign matters: SAR thresholds are negative dB values, and dropping the
# minus made "-10.99 dB" fail to match an evidence value of -10.99.
NUMBER_PATTERN = re.compile(
    r"(?<![\w.])(-?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w])"
)
CITATION_PATTERN = re.compile(r"\[(E\d+)\]")

# Digits that are part of a name, not a measurement: "Sentinel-1", "S2",
# "GSW1_4", "Landsat-8", "COPERNICUS/S1_GRD". Blanked before extraction so they
# are never treated as claims. Without this, "Sentinel-1 radar" reports a
# spurious unsupported claim of "1".
IDENTIFIER_PATTERN = re.compile(
    r"""
    \[E\d+\]                  # evidence citations
    | \d{4}-\d{2}-\d{2}       # ISO dates
    | [A-Za-z]+[-_/]?\d+\w*   # Sentinel-1, S2, GSW1_4, MOD44W
    | \d+[-_/][A-Za-z]\w*     # 2A-something
    """,
    re.VERBOSE,
)

# Years and small ordinals appear in prose legitimately ("the 2024 monsoon",
# "7 zones" is a real claim but "1.7%" is too). We do not skip small numbers,
# only things that are unambiguously dates.
YEAR_PATTERN = re.compile(r"^(19|20)\d{2}$")

DEFAULT_REL_TOLERANCE = 0.005   # 0.5%, covers rounding in prose
DEFAULT_ABS_TOLERANCE = 0.05


# Language models produce typographically correct text: en-dashes for minus
# signs, non-breaking hyphens inside compounds, narrow no-break spaces in
# numbers. A naive ASCII regex reads "-10.99 dB" as positive 10.99 and reports
# a correct statement as a hallucination. Normalise before parsing, never after.
UNICODE_EQUIVALENTS = {
    "‐": "-",   # hyphen
    "‑": "-",   # non-breaking hyphen
    "‒": "-",   # figure dash
    "–": "-",   # en dash
    "—": "-",   # em dash
    "―": "-",   # horizontal bar
    "−": "-",   # minus sign
    " ": " ",   # no-break space
    " ": " ",   # narrow no-break space
    " ": " ",   # thin space
}

_UNICODE_TABLE = str.maketrans(UNICODE_EQUIVALENTS)


def normalise(text):
    """ASCII-equivalent punctuation, preserving character count."""
    return (text or "").translate(_UNICODE_TABLE)


def _mask_identifiers(text):
    """Replace sensor/dataset names with spaces, preserving character offsets."""
    return IDENTIFIER_PATTERN.sub(lambda m: " " * len(m.group(0)), text)


def _parse_numbers(text):
    """Yield (raw_string, float_value, position) for each number in text."""
    text = _mask_identifiers(normalise(text))
    for match in NUMBER_PATTERN.finditer(text):
        sign = match.group(1)
        whole = match.group(2).replace(",", "")
        frac = match.group(3)
        raw = match.group(0)

        if not sign and YEAR_PATTERN.match(whole) and frac is None:
            continue

        value = float(f"{whole}.{frac}") if frac else float(whole)
        if sign:
            value = -value
        yield raw, value, match.start()


def _matches(claim_value, evidence_value, rel_tol, abs_tol):
    if evidence_value is None:
        return False
    if abs(claim_value - evidence_value) <= abs_tol:
        return True
    if evidence_value == 0:
        return False
    return abs(claim_value - evidence_value) / abs(evidence_value) <= rel_tol


def verify_report(
    text,
    evidence,
    extra_values=None,
    rel_tolerance=DEFAULT_REL_TOLERANCE,
    abs_tolerance=DEFAULT_ABS_TOLERANCE,
):
    """Check every number in `text` against `evidence`.

    Args:
        text: the generated report.
        evidence: list of evidence dicts (from EvidenceBuilder.to_list()).
        extra_values: additional legitimate numbers not in the evidence array,
            e.g. observation counts or coverage percentage. Pass as
            {"label": value}.

    Returns a dict matching the `verification` block of the contract.
    """
    evidence = evidence or []
    lookup = []
    for item in evidence:
        lookup.append((item.get("id"), item.get("quantity"), item.get("value")))
        # Thresholds are legitimately quotable too.
        if item.get("threshold") is not None:
            lookup.append((item.get("id"), "threshold", item.get("threshold")))
        if item.get("confidence") is not None:
            lookup.append((item.get("id"), "confidence", item.get("confidence")))

    for label, value in (extra_values or {}).items():
        lookup.append((None, label, value))

    valid_ids = {item.get("id") for item in evidence}

    supported = []
    unsupported = []

    for raw, value, position in _parse_numbers(text):
        hit = None
        for ev_id, quantity, ev_value in lookup:
            if _matches(value, ev_value, rel_tolerance, abs_tolerance):
                hit = {"evidence_id": ev_id, "quantity": quantity}
                break

        claim = {"claim": raw, "value": value, "position": position}
        if hit:
            claim.update(hit)
            supported.append(claim)
        else:
            unsupported.append(claim)

    cited = CITATION_PATTERN.findall(text)
    invalid_citations = sorted({c for c in cited if c not in valid_ids})
    uncited_evidence = sorted(valid_ids - set(cited))

    total = len(supported) + len(unsupported)
    rate = (len(supported) / total) if total else 1.0

    return {
        "faithfulness_rate": round(rate, 4),
        "claims_total": total,
        "claims_supported": len(supported),
        "claims_unsupported": len(unsupported),
        "unsupported_claims": unsupported,
        "supported_claims": supported,
        "citations_found": sorted(set(cited)),
        "invalid_citations": invalid_citations,
        "uncited_evidence": uncited_evidence,
        "passed": not unsupported and not invalid_citations,
        "checked_by": "deterministic_numeric_matcher_v1",
    }


STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "to", "for", "was",
    "were", "is", "are", "be", "been", "this", "that", "it", "its", "so",
    "which", "usually", "means", "may", "not", "have", "has", "with", "from",
    "at", "by", "as", "than", "then", "there", "their", "very", "more", "less",
}


def _distinctive_terms(note, limit=6):
    """Content words and numbers that ought to survive into any faithful retelling."""
    tokens = re.findall(r"[A-Za-z]{4,}|\d+\.?\d*", note.lower())
    seen, terms = set(), []
    for token in tokens:
        if token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms[:limit]


def check_caveats(text, notes, min_overlap=0.34):
    """Did the report carry across the caveats the pipeline attached?

    A report can be perfectly faithful numerically and still mislead by
    omission. Our Kerala 2018 run scored 100% faithfulness while silently
    dropping the note that the detection threshold had been overruled and the
    extent was therefore less reliable. Fabrication and omission are distinct
    failures and both need measuring.
    """
    notes = [n for n in (notes or []) if n and n.strip()]
    if not notes:
        return {
            "caveats_required": 0,
            "caveats_mentioned": 0,
            "completeness": 1.0,
            "omitted_caveats": [],
            "passed": True,
        }

    lowered = normalise(text).lower()
    omitted = []
    mentioned = 0

    for note in notes:
        terms = _distinctive_terms(note)
        if not terms:
            mentioned += 1
            continue
        hits = sum(1 for term in terms if term in lowered)
        if hits / len(terms) >= min_overlap:
            mentioned += 1
        else:
            omitted.append({"note": note, "matched_terms": hits, "total_terms": len(terms)})

    return {
        "caveats_required": len(notes),
        "caveats_mentioned": mentioned,
        "completeness": round(mentioned / len(notes), 4),
        "omitted_caveats": omitted,
        "passed": not omitted,
    }


def summarise(result):
    """One-line human summary, useful in logs and the UI."""
    if result["passed"]:
        return (
            f"All {result['claims_total']} numeric claims traced to evidence."
        )
    parts = []
    if result["claims_unsupported"]:
        values = ", ".join(c["claim"] for c in result["unsupported_claims"])
        parts.append(f"{result['claims_unsupported']} unsupported ({values})")
    if result["invalid_citations"]:
        parts.append(f"invalid citations: {', '.join(result['invalid_citations'])}")
    return "; ".join(parts)
