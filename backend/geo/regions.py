import os
import re
from functools import lru_cache

import ee

from core import earth_engine
from geo import boundaries


# ------------------------------------------------------------ boundary vintage
#
# Every geometry comes from FAO GAUL 2015, which is the only administrative
# boundary set Google publishes in the Earth Engine catalogue. That has two
# consequences, and the second is the one that catches people out.
#
# 1. Districts created after 2015 do not exist here - Ladakh (2019), the
#    Jammu and Kashmir reorganisation (2019), Telangana's expansion from 10
#    districts to 33 (2016 onward), Rajasthan's 2023 reorganisation.
#    measure_boundary_coverage.py reports how many, so the limitation in the
#    report is a number rather than a hedge.
#
# 2. GAUL 2015 also carries names that were already out of date IN 2015.
#    Odisha was renamed from Orissa in 2011 and Puducherry from Pondicherry in
#    2006, but GAUL still uses the old ones. So "flooding in Odisha" - a whole
#    state, not an obscure new district - resolved to nothing at all. That is a
#    far bigger hole than any post-2015 district, and it is fixable here.
#
# Current name on the left, whatever GAUL calls it on the right.
NAME_ALIASES = {
    # States and union territories renamed before 2015, which GAUL missed.
    "odisha": "orissa",
    "puducherry": "pondicherry",
    "uttarakhand": "uttaranchal",
    # Cities renamed long ago and still commonly written both ways.
    "bengaluru": "bangalore",
    "mumbai": "greater bombay",
    "chennai": "madras",
    "kolkata": "calcutta",
    "thiruvananthapuram": "trivandrum",
    "kochi": "cochin",
    "vadodara": "baroda",
    "kanpur": "kanpur nagar",
    "prayagraj": "allahabad",
    "shimla": "simla",
    "belagavi": "belgaum",
    "mysuru": "mysore",
    "hubballi": "dharwad",
    "kalaburagi": "gulbarga",
    "ballari": "bellary",
    "vijayawada": "krishna",
    "tiruchirappalli": "tiruchchirappalli",
    "thoothukudi": "tuticorin",
    # Districts renamed or respelt after GAUL 2015 was compiled.
    "sivasagar": "sibsagar",
    "gurugram": "gurgaon",
    "shivamogga": "shimoga",
    "tumakuru": "tumkur",
    "ayodhya": "faizabad",
}


def split_state(text):
    """'Aurangabad, Bihar' or 'Aurangabad (Bihar)' -> ('Aurangabad', 'Bihar').

    The way to say WHICH of two same-named districts is meant; the suggestion
    list in the web app fills it in. Without a qualifier -> (text, None).
    """
    raw = (text or "").strip()
    paren = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", raw)
    if paren:
        return paren.group(1).strip(), paren.group(2).strip()
    if "," in raw:
        name, state = raw.rsplit(",", 1)
        if name.strip() and state.strip():
            return name.strip(), state.strip()
    return raw, None

# Read at import for the names other modules cite; the boundary set itself
# lives in geo/boundaries.py (BOUNDARY_SET in .env chooses it).
BOUNDARY_SOURCE = boundaries.source()
BOUNDARY_VINTAGE = boundaries.vintage()


class RegionAmbiguous(LookupError):
    """More than one district matched, and picking one would be a guess.

    Raised rather than resolved because the failure is invisible otherwise: a
    user asks about one place, gets the statistics of another, and every check
    downstream passes. "Bangalore" matches both Bangalore Urban and Bangalore
    Rural, and the old substring match silently returned whichever came first.
    """

    def __init__(self, query, matches):
        self.query = query
        self.matches = sorted(matches)
        listed = ", ".join(self.matches[:6])
        more = "" if len(self.matches) <= 6 else f", and {len(self.matches) - 6} more"
        super().__init__(
            f"{query!r} matches {len(self.matches)} regions ({listed}{more}). "
            "Name one exactly."
        )


class RegionNotFound(LookupError):
    """The name is not in GAUL 2015.

    Carries the vintage in the message, because the usual cause is not a typo:
    the district was created after 2015 and this boundary set predates it.
    """

    def __init__(self, query):
        self.query = query
        super().__init__(
            f"{query!r} is not in {boundaries.source()}. Boundaries are the "
            f"{boundaries.vintage()} vintage, and the newer FAO GAUL 2025 was "
            "checked too: most districts created since 2015 - Ladakh, Rajasthan's "
            "2023 reorganisation, Andhra Pradesh's 2022 districts - are in neither. "
            "Draw the area or upload its boundary, or try the parent district or "
            "the state."
        )


REGIONS = {
    "kerala": {
        "name": "Kerala",
        "bbox": [74.5, 8.0, 77.0, 12.5],
        "mode": "disaster",
        "summary": "Flood-prone coastal and riverine landscape in southern India.",
        "geometry_source": {
            "level": "state",
            "name": "Kerala",
        },
    },
    "assam": {
        "name": "Assam",
        "bbox": [89.7, 24.1, 96.1, 27.8],
        "mode": "disaster",
        "summary": "Brahmaputra basin region with recurring monsoon flood exposure.",
        "geometry_source": {
            "level": "state",
            "name": "Assam",
        },
    },
    "punjab": {
        "name": "Punjab",
        "bbox": [73.8, 29.5, 76.9, 32.6],
        "mode": "agriculture",
        "summary": "Agricultural monitoring region suitable for crop and water analysis.",
        "geometry_source": {
            "level": "state",
            "name": "Punjab",
        },
    },
    "bengaluru": {
        "name": "Bengaluru",
        "bbox": [77.3, 12.8, 77.9, 13.3],
        "mode": "urban",
        "summary": "Urban growth and peri-urban expansion analysis area.",
    },
    "surat": {
        "name": "Surat",
        "bbox": [72.6, 21.0, 73.0, 21.4],
        "mode": "urban",
        "summary": "Urban expansion and flood-sensitive city corridor.",
    },
}


def _initialize_earth_engine():
    """Kept as a name the rest of this module already calls."""
    earth_engine.initialize()


def _normalize(text):
    # Collapse runs of whitespace too, or "bangalore  urban" fails to equal
    # "bangalore urban" and falls through to the fuzzy pass.
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def canonical_name(text):
    """The older name GAUL might file a place under, or the name as given."""
    return NAME_ALIASES.get(_normalize(text), _normalize(text))


def candidate_names(text):
    """Names to try, in order: what the user wrote, then the older alias.

    The order matters and I had it backwards to begin with. Treating the alias
    as a REPLACEMENT assumed GAUL always uses the older name, and it does not -
    it says Orissa but it also says Puducherry, Chennai and Kolkata. Rewriting
    those into Pondicherry, Madras and Calcutta turned six working queries into
    failures. The alias is a fallback for when the modern name is absent, not a
    claim about what the dataset contains.
    """
    given = _normalize(text)
    names = [given]

    alias = NAME_ALIASES.get(given)
    if alias and alias != given:
        names.append(alias)

    return names


def _candidate_phrases(question, preferred_region=None):
    candidates = []
    if preferred_region:
        candidates.append(preferred_region)

    normalized_question = _normalize(question)
    candidates.append(normalized_question)

    patterns = [
        r"(?:in|around|near|for|of)\s+([a-z\s]+?)(?:\s+(?:before|after|during|with|from|to|using|via)\b|$)",
        r"show\s+([a-z\s]+?)(?:\s+(?:flood|water|crop|urban|change|analysis)\b|$)",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, normalized_question)
        candidates.extend(matches)

    return [item.strip() for item in candidates if item and item.strip()]


def _extract_points(coordinates):
    if not coordinates:
        return []
    if isinstance(coordinates[0], (int, float)):
        return [coordinates]

    points = []
    for value in coordinates:
        points.extend(_extract_points(value))
    return points


@lru_cache(maxsize=2)
def gaul_name_index(key=None):
    """Every India administrative name GAUL 2015 knows, without the geometry.

    Names only. Pulling boundaries just to check a spelling costs megabytes,
    and the check happens on every query.

    Exists because guessing at capitalisation does not work. The old code
    filtered Earth Engine with `name.title()`, which turns "Jammu and Kashmir"
    into "Jammu And Kashmir" - a string GAUL has never heard of. Matching
    against the real names and then filtering by the exact one removes the
    guess entirely.

    Returns {normalised_name: [entry, ...]}. The list matters: India reuses
    district names across states. Aurangabad is in both Maharashtra and Bihar,
    as are Bilaspur, Hamirpur and Pratapgarh.
    """
    _initialize_earth_engine()

    key = key or boundaries.active_key()
    state_field = boundaries.active(key)["state_field"]
    fixes = boundaries.NAME_FIXES.get(key, {})
    index = {}
    for dataset, field, level in boundaries.levels(key):
        collection = boundaries.india(dataset, key)
        names = collection.aggregate_array(field).getInfo() or []
        states = collection.aggregate_array(state_field).getInfo() or []

        for exact, state in zip(names, states):
            if not exact:
                continue
            # A slice GAUL marks as disputed between states is not a place a
            # user means; leave it out of the index entirely.
            if (_normalize(exact).startswith(boundaries.DISPUTED_PREFIX)
                    or _normalize(state).startswith(boundaries.DISPUTED_PREFIX)):
                continue
            normalized = _normalize(exact)
            index.setdefault(fixes.get(normalized, normalized), []).append(
                {
                    "name": exact,
                    "state": state,
                    "level": level,
                    "dataset": dataset,
                    "field": field,
                    "set": key,
                }
            )

    return index


def place_names(key=None):
    """Every state and district the boundary set knows, for the search box.

    [{"name", "state", "level", "aka"}], states first then districts, A-Z.
    `aka` is the modern spelling when GAUL files the place under an older one
    (Sibsagar -> Sivasagar), so typing either finds it. Names only - no
    geometry - from the same index every lookup already uses.
    """
    index = gaul_name_index(key) if key else gaul_name_index()
    modern = {}
    for new, old in NAME_ALIASES.items():
        modern.setdefault(old, new)
    seen, out = set(), []
    for normalized, entries in index.items():
        for e in entries:
            row = (e["level"], e["name"], e["state"])
            if row in seen:
                continue
            seen.add(row)
            out.append({"name": e["name"], "state": e["state"], "level": e["level"],
                        "aka": modern.get(normalized)})
    order = {"state": 0, "district": 1}
    out.sort(key=lambda r: (order.get(r["level"], 2), r["name"].lower(), (r["state"] or "").lower()))
    return out


def lookup(slug_or_name, key=None, fallback=True):
    """Find a place in the active boundary set; if it is not there, in the
    fallback set (geo/boundaries.py) - and say which one answered.

    The fallback is tried ONLY for a name the active set does not know, so
    every name that resolved before resolves exactly as before. Ambiguity in
    the active set is never resolved by looking elsewhere.
    """
    try:
        return _lookup_in(slug_or_name, key)
    except RegionNotFound as missing:
        other = boundaries.fallback_key() if (fallback and key is None) else None
        if not other:
            raise
        try:
            entry = _lookup_in(slug_or_name, other)
        except RegionNotFound:
            raise missing from None
        except RegionAmbiguous:
            raise
        except Exception:                 # noqa: BLE001 - a broken fallback is not an answer
            raise missing from None
        return {**entry, "fallback_from": boundaries.active_key()}


def _lookup_in(slug_or_name, key=None):
    """Find a place in GAUL 2015, or say precisely why it cannot be found.

    Returns a single entry dict. Raises RegionAmbiguous when the name belongs
    to more than one place, and RegionNotFound when it belongs to none - never
    picks one, and never falls back to somewhere else.
    """
    raw, state_wanted = split_state((slug_or_name or "").replace("-", " "))
    if not raw:
        raise RegionNotFound(raw)
    wanted_states = set(candidate_names(state_wanted)) if state_wanted else None

    # Called without the key when none is given, so the index can still be
    # replaced by a no-argument stand-in in tests.
    index = gaul_name_index(key) if key else gaul_name_index()

    def decide(entries):
        """One match is an answer. Several is a question for the user."""
        if wanted_states is not None:
            # "Aurangabad, Bihar": keep the district in that state. A state
            # GAUL files under its older name (Odisha -> Orissa) still matches.
            entries = [e for e in entries
                       if e["level"] == "district" and _normalize(e["state"]) in wanted_states]
        for level in ("state", "district"):
            at_level = [entry for entry in entries if entry["level"] == level]
            if len(at_level) == 1:
                return at_level[0]
            if len(at_level) > 1:
                raise RegionAmbiguous(
                    raw, [f"{e['name']} ({e['state']})" for e in at_level]
                )
        return None

    # Every exact attempt before any partial one. Trying "bengaluru" exactly,
    # then "bengaluru" partially, then "bangalore" exactly would let a loose
    # match on the modern name beat an exact match on the older one.
    for wanted in candidate_names(raw):
        # States before districts: "Goa" is a state as well as a substring of
        # two district names, and the state is what is meant.
        found = decide(index.get(wanted, []))
        if found:
            return found

    # Then partial, and only when it identifies a single place. "Bangalore" is
    # inside both Bangalore Urban and Bangalore Rural; the old code returned
    # whichever it reached first and said nothing about having chosen.
    for wanted in candidate_names(raw):
        # Fragments under three characters match half of India.
        if len(wanted) < 3:
            continue
        partial = [
            entry
            for key, entries in index.items()
            if wanted in key
            for entry in entries
        ]
        found = decide(partial)
        if found:
            return found

    raise RegionNotFound(raw)


@lru_cache(maxsize=2)
def _india_admin_regions(key=None):
    _initialize_earth_engine()

    regions = []
    for dataset_id, name_field, region_type in boundaries.levels(key):
        features = (
            boundaries.india(dataset_id, key)
            .select([name_field])
            .getInfo()
            .get("features", [])
        )

        for feature in features:
            properties = feature.get("properties", {})
            name = properties.get(name_field)
            if not name:
                continue
            slug = _normalize(name).replace(" ", "-")
            if any(region["slug"] == slug for region in regions):
                continue

            geometry = feature.get("geometry", {})
            points = _extract_points(geometry.get("coordinates", []))
            if not points:
                continue
            xs = [coord[0] for coord in points]
            ys = [coord[1] for coord in points]

            regions.append(
                {
                    "slug": slug,
                    "name": name,
                    "bbox": [min(xs), min(ys), max(xs), max(ys)],
                    "mode": "disaster",
                    "summary": f"India-only {region_type} boundary resolved from Earth Engine.",
                    "geometry_geojson": geometry,
                }
            )

    return regions


def _find_dynamic_region(question, preferred_region=None):
    """Resolve a place name in a question, with geometry attached.

    Matching is delegated to lookup(), so the aliases and the ambiguity refusal
    apply identically here and on the /ask path. Geometry is fetched only once
    a name has been settled - there is no point downloading boundaries for a
    query that was never going to resolve.
    """
    for candidate in _candidate_phrases(question, preferred_region):
        try:
            entry = lookup(candidate)
        except RegionNotFound:
            continue
        # RegionAmbiguous deliberately propagates: the caller must hear that
        # the name was not specific enough rather than receive a guess.

        wanted = _normalize(entry["name"])
        for region in _india_admin_regions():
            if _normalize(region["name"]) == wanted:
                return region["slug"], region

    return None, None


def resolve_region(question, preferred_region=None):
    query = _normalize(question)
    if preferred_region and preferred_region in REGIONS:
        return preferred_region, REGIONS[preferred_region]

    for slug, config in REGIONS.items():
        if slug in query or _normalize(config["name"]) in query:
            return slug, config

    dynamic_slug, dynamic_region = _find_dynamic_region(question, preferred_region)
    if dynamic_region:
        return dynamic_slug, dynamic_region

    # Used to return Kerala here.
    #
    # A question about Ladakh came back with Kerala's flood statistics, under
    # Kerala's name, cited and verified - because an unresolvable region was
    # indistinguishable from a resolved one. Refusing is the only honest answer
    # when we do not know where the user means.
    raise RegionNotFound(preferred_region or question)


def get_region_geometry(region_config):
    geometry_geojson = region_config.get("geometry_geojson")
    if geometry_geojson:
        return ee.Geometry(geometry_geojson)

    geometry_source = region_config.get("geometry_source")
    if geometry_source:
        _initialize_earth_engine()
        level = geometry_source.get("level", "state")
        return (
            boundaries.india(boundaries.dataset(level))
            .filter(ee.Filter.eq(boundaries.name_field(level), geometry_source["name"]))
            .geometry()
        )

    return None
