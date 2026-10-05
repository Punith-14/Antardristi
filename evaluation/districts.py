"""
Matching district names as people write them to the names in FAO GAUL 2015.

Lists of drought districts are written in today's spellings (Vijayapura,
Kalaburagi, Telangana); GAUL 2015 predates several renamings and the 2014
split of Andhra Pradesh, so it says Bijapur, Gulbarga and puts Mahbubnagar in
Andhra Pradesh. Some names exist in two states (Aurangabad, Bijapur,
Hamirpur), so the state always takes part in the match.

A district that cannot be matched is reported, never guessed: a silent wrong
match would score a drought against the wrong place.
"""

import difflib
import re

# Today's state name -> the names GAUL 2015 may use for the same ground.
STATE_ALIASES = {
    "telangana": ["andhra pradesh"],
    "odisha": ["orissa"],
    "uttarakhand": ["uttaranchal"],
    "puducherry": ["pondicherry"],
}

# Close-match cutoff, used only within the right state.
CUTOFF = 0.85


def normalise(name):
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _states(state):
    key = (state or "").strip().lower()
    return {normalise(s) for s in [key, *STATE_ALIASES.get(key, [])]}


def resolve(state, name, table, aliases=()):
    """The GAUL (state, district) for one listed district, or None.

    table: iterable of (ADM1_NAME, ADM2_NAME) pairs from GAUL.
    aliases: older or alternative spellings to try, in order.
    """
    states = _states(state)
    candidates = [(s, d) for s, d in table if normalise(s) in states]
    by_name = {}
    for s, d in candidates:
        by_name.setdefault(normalise(d), (s, d))
    for wanted in (name, *aliases):
        hit = by_name.get(normalise(wanted))
        if hit:
            return hit
    for wanted in (name, *aliases):
        close = difflib.get_close_matches(normalise(wanted), list(by_name), n=1, cutoff=CUTOFF)
        if close:
            return by_name[close[0]]
    return None


def resolve_all(entries, table):
    """Resolve a list of {state, district, aliases?} dicts.

    Returns (matched, missing): matched maps the listed (state, district) to
    GAUL's pair; missing lists what could not be matched.
    """
    table = list(table)
    matched, missing = {}, []
    for entry in entries:
        key = (entry["state"], entry["district"])
        hit = resolve(entry["state"], entry["district"], table, entry.get("aliases", ()))
        if hit:
            matched[key] = hit
        else:
            missing.append(key)
    return matched, missing


def suggestions(state, name, table, n=3):
    """Nearest GAUL names in the state, to print beside a miss."""
    states = _states(state)
    names = sorted({d for s, d in table if normalise(s) in states})
    return difflib.get_close_matches(name, names, n=n, cutoff=0.5)
