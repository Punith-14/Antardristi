"""
The newest Sentinel-1 image Earth Engine holds over an area.

This is the newest image the platform could analyse right now - not the
newest image taken: Earth Engine ingests Sentinel-1 a few hours to about a day
after acquisition. The answer is a real scene, with its ID, so it can be
checked.

One Earth Engine round trip (a single ee.Dictionary(...).getInfo()), and the
answer is kept for CACHE_S per area so a busy page does not spend quota.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

COLLECTION = "COPERNICUS/S1_GRD"
LOOKBACK_DAYS = 45
CACHE_S = 3600

_lock = threading.Lock()
_cache = {}


def _iso_ms(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def platform_label(code):
    """'A'/'C' (the S1_GRD platform_number) -> 'Sentinel-1C'."""
    code = str(code or "").strip().upper()
    return f"Sentinel-1{code}" if len(code) == 1 else (code or "Sentinel-1")


def _query(ee_geometry, now):
    import ee
    start = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    collection = (ee.ImageCollection(COLLECTION)
                  .filterBounds(ee_geometry)
                  .filterDate(start, end)
                  .sort("system:time_start", False))
    first = ee.Image(collection.first())
    return ee.Dictionary({
        "count": collection.size(),
        "id": first.get("system:index"),
        "time": first.get("system:time_start"),
        "platform": first.get("platform_number"),
        "pass": first.get("orbitProperties_pass"),
        "relative_orbit": first.get("relativeOrbitNumber_start"),
        "mode": first.get("instrumentMode"),
    }).getInfo()


def last_image(key, ee_geometry, now=None, query=_query):
    """{id, time, platform, pass, relative_orbit, mode} or {error}; cached per key."""
    now = now or datetime.now(timezone.utc)
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
    try:
        raw = query(ee_geometry, now)
        if not raw or not raw.get("count"):
            answer = {"error": f"No Sentinel-1 image in Earth Engine over this area in the last {LOOKBACK_DAYS} days."}
        else:
            answer = {
                "id": raw.get("id"),
                "time": _iso_ms(raw["time"]),
                "platform": platform_label(raw.get("platform")),
                "pass": (raw.get("pass") or "").title() or None,
                "relative_orbit": raw.get("relative_orbit"),
                "mode": raw.get("mode"),
                "source": "Earth Engine COPERNICUS/S1_GRD",
            }
    except Exception as exc:                     # noqa: BLE001 - reported, never raised to a page
        return {"error": f"Earth Engine could not be asked ({type(exc).__name__})."}
    with _lock:
        _cache[key] = (time.monotonic(), answer)
    return answer


def reset():
    with _lock:
        _cache.clear()
