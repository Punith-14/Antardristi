"""
Region resolution against FAO GAUL 2015.

The dangerous failure here is never "not found" - it is resolving to the WRONG
place and saying nothing. A question about one district coming back with
another district's statistics passes every check downstream: the numbers match
the evidence record, the citations are valid, verification scores full marks.
Three ways that used to happen, all fixed and all tested below.

Earth Engine is not available here, so the index is stubbed. What is under test
is the matching logic, which is where all three bugs lived.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from geo import regions  # noqa: E402


# A cut-down GAUL index carrying the awkward cases: a name that is both a state
# and part of district names, two districts sharing a name across states, a
# state whose real name contains "and", and places filed under retired names.
FAKE_INDEX = {
    "kerala": [{"name": "Kerala", "state": "Kerala", "level": "state",
                "dataset": "FAO/GAUL/2015/level1", "field": "ADM1_NAME"}],
    "orissa": [{"name": "Orissa", "state": "Orissa", "level": "state",
                "dataset": "FAO/GAUL/2015/level1", "field": "ADM1_NAME"}],
    "pondicherry": [{"name": "Pondicherry", "state": "Pondicherry", "level": "state",
                     "dataset": "FAO/GAUL/2015/level1", "field": "ADM1_NAME"}],
    "jammu and kashmir": [{"name": "Jammu and Kashmir", "state": "Jammu and Kashmir",
                           "level": "state", "dataset": "FAO/GAUL/2015/level1",
                           "field": "ADM1_NAME"}],
    "goa": [{"name": "Goa", "state": "Goa", "level": "state",
             "dataset": "FAO/GAUL/2015/level1", "field": "ADM1_NAME"}],
    "north goa": [{"name": "North Goa", "state": "Goa", "level": "district",
                   "dataset": "FAO/GAUL/2015/level2", "field": "ADM2_NAME"}],
    "south goa": [{"name": "South Goa", "state": "Goa", "level": "district",
                   "dataset": "FAO/GAUL/2015/level2", "field": "ADM2_NAME"}],
    "bangalore urban": [{"name": "Bangalore Urban", "state": "Karnataka",
                         "level": "district", "dataset": "FAO/GAUL/2015/level2",
                         "field": "ADM2_NAME"}],
    "bangalore rural": [{"name": "Bangalore Rural", "state": "Karnataka",
                         "level": "district", "dataset": "FAO/GAUL/2015/level2",
                         "field": "ADM2_NAME"}],
    # The same district name in two different states, 1,000 km apart.
    "aurangabad": [
        {"name": "Aurangabad", "state": "Maharashtra", "level": "district",
         "dataset": "FAO/GAUL/2015/level2", "field": "ADM2_NAME"},
        {"name": "Aurangabad", "state": "Bihar", "level": "district",
         "dataset": "FAO/GAUL/2015/level2", "field": "ADM2_NAME"},
    ],
}


@pytest.fixture(autouse=True)
def stub_index(monkeypatch):
    monkeypatch.setattr(regions, "gaul_name_index", lambda: FAKE_INDEX)


# --------------------------------------------------------------- the aliases

@pytest.mark.parametrize("current,gaul", [
    ("Odisha", "Orissa"),
    ("odisha", "Orissa"),
    ("Puducherry", "Pondicherry"),
])
def test_places_renamed_before_2015_still_resolve(current, gaul):
    """GAUL 2015 carries names that were already obsolete when it was made.
    Odisha was renamed in 2011 and Puducherry in 2006, so a query naming an
    entire state returned nothing at all."""
    assert regions.lookup(current)["name"] == gaul


def test_the_alias_translates_the_query_not_the_dataset():
    """The response reports the boundary actually measured. We do not relabel
    GAUL's polygon as "Odisha" - we record that GAUL calls it Orissa."""
    assert regions.canonical_name("Odisha") == "orissa"
    assert regions.lookup("Odisha")["name"] == "Orissa"


def test_a_name_gaul_already_agrees_with_is_untouched():
    assert regions.lookup("Kerala")["name"] == "Kerala"
    assert regions.canonical_name("Kerala") == "kerala"


# ------------------------------------------------------------- the ambiguity

def test_a_district_name_shared_across_states_is_refused():
    """The bug worth the most.

    India reuses district names: Aurangabad is in Maharashtra AND Bihar. The
    old resolver filtered on the name and called collection.geometry(), which
    returns the UNION - statistics over two districts a thousand kilometres
    apart, reported under one name, with the state taken from whichever
    feature happened to come first.
    """
    with pytest.raises(regions.RegionAmbiguous) as caught:
        regions.lookup("Aurangabad")

    message = str(caught.value)
    assert "Maharashtra" in message
    assert "Bihar" in message


def test_the_ambiguity_error_lists_what_it_was_torn_between():
    """A refusal the user cannot act on is barely better than a wrong answer."""
    with pytest.raises(regions.RegionAmbiguous) as caught:
        regions.lookup("Aurangabad")

    assert len(caught.value.matches) == 2
    assert "Name one exactly" in str(caught.value)


def test_a_state_beats_districts_that_merely_contain_its_name():
    """Goa is a state, and also a substring of North Goa and South Goa.
    Someone asking for Goa means the state."""
    entry = regions.lookup("Goa")
    assert entry["level"] == "state"
    assert entry["name"] == "Goa"


def test_an_exact_district_name_is_never_ambiguous():
    assert regions.lookup("Bangalore Urban")["name"] == "Bangalore Urban"
    assert regions.lookup("Bangalore Rural")["name"] == "Bangalore Rural"


def test_spacing_does_not_change_the_answer():
    assert regions.lookup("bangalore  urban")["name"] == "Bangalore Urban"
    assert regions.lookup("bangalore-urban")["name"] == "Bangalore Urban"


def test_a_name_containing_and_resolves():
    """`name.title()` turned "Jammu and Kashmir" into "Jammu And Kashmir",
    which GAUL has never heard of. Matching real names removes the guess."""
    assert regions.lookup("Jammu and Kashmir")["name"] == "Jammu and Kashmir"


# --------------------------------------------------------------- the vintage

@pytest.mark.parametrize("district", ["Ladakh", "Mulugu", "Balotra", "Chengalpattu"])
def test_districts_created_after_2015_are_refused_not_guessed(district):
    with pytest.raises(regions.RegionNotFound):
        regions.lookup(district)


def test_the_not_found_message_explains_the_vintage_rather_than_blaming_a_typo():
    """The usual cause is not a misspelling - it is a district that did not
    exist when the boundary set was made. Saying so saves the user retyping."""
    with pytest.raises(regions.RegionNotFound) as caught:
        regions.lookup("Ladakh")

    message = str(caught.value)
    assert "2015" in message
    assert "Ladakh" in message
    assert "state" in message.lower()


def test_an_empty_name_is_refused():
    for value in ("", "   ", None):
        with pytest.raises(regions.RegionNotFound):
            regions.lookup(value)


# ------------------------------------------------- never silently substitute

def test_an_unresolvable_question_does_not_fall_back_to_kerala():
    """resolve_region used to `return "kerala", REGIONS["kerala"]` when nothing
    matched. A question about Ladakh came back with Kerala's flood statistics,
    under Kerala's name, cited and verified."""
    with pytest.raises(regions.RegionNotFound):
        regions.resolve_region("flooding in Ladakh in August 2023")


def test_the_curated_regions_still_resolve_directly():
    """The fix must not break the hand-written entries the demo relies on."""
    slug, config = regions.resolve_region("flooding in Kerala")
    assert slug == "kerala"
    assert config["name"] == "Kerala"


# ------------------------------------------------------------ the claim itself

def test_the_boundary_vintage_is_stated_in_one_place():
    """Both the 404 body and the provenance block read these, so they cannot
    disagree with each other."""
    assert regions.BOUNDARY_VINTAGE == "2015"
    assert "2015" in regions.BOUNDARY_SOURCE


def test_every_alias_points_at_a_different_name():
    """An alias mapping a name to itself is dead weight that reads like a fix."""
    for current, gaul in regions.NAME_ALIASES.items():
        if current == gaul:
            pytest.fail(f"{current!r} aliases to itself - drop it or correct it")


def test_a_partial_name_matching_several_districts_is_refused():
    """"Bangalore" sits inside Bangalore Urban and Bangalore Rural. The old
    resolver returned whichever it reached first."""
    with pytest.raises(regions.RegionAmbiguous) as caught:
        regions.lookup("Bangalore")

    assert len(caught.value.matches) == 2


def test_a_partial_name_matching_one_district_still_works():
    """The refusal must not make the resolver useless - a fragment that picks
    out exactly one place is still an answer."""
    assert regions.lookup("Pondich")["name"] == "Pondicherry"


def test_two_character_fragments_are_not_treated_as_names():
    with pytest.raises(regions.RegionNotFound):
        regions.lookup("go")
