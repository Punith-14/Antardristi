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


def check_group_b():
    """Scenes used, sensor and method choice, and GIS downloads, live.

    A window no earlier section used, so the result is computed now rather
    than served from a cache entry made before scene lists existed.
    """
    import io
    import math
    import zipfile
    from xml.etree import ElementTree

    print("\n18. scenes used (Kerala, 14-24 August 2018, with a May baseline)")
    body = {"region": "kerala", "post_start": "2018-08-14", "post_end": "2018-08-24",
            "pre_start": "2018-05-01", "pre_end": "2018-05-31", "sensor": "sentinel-1",
            "scale": 200, "use_llm": False}
    started = time.time()
    response = requests.post(f"{BASE}/analyze", json=body, timeout=900)
    if not check("200", response.status_code == 200, f"{time.time() - started:.0f}s"):
        print("   ", response.text[:400])
        return
    payload = response.json()
    rid = payload["request_id"]
    scenes = payload.get("scenes") or {}
    post, baseline = scenes.get("post") or {}, scenes.get("baseline") or {}
    check("post-event scenes listed", (post.get("total") or 0) > 0 and post.get("scenes"),
          f"{post.get('total')} scenes" if not post.get("error") else post["error"])
    check("baseline scenes listed separately", (baseline.get("total") or 0) > 0,
          f"{baseline.get('total')} scenes" if not baseline.get("error") else baseline["error"])
    ids = [s["id"] for s in post.get("scenes") or []]
    check("scene IDs look like Sentinel-1 GRD", ids and all(i.startswith("S1") for i in ids),
          ids[0] if ids else "")
    check("no scene in both windows",
          not set(ids) & {s["id"] for s in baseline.get("scenes") or []})
    check("dates agree with 'images taken'",
          post.get("days") == (payload.get("acquisition") or {}).get("days"),
          f"{post.get('days')}")
    check("one relative orbit", len({s.get("relative_orbit") for s in post.get("scenes") or []}) == 1)
    print(f"   [note] passes {post.get('passes')}; satellites "
          f"{sorted({s.get('platform') for s in post.get('scenes') or []})}")
    check("reproduce snippet present", "ee.Filter.inList" in (post.get("reproduce") or ""))

    print("\n19. sensor and method choice")
    catalogue = requests.get(f"{BASE}/analyses", timeout=60).json()
    methods = {m["key"]: m for m in (catalogue.get("flood") or {}).get("methods") or []}
    check("three methods offered", set(methods) == {"radar_threshold", "optical_threshold",
                                                    "radar_change"}, str(sorted(methods)))
    check("each states its accuracy", all((m.get("validation") or {}).get("iou")
                                          for m in methods.values()))
    response = requests.post(f"{BASE}/analyze", json={
        "region": "kerala", "post_start": "2023-01-01", "post_end": "2023-01-31",
        "sensor": "sentinel-2", "scale": 200, "use_llm": False}, timeout=900)
    if check("optical run 200", response.status_code == 200, str(response.status_code)):
        optical_result = response.json()
        check("optical result used Sentinel-2",
              (optical_result.get("observation") or {}).get("sensor_used") == "sentinel-2")
        optical_scenes = ((optical_result.get("scenes") or {}).get("post") or {})
        check("optical scenes carry cloud cover",
              all("cloud_pct" in s for s in optical_scenes.get("scenes") or [{}]),
              f"{optical_scenes.get('total')} scenes")
    else:
        print("   ", response.text[:300])
    response = requests.post(f"{BASE}/analyze", json={
        "region": "kerala", "latest": True, "sensor": "sentinel-2", "scale": 200,
        "use_llm": False}, timeout=120)
    check("latest + optical refused in words", response.status_code == 400
          and "Latest-pass mode" in detail_text(response), str(response.status_code))

    print("\n20. GIS downloads (the section 18 result)")
    for path, test in (
        ("zones.geojson", lambda r: r.json()["type"] == "FeatureCollection"),
        ("zones.kml", lambda r: ElementTree.fromstring(r.content) is not None),
        ("zones.csv", lambda r: r.text.startswith("rank,id,area_km2")),
        ("districts.csv", lambda r: r.text.startswith("rank,district,state")),
    ):
        response = requests.get(f"{BASE}/analyze/{rid}/export/{path}", timeout=120)
        ok = response.status_code == 200
        try:
            ok = ok and test(response)
        except Exception as exc:          # noqa: BLE001 - reported, not raised
            ok = False
            print("   ", exc)
        check(path, ok, response.headers.get("content-disposition", str(response.status_code)))

    plan = requests.get(f"{BASE}/analyze/{rid}/export/flood-plan", timeout=300).json()
    print(f"   [note] GeoTIFF plan: {plan.get('note')}")
    started = time.time()
    response = requests.get(f"{BASE}/analyze/{rid}/export/flood.zip", timeout=900)
    if not check("flood.zip 200", response.status_code == 200,
                 f"{time.time() - started:.0f}s, {len(response.content) / 1e6:.1f} MB"):
        print("   ", response.text[:400])
        return
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    tif_name = next(n for n in archive.namelist() if n.endswith(".tif"))
    check("sidecar and README included",
          f"{tif_name}.aux.xml" in archive.namelist() and "README.txt" in archive.namelist())

    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        print("   [skip] numpy/Pillow not installed - GeoTIFF not read back")
        return
    image = Image.open(io.BytesIO(archive.read(tif_name)))
    pixels = np.array(image)
    values = set(np.unique(pixels).tolist())
    check("only the four codes", values <= {0, 1, 2, 255}, str(sorted(values)))
    # Earth Engine may snap the grid outward by a pixel or two at each edge.
    check("size matches the plan", abs(pixels.shape[1] - plan["width"]) <= 3
          and abs(pixels.shape[0] - plan["height"]) <= 3,
          f"{pixels.shape[1]}x{pixels.shape[0]} vs {plan['width']}x{plan['height']}")

    # The flooded pixels, as area, against the result's own flood_extent.
    # Pixel size and top edge from the GeoTIFF's own tags where Pillow exposes
    # them - ModelPixelScale + ModelTiepoint, or ModelTransformation - and
    # otherwise from the plan's bounds and the delivered size.
    tags = image.tag_v2
    pixel_scale, tiepoint, transform = tags.get(33550), tags.get(33922), tags.get(34264)
    if pixel_scale and tiepoint:
        scale_x, scale_y, top_lat = pixel_scale[0], pixel_scale[1], tiepoint[4]
        source = "GeoTIFF tags"
    elif transform:
        scale_x, scale_y, top_lat = transform[0], -transform[5], transform[7]
        source = "GeoTIFF transformation tag"
    else:
        west, south, east, north = plan["bounds"]
        scale_x = (east - west) / pixels.shape[1]
        scale_y = (north - south) / pixels.shape[0]
        top_lat = north
        source = "plan bounds (no geo tags readable by Pillow)"
    print(f"   [note] pixel size {scale_x:.6f} x {scale_y:.6f} deg, from {source}")
    rows = np.arange(pixels.shape[0])
    lat = np.radians(top_lat - (rows + 0.5) * scale_y)
    row_km2 = (scale_x * 111.32) * (scale_y * 111.32) * np.cos(lat)
    flooded_km2 = float(((pixels == 1).sum(axis=1) * row_km2).sum())
    extent = next(e for e in payload["evidence"] if e["quantity"] == "flood_extent")["value"]
    share = abs(flooded_km2 - extent) / extent if extent else 0
    check("GeoTIFF flood area matches the evidence record (within 10%)", share <= 0.10,
          f"{flooded_km2:.1f} km2 in the file vs {extent} km2 in E1")


def check_group_c():
    """The terrain check, live - only once notebook 09 has shipped a rule."""
    print("\n21. terrain check (Kerala, 14-24 August 2018)")
    terrain = (requests.get(f"{BASE}/analyses", timeout=60).json().get("flood") or {}).get("terrain")
    if not terrain:
        print("   [note] no terrain rule shipped: run scripts.fetch_terrain and notebook 09. "
              "If its decision says SHIP, fill sar.TERRAIN_RULE and run this again.")
        return
    print(f"   [note] rule: {terrain.get('text')} (IoU {terrain.get('validation', {}).get('iou')})")
    body = {"region": "kerala", "post_start": "2018-08-14", "post_end": "2018-08-24",
            "sensor": "sentinel-1", "scale": 200, "use_llm": False}
    on = requests.post(f"{BASE}/analyze", json=body, timeout=900)
    off = requests.post(f"{BASE}/analyze", json={**body, "terrain_check": False}, timeout=900)
    if not check("both runs 200", on.status_code == 200 and off.status_code == 200,
                 f"{on.status_code} / {off.status_code}"):
        return
    on, off = on.json(), off.json()
    ev_on = {e["quantity"]: e for e in on["evidence"]}
    ev_off = {e["quantity"]: e for e in off["evidence"]}
    check("check applied", (on.get("terrain") or {}).get("applied") is True, str(on.get("terrain")))
    check("switched off when asked", (off.get("terrain") or {}).get("applied") is False)
    excluded = (ev_on.get("water_excluded_by_terrain") or {}).get("value")
    check("excluded area reported", excluded is not None, f"{excluded} km2")
    if excluded is not None:
        difference = ev_off["flood_extent"]["value"] - ev_on["flood_extent"]["value"]
        check("excluded = extent without check - extent with it (within 1 km2)",
              abs(difference - excluded) <= 1.0,
              f"{difference:.1f} vs {excluded} km2")
    check("report still verified", (on.get("verification") or {}).get("passed") is True)


def check_group_d():
    """Uploaded boundary, villages and roads, and the Hindi report - live."""
    import io
    import json as _json

    kerala = {"region": "kerala", "post_start": "2018-08-14", "post_end": "2018-08-24",
              "pre_start": "2018-05-01", "pre_end": "2018-05-31", "sensor": "sentinel-1",
              "scale": 200, "use_llm": False}

    print("\n22. uploaded boundary (a Kuttanad outline as GeoJSON)")
    ring = [[76.30, 9.30], [76.55, 9.30], [76.55, 9.60], [76.30, 9.60], [76.30, 9.30]]
    doc = {"type": "FeatureCollection", "features": [{"type": "Feature",
           "properties": {"name": "Kuttanad test outline"},
           "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
    response = requests.post(f"{BASE}/boundary/parse", timeout=120, files={
        "file": ("kuttanad.geojson", io.BytesIO(_json.dumps(doc).encode()), "application/geo+json")})
    if not check("file parsed", response.status_code == 200, str(response.status_code)):
        print("   ", response.text[:300])
    else:
        boundary = response.json().get("boundary")
        check("one shape, ready to analyse", bool(boundary),
              (boundary or {}).get("source", {}).get("feature", ""))
        body = {k: v for k, v in kerala.items() if k != "region"}
        response = requests.post(f"{BASE}/analyze", json={**body, "boundary": boundary},
                                 timeout=900)
        if check("analysis over the uploaded boundary 200", response.status_code == 200,
                 str(response.status_code)):
            result = response.json()
            region = result.get("region") or {}
            check("labelled as an uploaded boundary", "(uploaded boundary)" in region.get("name", ""),
                  region.get("name", ""))
            check("never called official", "not been checked against any official source"
                  in region.get("note", ""))
            districts = result.get("districts") or {}
            names = [r["name"] for r in districts.get("rows") or []]
            check("districts it falls in", districts.get("kind") == "drawn_area_overlap"
                  and "Alappuzha" in names, ", ".join(names[:5]))
        else:
            print("   ", response.text[:300])
    utm = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
           "geometry": {"type": "Polygon", "coordinates": [[[500000, 1040000], [510000, 1040000],
                                                            [510000, 1050000], [500000, 1040000]]]}}]}
    response = requests.post(f"{BASE}/boundary/parse", timeout=60, files={
        "file": ("utm.geojson", io.BytesIO(_json.dumps(utm).encode()), "application/geo+json")})
    check("a UTM file is refused in words", response.status_code == 422
          and "metres" in detail_text(response), str(response.status_code))

    response = requests.post(f"{BASE}/analyze", json=kerala, timeout=900)
    if not check("section 18 result available", response.status_code == 200):
        return
    rid = response.json()["request_id"]

    print("\n23. villages and roads in the flood zones (OpenStreetMap)")
    started = time.time()
    response = requests.get(f"{BASE}/analyze/{rid}/places", timeout=300)
    try:
        places = response.json()
    except ValueError:
        check("places endpoint answers in JSON", False,
              f"HTTP {response.status_code}: {response.text[:200]} - see the uvicorn window for the traceback")
        places = {"error": "not JSON"}
    if places.get("error"):
        print(f"   [note] unavailable: {places['error']}")
    else:
        totals = places.get("totals") or {}
        check("places found", (totals.get("places") or 0) > 0,
              f"{totals.get('places')} places, {totals.get('by_type')}, {time.time() - started:.0f}s")
        named = [p["name"] for z in places["zones"] for p in z["places"]][:8]
        print(f"   [note] e.g. {', '.join(named)}")
        print(f"   [note] road km by class: {totals.get('road_km_by_class')}")
        check("caveats attached", len(places.get("caveats") or []) >= 4)
        evidence = requests.get(f"{BASE}/analyze/{rid}", timeout=60).json()["evidence"]
        check("kept out of the evidence record",
              not any("place" in e["quantity"] or "road" in e["quantity"] for e in evidence))
        csv_text = requests.get(f"{BASE}/analyze/{rid}/export/places.csv", timeout=60).text
        check("places.csv", csv_text.startswith("zone,zone_rank,kind,name"))

    print("\n24. the finding in Hindi (Groq translation, checked)")
    started = time.time()
    hindi = requests.get(f"{BASE}/analyze/{rid}/report/hi", timeout=300).json()
    if hindi.get("available"):
        check("translation passed every check", True,
              f"{hindi.get('attempts')} attempt(s), {time.time() - started:.0f}s, {hindi.get('model')}")
        print(f"   [note] {hindi['text'][:160]}...")
    else:
        check("translation available", False, hindi.get("reason", "")[:200])
    response = requests.get(f"{BASE}/analyze/{rid}/report.pdf", params={"lang": "hi"}, timeout=300)
    check("PDF with Hindi 200", response.status_code == 200 and response.content[:4] == b"%PDF",
          f"{len(response.content) / 1e3:.0f} kB")
    if response.status_code == 200:
        import tempfile
        from pathlib import Path as _Path
        out = _Path(tempfile.gettempdir()) / "antardrishti_hindi_check.pdf"
        out.write_bytes(response.content)
        print(f"   [note] saved to {out} - open it and check the Hindi letters are joined "
              "correctly (or, if no Devanagari font was found, that it says so)")


def check_boundary_fallback():
    """Telangana - absent from GAUL 2015 - through the GAUL 2025 fallback."""
    print("\n25. a state GAUL 2015 lacks (Telangana, October 2020 floods)")
    started = time.time()
    response = requests.post(f"{BASE}/analyze", json={
        "region": "telangana", "post_start": "2020-10-13", "post_end": "2020-10-21",
        "sensor": "sentinel-1", "scale": 200, "use_llm": False}, timeout=900)
    if not check("200", response.status_code == 200, f"{time.time() - started:.0f}s"):
        print("   ", response.text[:300])
        return
    result = response.json()
    region = result.get("region") or {}
    check("outline from GAUL 2025, and said so",
          region.get("boundary_set") == "2025" and "not in FAO GAUL 2015" in (region.get("note") or ""),
          region.get("boundary_source", ""))
    districts = result.get("districts") or {}
    rows = districts.get("rows") or []
    check("Telangana's districts, from the same set", districts.get("kind") == "state_breakdown"
          and len(rows) >= 20 and "2025" in (districts.get("boundary_source") or ""),
          districts.get("error") or f"{len(rows)} districts, {districts.get('boundary_source')}")
    check("districts add up to the state", (districts.get("sum_check") or {}).get("within_tolerance"),
          str(districts.get("sum_check")))
    check("report verified", (result.get("verification") or {}).get("passed") is True)
    kerala = requests.post(f"{BASE}/analyze", json={
        "region": "kerala", "post_start": "2018-08-14", "post_end": "2018-08-24",
        "sensor": "sentinel-1", "scale": 200, "use_llm": False}, timeout=900).json()
    check("Kerala unchanged: still GAUL 2015",
          (kerala.get("region") or {}).get("boundary_source") == "FAO/GAUL/2015/level1")


if __name__ == "__main__":
    main()
    check_group_a()
    check_group_b()
    check_group_c()
    check_group_d()
    check_boundary_fallback()
