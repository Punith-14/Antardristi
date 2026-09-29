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
            "2015" in response.json().get("detail", ""),
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

    with open("api_response.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print("\nSaved api_response.json")
    print("\nReport:\n")
    print(report.get("text", ""))


if __name__ == "__main__":
    main()
