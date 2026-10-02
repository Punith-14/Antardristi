"""
D3: villages and roads in the flood zones, from OpenStreetMap.

A recorded-style Overpass response is matched against two zone outlines:
inside, within the 500 m buffer, outside; road kilometres clipped to the
zone; caveats always attached; failure never costs the result; kept out of
the evidence record.
"""

import io
import json
import math
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

pytest.importorskip("shapely")

from geo import places as P  # noqa: E402


def square(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


Z1 = square(76.30, 9.40, 76.40, 9.50)
Z2 = square(76.60, 9.60, 76.65, 9.65)
KX = 111.32 * math.cos(math.radians(9.45))       # km per degree of longitude here

RESULT = {
    "request_id": "abc123def4567890abcd",
    "zones_summary": {"count": 30, "listed": 2},
    "zones_geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": Z2, "properties": {"id": "Z2", "rank": 2, "kind": "outline"}},
        {"type": "Feature", "geometry": Z1, "properties": {"id": "Z1", "rank": 1, "kind": "outline"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [76.35, 9.45]},
         "properties": {"id": "Z1", "rank": 1, "kind": "marker"}},
    ]},
}


def node(i, lon, lat, place, name, en=None):
    tags = {"place": place, "name": name}
    if en:
        tags["name:en"] = en
    return {"type": "node", "id": i, "lon": lon, "lat": lat, "tags": tags}


def way(i, coords, highway, ref=None, name=None):
    tags = {"highway": highway}
    if ref:
        tags["ref"] = ref
    if name:
        tags["name"] = name
    return {"type": "way", "id": i, "tags": tags,
            "geometry": [{"lon": lon, "lat": lat} for lon, lat in coords]}


OSM = {"elements": [
    node(1, 76.35, 9.45, "village", "കൈനകരി", en="Kainakary"),
    node(2, 76.40 + 0.3 / KX, 9.45, "village", "Edgeville"),          # 300 m outside Z1
    node(3, 76.40 + 2.0 / KX, 9.45, "village", "Faraway"),            # 2 km outside
    node(4, 76.62, 9.62, "hamlet", "Chennamkary"),
    node(5, 76.31, 9.41, "town", "Alappuzha"),
    way(10, [(76.25, 9.45), (76.45, 9.45)], "trunk", ref="NH66"),       # crosses Z1
    way(11, [(77.0, 10.0), (77.1, 10.0)], "secondary", ref="MDR9"),     # nowhere near
    way(12, [(76.62, 9.59), (76.62, 9.66)], "primary", ref="SH11"),     # crosses Z2
]}


def run():
    return P.for_result(RESULT, fetcher=lambda query: (OSM, 1_727_740_800))


def zone(block, zid):
    return next(z for z in block["zones"] if z["zone"] == zid)


def test_villages_inside_and_within_the_buffer_and_not_beyond():
    z1 = zone(run(), "Z1")
    names = [p["name"] for p in z1["places"]]
    assert names == ["Alappuzha", "Kainakary", "Edgeville"], "inside first, towns before villages"
    assert {p["name"]: p["inside"] for p in z1["places"]} == {
        "Alappuzha": True, "Kainakary": True, "Edgeville": False}
    assert "Faraway" not in names


def test_the_english_name_leads_and_the_local_one_is_kept():
    kainakary = next(p for p in zone(run(), "Z1")["places"] if p["name"] == "Kainakary")
    assert kainakary["name_local"] == "കൈനകരി"


def test_road_length_is_clipped_to_the_zone():
    roads = zone(run(), "Z1")["roads"]
    assert [r["ref"] for r in roads] == ["NH66"]
    assert roads[0]["km"] == pytest.approx(0.1 * KX, rel=0.01)          # 10 km of a 22 km road
    assert "national highway" in roads[0]["class_label"]
    z2 = zone(run(), "Z2")["roads"]
    assert z2[0]["ref"] == "SH11" and z2[0]["km"] == pytest.approx(0.05 * 111.32, rel=0.02)


def test_zones_come_in_rank_order_with_totals():
    block = run()
    assert [z["zone"] for z in block["zones"]] == ["Z1", "Z2"]
    assert block["totals"]["places"] == 4
    assert block["totals"]["by_type"] == {"town": 1, "village": 2, "hamlet": 1}
    assert set(block["totals"]["road_km_by_class"]) == {"trunk", "primary"}


def test_every_block_carries_its_caveats_attribution_and_scope():
    block = run()
    text = " ".join(block["caveats"])
    assert "not 'flooded roads'" in text and "incomplete" in text and "500 m" in text
    assert block["attribution"] == "© OpenStreetMap contributors (ODbL)"
    assert block["zones_searched"] == 2 and block["zones_found"] == 30
    assert block["fetched_at"] == "2024-10-01T00:00:00Z"


def test_one_query_covers_every_zone_box():
    query = P.overpass_query(P.zone_outlines(RESULT))
    assert query.count('node["place"') == 2 and query.count('way["highway"') == 2
    # Widened by the 500 m buffer and a margin for longitude: 9.40 -> 9.39326.
    assert "(9.39326,76.29326,9.50674,76.40674)" in query and "out tags geom" in query


def test_failure_is_an_error_block_not_an_exception():
    def down(query):
        raise P.PlacesUnavailable("OpenStreetMap (Overpass) could not be reached: timed out")
    assert "could not be reached" in P.for_result(RESULT, fetcher=down)["error"]
    assert "nothing to look up" in P.for_result({"zones_geojson": {"features": []}})["error"]


def test_fetch_caches_on_disk_and_reuses_it(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "CACHE_DIR", tmp_path)
    calls = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout):
        calls.append(request.get_header("User-agent"))
        return Response(json.dumps(OSM).encode())

    monkeypatch.setattr(P.urllib.request, "urlopen", urlopen)
    first, _ = P.fetch("q", now=1000)
    second, _ = P.fetch("q", now=2000)
    assert first == second == OSM and len(calls) == 1
    assert "Antardrishti" in calls[0], "Overpass asks clients to identify themselves"
    P.fetch("q", now=1000 + P.CACHE_TTL_S + 1)
    assert len(calls) == 2, "a stale entry is refetched"


# ------------------------------------------------------------- endpoints

@pytest.fixture
def app(tmp_path, monkeypatch):
    pytest.importorskip("ee")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import flood_scenario as S
    import main
    from core import cache
    from geo import zones as zone_extraction

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    region = S.install(monkeypatch)
    monkeypatch.setattr(main, "resolve_area_or_fail", lambda payload: (region, dict(S.META)))
    monkeypatch.setattr(zone_extraction, "to_geojson",
                        lambda zones, kind="polygon": RESULT["zones_geojson"])
    calls = []
    monkeypatch.setattr(P, "fetch", lambda q: (calls.append(q) or OSM, 1_727_740_800))
    client = TestClient(main.app)
    body = client.post("/analyze", json={"region": "testland", "post_start": S.POST[0],
                                         "post_end": S.POST[1], "use_llm": False}).json()
    return client, body, calls


def test_the_endpoint_looks_up_once_and_keeps_it_out_of_the_evidence(app):
    client, body, calls = app
    rid = body["request_id"]
    first = client.get(f"/analyze/{rid}/places").json()
    second = client.get(f"/analyze/{rid}/places").json()
    assert first["zones"][0]["zone"] == "Z1" and second == first
    assert len(calls) == 1, "the second call is served from the stored block"
    stored = client.get(f"/analyze/{rid}").json()
    assert not any("place" in e["quantity"] or "road" in e["quantity"] for e in stored["evidence"])
    assert "places" not in stored, "the flood result itself is unchanged"


def test_the_csv_and_pdf_carry_the_names_once_looked_up(app):
    pypdf = pytest.importorskip("pypdf")
    client, body, _ = app
    rid = body["request_id"]
    assert client.get(f"/analyze/{rid}/export/places.csv").status_code == 404
    client.get(f"/analyze/{rid}/places")

    csv_text = client.get(f"/analyze/{rid}/export/places.csv").text
    assert csv_text.startswith("zone,zone_rank,kind,name")
    assert "Kainakary" in csv_text and "NH66" in csv_text
    assert "OpenStreetMap contributors" in csv_text

    pdf = client.get(f"/analyze/{rid}/report.pdf").content
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    assert "Villages and roads in the flood zones" in text
    assert "Kainakary" in text and "NH66" in text
    assert "not 'flooded roads'" in text.replace("\n", " ")


# ------------------------------------------- a busy public Overpass server

def test_a_busy_server_hands_over_to_the_next(tmp_path, monkeypatch):
    """Found live: overpass-api.de answered the Kerala lookup with 504."""
    import urllib.error
    monkeypatch.setattr(P, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(P, "OVERPASS_URLS", ["https://a.example/api", "https://b.example/api"])
    seen = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout):
        seen.append(request.full_url)
        if "a.example" in request.full_url:
            raise urllib.error.HTTPError(request.full_url, 504, "Gateway Timeout", {}, None)
        return Response(json.dumps(OSM).encode())

    monkeypatch.setattr(P.urllib.request, "urlopen", urlopen)
    data, _ = P.fetch("q", now=1)
    assert data == OSM and seen == ["https://a.example/api", "https://b.example/api"]


def test_every_server_failing_names_each(tmp_path, monkeypatch):
    import urllib.error
    monkeypatch.setattr(P, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(P, "OVERPASS_URLS", ["https://a.example/api", "https://b.example/api"])

    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 504, "Gateway Timeout", {}, None)

    monkeypatch.setattr(P.urllib.request, "urlopen", urlopen)
    with pytest.raises(P.PlacesUnavailable, match="a.example: HTTP 504; b.example: HTTP 504"):
        P.fetch("q2", now=1)


def test_queries_are_small_and_a_failed_chunk_is_named_not_hidden(monkeypatch):
    zones = [(f"Z{i}", i, square(76.0 + i * 0.01, 9.4, 76.005 + i * 0.01, 9.405)) for i in range(1, 8)]
    result = {"zones_summary": {"count": 7}, "zones_geojson": {"features": [
        {"geometry": g, "properties": {"id": zid, "rank": rank, "kind": "outline"}}
        for zid, rank, g in zones]}}
    calls = []

    def fetcher(query):
        calls.append(query.count('node["place"'))
        if len(calls) == 2:
            raise P.PlacesUnavailable("OpenStreetMap (Overpass) could not be reached: busy")
        return {"elements": []}, 1_727_740_800

    block = P.for_result(result, fetcher=fetcher)
    assert calls == [5, 2], "7 zones in queries of at most 5"
    assert block["zones_not_answered"] == ["Z6", "Z7"]
    assert block["zones_searched"] == 5
    assert block["caveats"][0].startswith("OpenStreetMap did not answer for zones Z6, Z7")


def test_the_lookup_stops_at_its_time_budget(monkeypatch):
    """Five slow queries must not hold a request open for twenty minutes."""
    zones = [(f"Z{i}", i, square(76.0 + i * 0.01, 9.4, 76.005 + i * 0.01, 9.405)) for i in range(1, 12)]
    result = {"zones_summary": {"count": 11}, "zones_geojson": {"features": [
        {"geometry": g, "properties": {"id": zid, "rank": rank, "kind": "outline"}}
        for zid, rank, g in zones]}}
    now = {"t": 0.0}
    monkeypatch.setattr(P, "clock", lambda: now["t"])

    def slow(query):
        now["t"] += P.LOOKUP_BUDGET_S          # each query eats the whole budget
        return {"elements": []}, 1_727_740_800

    block = P.for_result(result, fetcher=slow)
    assert block["zones_searched"] == 5, "only the first query ran"
    assert block["zones_not_answered"] == [f"Z{i}" for i in range(6, 12)]


def test_a_self_crossing_zone_outline_does_not_crash_the_lookup():
    """Found live: a simplified zone outline crossed itself, shapely raised
    TopologyException on the road intersection, and /places returned a 500."""
    bowtie = {"type": "Polygon", "coordinates": [[[76.30, 9.40], [76.40, 9.50], [76.40, 9.40],
                                                  [76.30, 9.50], [76.30, 9.40]]]}
    result = {"zones_summary": {"count": 1}, "zones_geojson": {"features": [
        {"geometry": bowtie, "properties": {"id": "Z1", "rank": 1, "kind": "outline"}}]}}
    block = P.for_result(result, fetcher=lambda q: (OSM, 1_727_740_800))
    assert "error" not in block, block.get("error")
    road = next(r for r in block["zones"][0]["roads"] if r["ref"] == "NH66")
    assert road["km"] > 0, "the crossing road is still measured, against the repaired outline"
    assert "Kainakary" in [p["name"] for p in block["zones"][0]["places"]]
