"""Cache behaviour and the response contract everyone builds against."""

import json

import pytest

from core import cache


@pytest.fixture(autouse=True)
def temp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("ANTARDRISHTI_NO_CACHE", raising=False)


REQUEST = {"region": "kerala", "post_start": "2018-08-15", "post_end": "2018-08-25"}


def test_miss_then_hit():
    assert cache.get(REQUEST) is None
    cache.put(REQUEST, {"evidence": [], "value": 1})
    assert cache.get(REQUEST) is not None


def test_hit_is_marked_as_such():
    cache.put(REQUEST, {"value": 1})
    assert cache.get(REQUEST)["_cache"]["hit"] is True


def test_key_ignores_dictionary_order():
    cache.put({"a": 1, "b": 2}, {"value": 1})
    assert cache.get({"b": 2, "a": 1}) is not None


def test_a_different_parameter_is_a_different_entry():
    cache.put(REQUEST, {"value": 1})
    assert cache.get({**REQUEST, "scale": 200}) is None


def test_request_id_is_stable():
    """REGRESSION: the geojson route rebuilt a cache key from assumed defaults
    and missed. Callers now fetch by the id they were given."""
    assert cache.key_for(REQUEST) == cache.key_for(dict(reversed(list(REQUEST.items()))))


def test_fetch_by_id_round_trips():
    cache.put(REQUEST, {"value": 42})
    stored = cache.get_by_id(cache.key_for(REQUEST))
    assert stored["value"] == 42


def test_unknown_id_returns_none():
    assert cache.get_by_id("nope") is None


def test_internal_keys_are_never_written():
    """`_internal` holds live Earth Engine objects: not serialisable, and
    meaningless in another process."""
    cache.put(REQUEST, {"value": 1, "_internal": {"mask": object()}})
    assert "_internal" not in cache.get_by_id(cache.key_for(REQUEST))


def test_expired_entries_are_ignored():
    cache.put(REQUEST, {"value": 1})
    assert cache.get(REQUEST, ttl=-1) is None


def test_cache_can_be_disabled(monkeypatch):
    cache.put(REQUEST, {"value": 1})
    monkeypatch.setenv("ANTARDRISHTI_NO_CACHE", "1")
    assert cache.get(REQUEST) is None


def test_corrupt_entry_does_not_raise():
    """A bad cache file must degrade to a miss, never break the request."""
    cache.put(REQUEST, {"value": 1})
    path = cache.CACHE_DIR / f"{cache.key_for(REQUEST)}.json"
    path.write_text("{ not json", encoding="utf-8")
    assert cache.get(REQUEST) is None


def test_clear_removes_everything():
    cache.put(REQUEST, {"value": 1})
    cache.put({**REQUEST, "scale": 200}, {"value": 2})
    assert cache.clear() == 2
    assert cache.stats()["entries"] == 0


# ------------------------------------------------------------ the contract

REQUIRED_TOP_LEVEL = [
    "schema_version", "region", "period", "observation",
    "unobserved", "evidence", "zones", "provenance", "report", "verification",
]


def test_contract_has_every_required_section(contract):
    for key in REQUIRED_TOP_LEVEL:
        assert key in contract, key


def test_every_evidence_item_is_complete(contract):
    for item in contract["evidence"]:
        for key in ("id", "quantity", "value", "unit"):
            assert key in item, f"{item.get('id')} missing {key}"


def test_evidence_ids_are_sequential_and_unique(contract):
    ids = [item["id"] for item in contract["evidence"]]
    assert ids == [f"E{i}" for i in range(1, len(ids) + 1)]


def test_derived_values_reference_earlier_evidence(contract):
    seen = set()
    for item in contract["evidence"]:
        for reference in item.get("derived_from", []):
            assert reference in seen, f"{item['id']} references {reference} too early"
        seen.add(item["id"])


def test_percentages_declare_their_denominator(contract):
    """A percentage of observable area is a different fact from a percentage of
    the region. Every one must say which."""
    for item in contract["evidence"]:
        if item.get("unit") == "percent":
            assert item.get("denominator") or item.get("note"), item["id"]


def test_every_citation_in_the_report_exists(contract):
    import re

    ids = {item["id"] for item in contract["evidence"]}
    cited = set(re.findall(r"\[(E\d+)\]", contract["report"]["text"]))
    assert cited <= ids


def test_zones_are_ranked_largest_first(contract):
    areas = [zone["area_km2"] for zone in contract["zones"]]
    assert areas == sorted(areas, reverse=True)


def test_no_zone_is_larger_than_the_total(contract):
    """REGRESSION: zone area was measured from the polygon outline, which
    encloses the gaps inside it, so a single zone came out larger than the
    total area it belonged to."""
    extent = next(
        item["value"] for item in contract["evidence"]
        if item["quantity"] == "flood_extent"
    )
    for zone in contract["zones"]:
        assert zone["area_km2"] <= extent, zone["id"]


def test_provenance_carries_known_confusions(contract):
    assert contract["provenance"]["known_confusions"]


def test_contract_is_valid_json_on_disk(contract):
    assert json.dumps(contract)


def test_a_result_with_a_failed_part_is_not_served_from_the_cache(tmp_path, monkeypatch):
    """A 2026 flood cached with 'population could not be computed' would have
    kept that error for a month after the bug was fixed."""
    from core import cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.delenv("ANTARDRISHTI_NO_CACHE", raising=False)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    good = {"evidence": [], "population": {"people_in_flood": None}, "districts": {"rows": []}}
    cache.put({"q": "good"}, good)
    assert cache.get({"q": "good"}) is not None
    for part in ("population", "districts"):
        cache.put({"q": part}, {**good, part: {"error": "could not be computed"}})
        assert cache.get({"q": part}) is None, part
