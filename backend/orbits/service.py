"""
The satellite status the web app shows, put together from three sources:

    elements   CelesTrak orbital elements      -> live position, next pass (browser)
    plans      ESA acquisition plans (KML)     -> next planned image of an area
    latest     Earth Engine Sentinel-1 scenes  -> last image available to analyse

`summary()` answers for India and is public (the landing page shows it).
`area(name)` answers for one state or district and needs a session, because
outlining a place costs an Earth Engine call.

Only satellites ESA is currently planning are shown; a satellite whose
mission has ended (Sentinel-1A retired on 30 June 2026, 1B in 2022) has no
current plan file, so it drops out without a code change.
"""

import json
import threading
from datetime import datetime, timezone

from core import paths
from orbits import celestrak, latest, plans

SIMPLIFY_M = 2000            # outline detail sent to the browser and used for footprints
INDIA_SIMPLIFY_M = 5000

# Used only when Earth Engine cannot outline India: a coarse hand-drawn
# outline (lon, lat), said to be approximate wherever it is used.
INDIA_APPROX = [
    (68.2, 23.6), (68.9, 22.3), (70.0, 20.8), (72.7, 19.0), (73.4, 16.0), (74.8, 12.8),
    (76.2, 9.5), (77.5, 8.1), (78.2, 8.9), (79.9, 10.3), (80.3, 13.1), (80.1, 15.5),
    (82.3, 16.6), (84.8, 19.3), (86.9, 20.8), (88.9, 21.6), (89.1, 22.9), (88.8, 26.3),
    (89.8, 26.0), (92.4, 24.9), (93.4, 23.2), (94.6, 24.7), (95.3, 26.6), (96.9, 27.3),
    (97.4, 28.3), (96.0, 29.4), (94.0, 29.3), (92.0, 27.9), (88.9, 27.3), (88.1, 26.4),
    (84.1, 27.4), (80.1, 28.8), (81.0, 30.2), (78.9, 31.3), (79.5, 32.6), (80.3, 35.5),
    (77.8, 35.5), (74.6, 37.0), (73.9, 34.5), (74.3, 32.8), (75.4, 32.3), (74.6, 31.0),
    (73.9, 30.0), (72.9, 28.9), (71.4, 27.8), (70.1, 27.9), (69.5, 26.7), (70.4, 25.7),
    (71.1, 24.4), (68.8, 24.3), (68.2, 23.6),
]

RETRY_AFTER_FAILURE_S = 15 * 60

_lock = threading.Lock()
_shapes = {}
_india_failed = {"at": None}


def _now():
    return datetime.now(timezone.utc)


def _shape_file(key):
    return paths.CACHE / "orbits" / "shapes" / f"{key}.json"


def _round(geojson, places=3):
    def walk(coords):
        if isinstance(coords, (int, float)):
            return round(coords, places)
        return [walk(c) for c in coords]
    return {"type": geojson["type"], "coordinates": walk(geojson["coordinates"])}


def _remember(key, record):
    path = _shape_file(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    with _lock:
        _shapes[key] = record
    return record


def _recall(key):
    with _lock:
        if key in _shapes:
            return _shapes[key]
    try:
        record = json.loads(_shape_file(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    with _lock:
        _shapes[key] = record
    return record


def _outline(ee_geometry, metres):
    """An ee.Geometry as small GeoJSON (one getInfo)."""
    return _round(ee_geometry.simplify(maxError=metres).getInfo())


def india_outline():
    """{name, geometry, approximate} - Earth Engine's GAUL outline, cached on
    disk after the first time; a coarse fallback when EE is not reachable."""
    found = _recall("india")
    if found:
        return found
    failed = _india_failed["at"]
    if failed is None or (_now() - failed).total_seconds() >= RETRY_AFTER_FAILURE_S:
        try:
            return _india_from_earth_engine()
        except Exception:                        # noqa: BLE001 - fall back, and say so
            _india_failed["at"] = _now()
    return {"name": "India", "approximate": True, "source": "coarse outline (Earth Engine unreachable)",
            "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in INDIA_APPROX]]}}


def _india_from_earth_engine():
    from core import earth_engine
    from geo import boundaries
    earth_engine.initialize()
    states = boundaries.india(boundaries.dataset("state"))
    geometry = _outline(states.geometry(), INDIA_SIMPLIFY_M)
    return _remember("india", {"name": "India", "geometry": geometry, "approximate": False,
                               "source": boundaries.dataset("state")})


def area_outline(name):
    """{name, state, level, geometry, approximate} for a state or district, or
    None when the name is not found. Raises regions.RegionAmbiguous."""
    from pipeline import analysis
    geometry, meta = analysis.resolve_geometry(name)
    if geometry is None:
        return None
    key = f"{meta.get('boundary_set') or 'gaul'}-{meta['slug']}"
    found = _recall(key)
    if found:
        return found
    return _remember(key, {"name": meta["name"], "state": meta.get("state"),
                           "level": meta.get("admin_level"), "geometry": _outline(geometry, SIMPLIFY_M),
                           "approximate": False, "source": meta.get("boundary_source")})


def _shapely(record):
    from shapely.geometry import shape
    from shapely.validation import make_valid
    geometry = shape(record["geometry"])
    return geometry if geometry.is_valid else make_valid(geometry)


def _bbox(record):
    minx, miny, maxx, maxy = _shapely(record).bounds
    return [round(minx, 3), round(miny, 3), round(maxx, 3), round(maxy, 3)]


def _last_image(key, record):
    try:
        import ee
        from core import earth_engine
        earth_engine.initialize()
        return latest.last_image(key, ee.Geometry(record["geometry"]))
    except Exception as exc:                     # noqa: BLE001
        return {"error": f"Earth Engine could not be asked ({type(exc).__name__})."}


def _active(elements, plan):
    """Satellites ESA is planning now; all Sentinel-1 elements if the plan
    could not be read (better a retired satellite shown than none)."""
    planned = {f["satellite"] for f in plan["files"]}
    sats = elements["satellites"]
    return [s for s in sats if s["key"] in planned] if planned else sats


def _for_area(key, record, plan, now, limit):
    shape = _shapely(record)
    return {
        "area": {**{k: record.get(k) for k in ("name", "state", "level", "approximate", "source")},
                 "geometry": record["geometry"], "bbox": _bbox(record)},
        "next_planned": plans.upcoming(plan["datatakes"], shape, now=now, limit=limit),
        "last_image": _last_image(key, record),
    }


def _sources(elements, plan):
    return {
        "elements": {k: elements.get(k) for k in ("fetched_at", "stale", "error", "source")},
        "plans": {**{k: plan.get(k) for k in ("fetched_at", "stale", "error", "source")},
                  "files": plan["files"]},
    }


def summary(now=None):
    now = now or _now()
    elements = celestrak.elements(now=now)
    plan = plans.plan(now=now)
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "satellites": _active(elements, plan),
        "india": _for_area("india", india_outline(), plan, now, limit=6),
        "sources": _sources(elements, plan),
        "note": "Planned images come from ESA's published plan and can still change. "
                "The last image is the newest one already in Earth Engine, which lags "
                "acquisition by a few hours to about a day.",
    }


def area(name, now=None):
    """The same for one place, or None when the name is not found."""
    now = now or _now()
    record = area_outline(name)
    if record is None:
        return None
    plan = plans.plan(now=now)
    key = f"area-{record['name'].lower()}-{record.get('state') or ''}"
    return {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            **_for_area(key, record, plan, now, limit=8),
            "sources": {"plans": {k: plan.get(k) for k in ("fetched_at", "stale", "error", "source")}}}


def warm():
    """Fill the caches in the background at start-up, so the first visitor
    does not wait for three outside servers."""
    try:
        summary()
    except Exception:                            # noqa: BLE001 - warming is best effort
        pass
