"""
API smoke test against the response contract.

Start the server first:
    uvicorn main:app --reload

Then:
    python test_api.py

First run hits Earth Engine and takes a few minutes. Second run should be
instant from cache - that difference is the point of the cache.
"""

import json
import time

import requests

BASE = "http://127.0.0.1:8000"

KERALA_2018 = {
    "region": "kerala",
    "post_start": "2018-08-15",
    "post_end": "2018-08-25",
    "pre_start": "2018-02-01",
    "pre_end": "2018-04-30",
    "sensor": "sentinel-1",
    "scale": 200,
}

REQUIRED_TOP_LEVEL = [
    "schema_version", "region", "period", "observation",
    "unobserved", "evidence", "zones", "provenance",
]

REQUIRED_EVIDENCE_FIELDS = ["id", "quantity", "value", "unit"]


def check(label, condition, detail=""):
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' - ' + detail) if detail else ''}")
    return condition


def detail_text(response):
    """The error message, whichever shape the API sent it in.

    Newer handlers send a structured detail - {"error", "message", ...} - so
    the error kind can be read by code. Older ones send a plain string. A
    check that assumes a string searches a dict's KEYS for the phrase and
    fails even when the message is right, which is what happened here.
    """
    try:
        detail = response.json().get("detail", "")
    except ValueError:
        return response.text
    if isinstance(detail, dict):
        return " ".join(str(v) for v in detail.values())
    return str(detail)


def main():
    print("1. health")
    response = requests.get(f"{BASE}/", timeout=10)
    check("server responds", response.status_code == 200)

    print("\n2. unknown region returns 404, not a crash")
    response = requests.post(
        f"{BASE}/analyze",
        json={**KERALA_2018, "region": "not-a-real-place"},
        timeout=60,
    )
    check("404", response.status_code == 404, str(response.status_code))
    if response.status_code == 404:
        check(
            "explains the 2015 boundary limit",
            "2015" in detail_text(response),
            detail_text(response)[:90],
        )

    print("\n3. Kerala August 2018 (first call, hits Earth Engine)")
    started = time.time()
    response = requests.post(f"{BASE}/analyze", json=KERALA_2018, timeout=900)
    elapsed = time.time() - started
    if not check("200", response.status_code == 200, f"{elapsed:.0f}s"):
        print(response.text[:500])
        return

    payload = response.json()

    print("\n4. contract shape")
    for field in REQUIRED_TOP_LEVEL:
        check(f"has '{field}'", field in payload)

    evidence = payload.get("evidence") or []
    check("evidence is non-empty", len(evidence) > 0, f"{len(evidence)} items")
    for item in evidence:
        missing = [f for f in REQUIRED_EVIDENCE_FIELDS if f not in item]
        if missing:
            check(f"evidence {item.get('id')} complete", False, str(missing))
            break
    else:
        check("all evidence items complete", True)

    print("\n5. coverage is measured, not assumed")
    observation = payload.get("observation") or {}
    coverage = observation.get("coverage_fraction")
    check("coverage_fraction present", coverage is not None)
    check(
        "coverage is not a suspicious exactly-1.0",
        coverage != 1.0,
        f"{coverage}",
    )

    print("\n6. report and verification")
    report = payload.get("report") or {}
    verification = payload.get("verification") or {}
    check("report text present", bool(report.get("text")))
    check(
        "faithfulness is 100%",
        verification.get("faithfulness_rate") == 1.0,
        f"{verification.get('faithfulness_rate')}",
    )
    check(
        "completeness is 100%",
        (verification.get("caveats") or {}).get("completeness") == 1.0,
    )

    print("\n7. zones")
    zones = payload.get("zones") or []
    check("zones present", len(zones) > 0, f"{len(zones)} listed")
    if zones:
        check("zones are ranked largest first",
              all(zones[i]["area_km2"] >= zones[i + 1]["area_km2"]
                  for i in range(len(zones) - 1)))
        check("zones have centroids", all(z.get("centroid") for z in zones))

    print("\n8. map tiles")
    artifacts = payload.get("artifacts") or {}
    check("flood tile template", "flood_tiles" in artifacts)

    print("\n9. cache")
    started = time.time()
    response = requests.post(f"{BASE}/analyze", json=KERALA_2018, timeout=60)
    cached_elapsed = time.time() - started
    cached = response.json()
    check("cache hit", (cached.get("_cache") or {}).get("hit") is True)
    check("cached call is fast", cached_elapsed < 5, f"{cached_elapsed:.2f}s")
    print(f"       first {elapsed:.0f}s -> cached {cached_elapsed:.2f}s")

    print("\n10. retrieval by request_id")
    request_id = payload.get("request_id")
    check("request_id returned", bool(request_id), str(request_id))

    if request_id:
        response = requests.get(f"{BASE}/analyze/{request_id}", timeout=30)
        check("stored analysis retrievable", response.status_code == 200)

        response = requests.get(
            f"{BASE}/analyze/{request_id}/zones.geojson", timeout=30
        )
        if check("geojson served", response.status_code == 200, str(response.status_code)):
            collection = response.json()
            features = collection.get("features", [])
            check("geojson has features", len(features) > 0, f"{len(features)}")

            outlines = [f for f in features if f["properties"].get("kind") == "outline"]
            markers = [f for f in features if f["properties"].get("kind") == "marker"]
            check("has polygon outlines for the map", len(outlines) > 0, f"{len(outlines)}")
            check("has centroid markers", len(markers) > 0, f"{len(markers)}")
            check(
                "every feature carries area and colour",
                all(
                    "area_km2" in f["properties"] and "colour" in f["properties"]
                    for f in features
                ),
            )

    print("\n11. map display hints")
    hints = payload.get("map") or {}
    check("map hints present", bool(hints))
    check(
        "flood leads with outlines",
        hints.get("primary_layer") == "zone_polygons",
        str(hints.get("primary_layer")),
    )

    print("\n12. zone areas cannot exceed the total")
    zone_total = sum(z["area_km2"] for z in zones)
    extent = next(
        (e["value"] for e in evidence if e["quantity"] == "flood_extent"), 0
    )
    largest = max((z["area_km2"] for z in zones), default=0)
    check(
        "largest zone <= total extent",
        largest <= extent,
        f"largest {largest:,.1f} vs extent {extent:,.1f}",
    )
    check("listed zones sum <= total extent", zone_total <= extent + 1)

    check_drawn_polygon()

    with open("api_response.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print("\nSaved api_response.json")
    print("\nReport:\n")
    print(report.get("text", ""))


# A rough outline around Kuttanad, the way the map's shape tool would send it:
# an open ring of [longitude, latitude], no repeated closing point.
KUTTANAD_OUTLINE = [
    [76.30, 9.35],
    [76.58, 9.32],
    [76.62, 9.58],
    [76.44, 9.71],
    [76.26, 9.60],
]


def check_drawn_polygon():
    """A drawn outline reaches Earth Engine and comes back honestly labelled.

    The unit tests prove the ring is validated and the endpoint declares the
    field. Neither proves a polygon survives the trip to Earth Engine and back
    - that needs a live reduction, which is what this does.
    """
    print("\n13. a drawn polygon is measured and labelled as user-supplied")

    body = {
        "polygon": KUTTANAD_OUTLINE,
        "post_start": "2018-08-15",
        "post_end": "2018-08-25",
        "sensor": "sentinel-1",
        "scale": 200,
    }
    response = requests.post(f"{BASE}/analyze", json=body, timeout=600)
    if not check("polygon analysis returns 200", response.status_code == 200,
                 str(response.status_code)):
        print("   ", response.text[:400])
        return

    payload = response.json()
    region = payload.get("region") or {}
    shape = region.get("footprint") or {}

    check("the shape came back as a polygon", shape.get("kind") == "polygon",
          str(shape.get("kind")))
    check(
        "the point count is what was drawn, not the closed ring",
        shape.get("points") == len(KUTTANAD_OUTLINE),
        f"{shape.get('points')} vs {len(KUTTANAD_OUTLINE)} drawn",
    )
    # The claim this whole feature exists to keep honest: a drawn area is not
    # a district, and nothing in the response may imply it is.
    check("admin_level is custom", region.get("admin_level") == "custom",
          str(region.get("admin_level")))
    check("no state is claimed", region.get("state") in (None, ""),
          str(region.get("state")))
    check(
        "the note says it is not an administrative boundary",
        "not an administrative boundary" in (region.get("note") or "").lower(),
    )
    check("evidence was still produced", len(payload.get("evidence") or []) > 0,
          f"{len(payload.get('evidence') or [])} records")

    print("\n14. two shapes at once are refused rather than silently picked")
    both = {**body, "bbox": [76.3, 9.3, 76.7, 9.8]}
    response = requests.post(f"{BASE}/analyze", json=both, timeout=60)
    # 422, the status every invalid shape has used since footprint.py was
    # written: the request is well-formed JSON describing an unusable area.
    check("refused with 422", response.status_code == 422, str(response.status_code))
    if response.status_code == 422:
        text = detail_text(response)
        check("names both shapes", "bbox" in text and "polygon" in text, text[:90])

    print("\n15. a self-intersecting outline is refused, not measured")
    # Earth Engine would return a number for a bowtie: the two lobes wind
    # opposite ways and partly cancel, so the figure is the area of nothing
    # anyone drew. Refused on both sides - the browser so the user can fix it,
    # here because a browser is not a validator.
    bowtie = {**body, "polygon": [[76.3, 9.4], [76.6, 9.4], [76.3, 9.7], [76.6, 9.7]]}
    response = requests.post(f"{BASE}/analyze", json=bowtie, timeout=60)
    check("refused with 422", response.status_code == 422, str(response.status_code))
    if response.status_code == 422:
        text = detail_text(response)
        check("says why rather than just rejecting", "crosses itself" in text, text[:90])

def check_group_a():
    """People, districts, image age and latest-pass mode, live on Earth Engine.

    The unit tests replace Earth Engine with numpy grids. These are the
    checks that only a real run can make: that the population datasets load,
    that GAUL districts resolve for a real state, and that a real latest pass
    is found.
    """
    print("\n16. people, districts and image age (Kerala, August 2018, 200 m)")
    body = {**KERALA_2018, "post_start": "2018-08-01", "post_end": "2018-08-31",
            "pre_start": None, "pre_end": None, "scale": 200, "use_llm": False}
    started = time.time()
    response = requests.post(f"{BASE}/analyze", json=body, timeout=900)
    if not check("200", response.status_code == 200, f"{time.time() - started:.0f}s"):
        print("   ", response.text[:400])
        return
    payload = response.json()

    population = payload.get("population") or {}
    if not check("population computed", population and not population.get("error"),
                 population.get("error", "")):
        pass
    people = population.get("people_in_flood") or {}
    check("both models answered", len(people.get("by_source") or {}) == 2,
          str(people.get("by_source")))
    check("range is ordered", (people.get("low") or 0) <= (people.get("high") or 0),
          f"{people.get('low')} to {people.get('high')}")
    # Kerala has about 35 million people. A flood count above that means the
    # counting went wrong - the averaging pitfall would go the other way, so
    # also require it to be non-trivial for a flood of this size.
    check("plausible for Kerala", 1_000 < (people.get("high") or 0) < 35_000_000,
          f"{people.get('high')}")
    check("zones carry people", any(z.get("population") for z in payload.get("zones") or []))

    districts = payload.get("districts") or {}
    rows = districts.get("rows") or []
    check("district breakdown for a state", districts.get("kind") == "state_breakdown",
          str(districts.get("kind") or districts.get("error")))
    check("Kerala's districts listed", 10 <= len(rows) <= 16, f"{len(rows)} districts")
    check("worst first", rows == sorted(rows, key=lambda r: -r["flooded_km2"]))
    sum_check = districts.get("sum_check") or {}
    print(f"   [note] districts total {sum_check.get('districts_total_km2')} km2 vs region "
          f"{sum_check.get('region_total_km2')} km2 "
          f"({'within' if sum_check.get('within_tolerance') else 'OUTSIDE'} tolerance)")

    acquisition = payload.get("acquisition") or {}
    check("image dates present", bool(acquisition.get("last")), str(acquisition.get("last")))
    check("image age worked out", isinstance(acquisition.get("age_days"), int),
          str(acquisition.get("age_text")))

    text = (payload.get("report") or {}).get("text", "")
    check("report mentions people", "people live in the flooded area" in text)
    verification = payload.get("verification") or {}
    check("report still fully verified", verification.get("passed") is True,
          str(verification.get("unsupported_claims")))

    print("\n17. latest-pass mode (Kerala, newest Sentinel-1 image)")
    response = requests.post(f"{BASE}/analyze", json={
        "region": "kerala", "latest": True, "scale": 200, "use_llm": False}, timeout=900)
    if not check("200", response.status_code == 200, str(response.status_code)):
        print("   ", response.text[:400])
        return
    payload = response.json()
    latest = payload.get("latest") or {}
    period = (payload.get("period") or {}).get("post") or {}
    check("found a recent pass", bool(latest.get("date")), str(latest.get("date")))
    check("analysed exactly that day", period.get("start") == latest.get("date"),
          f"{period.get('start')} to {period.get('end')}")
    age = (payload.get("acquisition") or {}).get("age_days")
    check("image is recent", isinstance(age, int) and age <= 30, f"{age} days old")
    print(f"   [note] coverage {payload.get('observation', {}).get('coverage_fraction')}; "
          f"extend offer: {'yes' if latest.get('extend_offer') else 'no'}; "
          f"next pass estimate: {(latest.get('next_pass') or {}).get('expected_on')}")


if __name__ == "__main__":
    main()
    check_group_a()
