"""
Which administrative boundary set names resolve against - in one place.

Every named region, every district table and every boundary label used to
hard-code FAO GAUL 2015 and its field names (ADM0_NAME, ADM1_NAME,
ADM2_NAME). Earth Engine now marks GAUL 2015 deprecated, superseded by GAUL
2025, which renames the fields (GAUL0_NAME, GAUL1_NAME, GAUL2_NAME) and adds
an ISO3_CODE.

The newer set is not adopted on the strength of its date. Whether it
actually knows India's post-2015 districts - Ladakh, Telangana's 33,
Rajasthan's 2023 reorganisation - and still knows every state is MEASURED by
scripts/compare_boundaries.py against a rule written before the run. Until
that says switch, the default stays 2015; the switch is then one setting:

    BOUNDARY_SET=2025        in backend/.env

MEASURED, AND NOT SWITCHED (evaluation/boundary_comparison.json, October
2026). GAUL 2025 has 38 level-1 units and 701 districts against 34 and 573,
but it resolves only 7 of the 30 post-2015 test districts (five in
Telangana, two in Tamil Nadu - no Ladakh, no Rajasthan 2023, no Andhra
Pradesh 2022); Kerala's outline differs by +2.7%; and it splits four
"Disputed (...)" areas out of state boundaries and spells Arunachal Pradesh
"Arunchal". It does have Telangana as a state, which GAUL 2015 does not.
The rule said stay; the default stays 2015.

Results cached under one boundary set are never served under another: the
set is part of the cache key (main.FloodRequest.cache_key) whenever it is not
the original 2015.
"""

import os

import ee

SETS = {
    "2015": {
        "vintage": "2015",
        "state_dataset": "FAO/GAUL/2015/level1",
        "district_dataset": "FAO/GAUL/2015/level2",
        "country_field": "ADM0_NAME",
        "state_field": "ADM1_NAME",
        "district_field": "ADM2_NAME",
        "country": ("ADM0_NAME", "India"),
    },
    "2025": {
        "vintage": "2025",
        "state_dataset": "FAO/GAUL/2025/level1",
        "district_dataset": "FAO/GAUL/2025/level2",
        "country_field": "GAUL0_NAME",
        "state_field": "GAUL1_NAME",
        "district_field": "GAUL2_NAME",
        # By ISO code, not by name: GAUL 2025 uses UN standard names, and a
        # country filter that silently matched nothing would make every Indian
        # place "not found".
        "country": ("ISO3_CODE", "IND"),
    },
}
DEFAULT = "2015"

# Tried only for names the active set does NOT know - Telangana, and the
# handful of post-2015 districts GAUL 2025 has. Never for a name the active
# set resolves, so no result measured so far can change. "none" turns it off.
FALLBACK = "2025"

# GAUL 2025 cuts four "Disputed (...)" units out of state outlines; a name
# resolving to one would be a slice no user asked for. Refused.
DISPUTED_PREFIX = "disputed"

# GAUL 2025's own misspelling, so the index can still find the place. The
# boundary is filtered by the dataset's exact string, so this only fixes
# lookup, never the geometry.
NAME_FIXES = {"2025": {"arunchal pradesh": "arunachal pradesh"}}


def fallback_key():
    key = os.environ.get("BOUNDARY_FALLBACK", FALLBACK).strip()
    if key.lower() == "none" or key == active_key():
        return None
    if key not in SETS:
        raise ValueError(f"BOUNDARY_FALLBACK={key!r} is not one of {sorted(SETS)} or 'none'.")
    return key


def active_key():
    key = os.environ.get("BOUNDARY_SET", DEFAULT).strip()
    if key not in SETS:
        raise ValueError(f"BOUNDARY_SET={key!r} is not one of {sorted(SETS)}.")
    return key


def active(key=None):
    return SETS[key or active_key()]


def source(key=None):
    """'FAO/GAUL/2015/level2' - what results cite as their boundary source."""
    return active(key)["district_dataset"]


def vintage(key=None):
    return active(key)["vintage"]


def dataset(level, key=None):
    return active(key)["state_dataset" if level == "state" else "district_dataset"]


def name_field(level, key=None):
    return active(key)["state_field" if level == "state" else "district_field"]


def india(dataset_id, key=None):
    """An ee.FeatureCollection of the given boundary dataset, India only."""
    field, value = active(key)["country"]
    return ee.FeatureCollection(dataset_id).filter(ee.Filter.eq(field, value))


def levels(key=None):
    """((dataset, name_field, level), ...) for states then districts."""
    a = active(key)
    return ((a["state_dataset"], a["state_field"], "state"),
            (a["district_dataset"], a["district_field"], "district"))


def cache_tag(key=None):
    """None for the original 2015 set (so no cached result re-keys), else the set."""
    key = key or active_key()
    return None if key == DEFAULT else f"gaul_{key}"
