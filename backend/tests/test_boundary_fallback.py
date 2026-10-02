"""
GAUL 2025 as a fallback, only for names GAUL 2015 does not know.

Measured (scripts/compare_boundaries.py): switching wholesale failed its rule,
but GAUL 2015 has no Telangana at all and GAUL 2025 does. So a name that
resolves under 2015 resolves exactly as before, and only a name that fails
there is looked up in 2025 - labelled as such, with its districts from the
same set, never a "Disputed" slice.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

pytest.importorskip("ee")

from geo import boundaries as B  # noqa: E402
from geo import regions as R  # noqa: E402


def entry(name, state, level, key):
    a = B.active(key)
    return {"name": name, "state": state, "level": level, "set": key,
            "dataset": a["state_dataset" if level == "state" else "district_dataset"],
            "field": a["state_field" if level == "state" else "district_field"]}


INDEX = {
    "2015": {
        "kerala": [entry("Kerala", "Kerala", "state", "2015")],
        "aurangabad": [entry("Aurangabad", "Bihar", "district", "2015"),
                       entry("Aurangabad", "Maharashtra", "district", "2015")],
    },
    "2025": {
        "kerala": [entry("Kerala", "Kerala", "state", "2025")],
        "telangana": [entry("Telangana", "Telangana", "state", "2025")],
        "mulugu": [entry("Mulugu", "Telangana", "district", "2025")],
    },
}


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    monkeypatch.delenv("BOUNDARY_SET", raising=False)
    monkeypatch.delenv("BOUNDARY_FALLBACK", raising=False)
    monkeypatch.setattr(R, "gaul_name_index", lambda key=None: INDEX[key or "2015"])


def test_a_name_2015_knows_is_answered_by_2015_exactly_as_before():
    found = R.lookup("Kerala")
    assert found["set"] == "2015" and "fallback_from" not in found


def test_a_name_only_2025_knows_falls_back_and_says_so():
    found = R.lookup("Telangana")
    assert found["set"] == "2025" and found["fallback_from"] == "2015"
    assert found["dataset"] == "FAO/GAUL/2025/level1" and found["field"] == "GAUL1_NAME"
    assert R.lookup("Mulugu")["state"] == "Telangana"


def test_a_name_neither_knows_is_still_not_found_with_the_2015_message():
    with pytest.raises(R.RegionNotFound, match="FAO/GAUL/2015"):
        R.lookup("Ladakh")


def test_ambiguity_in_2015_is_never_settled_by_looking_elsewhere():
    with pytest.raises(R.RegionAmbiguous):
        R.lookup("Aurangabad")


def test_the_fallback_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("BOUNDARY_FALLBACK", "none")
    with pytest.raises(R.RegionNotFound):
        R.lookup("Telangana")


def test_a_broken_fallback_is_a_not_found_not_a_crash(monkeypatch):
    def index(key=None):
        if key == "2025":
            raise RuntimeError("Earth Engine refused")
        return INDEX["2015"]
    monkeypatch.setattr(R, "gaul_name_index", index)
    with pytest.raises(R.RegionNotFound):
        R.lookup("Telangana")


# ---------------------------------------------------- building the 2025 index

class Collection:
    def __init__(self, columns):
        self.columns = columns

    def aggregate_array(self, field):
        return type("I", (), {"getInfo": lambda s, v=self.columns[field]: v})()


def test_disputed_slices_are_left_out_and_the_misspelling_is_found(monkeypatch):
    monkeypatch.undo()                                 # the real index builder, not the stub
    monkeypatch.setattr(R, "_initialize_earth_engine", lambda: None)
    states = ["Telangana", "Arunchal Pradesh", "Disputed (West Bengal, Bihar & Jharkhand)"]
    districts = ["Mulugu", "Tawang", "Disputed area 3"]
    district_states = ["Telangana", "Arunchal Pradesh", "Disputed (Madhya Pradesh & Gujarat)"]

    def india(dataset, key=None):
        if dataset.endswith("level1"):
            return Collection({"GAUL1_NAME": states})
        return Collection({"GAUL2_NAME": districts, "GAUL1_NAME": district_states})

    monkeypatch.setattr(B, "india", india)
    R.gaul_name_index.cache_clear()
    try:
        index = R.gaul_name_index("2025")
    finally:
        R.gaul_name_index.cache_clear()
    assert "telangana" in index and "mulugu" in index
    assert not any(k.startswith("disputed") for k in index), "disputed slices are not places"
    assert index["arunachal pradesh"][0]["name"] == "Arunchal Pradesh", \
        "found by the right spelling, filtered by the dataset's own"
    assert "disputed area 3" not in index


# ----------------------------------------------- geometry and districts follow

class Chain:
    def __init__(self, log):
        self.log = log

    def filter(self, f):
        self.log.append(("filter",) + tuple(f))
        return self

    def geometry(self):
        return "GEOMETRY"


@pytest.fixture
def filters(monkeypatch):
    import ee
    monkeypatch.setattr(ee.Filter, "eq", lambda field, value: (field, value))


def test_the_outline_and_its_label_come_from_the_set_that_answered(monkeypatch, filters):
    from pipeline import analysis
    log = []
    monkeypatch.setattr(analysis, "_initialize", lambda: None)
    monkeypatch.setattr(B, "india", lambda dataset, key=None: (log.append((dataset, key)), Chain(log))[1])
    geometry, meta = analysis.resolve_geometry("Telangana")
    assert log == [("FAO/GAUL/2025/level1", "2025"), ("filter", "GAUL1_NAME", "Telangana")], \
        "India filtered with 2025's own country field, the state with 2025's field"
    assert meta["boundary_set"] == "2025" and meta["boundary_vintage"] == "2025"
    assert "not in FAO GAUL 2015" in meta["note"] and "FAO/GAUL/2025/level1" in meta["note"]

    log.clear()
    geometry, meta = analysis.resolve_geometry("Kerala")
    assert log == [("FAO/GAUL/2015/level1", "2015"), ("filter", "ADM1_NAME", "Kerala")]
    assert meta["boundary_set"] == "2015" and "note" not in meta


def test_a_fallback_states_districts_come_from_the_same_set(monkeypatch, filters):
    from geo import districts as D
    log = []
    monkeypatch.setattr(B, "india", lambda dataset, key=None: (log.append((dataset, key)), Chain(log))[1])
    D.districts_for({"admin_level": "state", "name": "Telangana", "boundary_set": "2025"}, "region")
    assert log == [("FAO/GAUL/2025/level2", "2025"), ("filter", "GAUL1_NAME", "Telangana")]
    log.clear()
    D.districts_for({"admin_level": "state", "name": "Kerala"}, "region")
    assert log == [("FAO/GAUL/2015/level2", None), ("filter", "ADM1_NAME", "Kerala")]


def test_district_people_reads_the_boundary_sets_fields_for_both_models(monkeypatch):
    """Found live (Telangana): the population loop variable overwrote the
    boundary-set argument, and every district table under GAUL 2025 failed
    with KeyError 'ghsl'."""
    from geo import districts as D
    from geo import population as P

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def getInfo(self):
            return {"features": self.rows}

    rows = {"ghsl": [{"properties": {"GAUL2_NAME": "Mulugu", "GAUL1_NAME": "Telangana", "sum": 1234}}],
            "worldpop": [{"properties": {"GAUL2_NAME": "Mulugu", "GAUL1_NAME": "Telangana", "sum": 5678}}]}
    image = lambda name: type("Img", (), {"name": name, "projection": lambda self: None})()
    monkeypatch.setattr(P, "sources_for", lambda year: [("ghsl", "GHSL", 2020, image("ghsl")),
                                                         ("worldpop", "WorldPop", 2020, image("worldpop"))])
    monkeypatch.setattr(P, "flooded_population", lambda img, mask, scale: img)
    monkeypatch.setattr(P, "sum_per_feature", lambda img, features, projection: Rows(rows[img.name]))

    people = D.district_people("mask", "features", "2020-10-13", 200, boundary_set="2025")
    range_ = people[D.district_key("Mulugu", "Telangana")]
    assert (range_["low"], range_["high"]) == (P.round_people(1234), P.round_people(5678))
