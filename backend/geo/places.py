"""
Villages and roads in the flood zones, from OpenStreetMap.

A district official acts on names, not coordinates: "Zone 3 takes in
Kainakary and Champakulam, and 4 km of NH 66" is something a team can be
sent to. This looks those names up for each listed zone.

What it is, and what it is not - stated in every result:

    places   OpenStreetMap place points (town, village, hamlet) inside a zone
             outline or within BUFFER_M of it. A village is a point here, not
             a boundary; one whose point lies just outside can still be hit.
             OpenStreetMap is incomplete in much of rural India, so a missing
             village is not a dry village.
    roads    kilometres of main road (trunk, primary, secondary - in India
             usually national highways, state highways and major district
             roads) that CROSS a zone outline. Roads are often raised above
             the floodplain and a 200 m radar pixel cannot tell, so this is
             "roads crossing flood zones", never "flooded roads".
    scope    only the listed zones are searched; scattered flood pixels
             outside them are not.

Kept OUT of the evidence record. OpenStreetMap is a gazetteer, not a
measurement, and nothing here has an accuracy figure; the report is not
allowed to cite it as one. It lives in its own `places` block.

One Overpass request per result (every zone's box in one query), cached on
disk. Any failure - Overpass down, slow, rate-limited - returns {"error": ...}
and never costs the flood result.
"""

import hashlib
import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from core import paths

# Public Overpass servers, tried in order. The main one answered the first
# live run (25 Kerala zones, one query) with "504 Gateway Timeout": a busy
# public server drops heavy queries. So queries are now small - a few zones
# each - and a server that is busy or timing out hands over to the next.
# OVERPASS_URL (one) or OVERPASS_URLS (comma-separated) override the list.
OVERPASS_URLS = [u.strip() for u in (
    os.environ.get("OVERPASS_URLS") or os.environ.get("OVERPASS_URL") or
    "https://overpass-api.de/api/interpreter,"
    "https://overpass.kumi.systems/api/interpreter,"
    "https://overpass.private.coffee/api/interpreter"
).split(",") if u.strip()]
OVERPASS_URL = OVERPASS_URLS[0]
ZONES_PER_QUERY = 5
RETRY_STATUS = (429, 502, 503, 504)
# Per server attempt, and for the whole lookup. Five queries x three servers
# x 90 s could keep a request open for over twenty minutes when Overpass is
# struggling - long past any client's patience. So each attempt gets 40 s,
# and once LOOKUP_BUDGET_S has passed the remaining zones are reported as not
# answered instead of being tried.
TIMEOUT_S = 40
LOOKUP_BUDGET_S = 150
BUFFER_M = 500
CACHE_DIR = paths.CACHE / "osm"
CACHE_TTL_S = 30 * 24 * 3600
USER_AGENT = "Antardrishti/0.5 (academic flood-mapping project)"

PLACE_TYPES = ("city", "town", "village", "hamlet")
PLACE_ORDER = {t: i for i, t in enumerate(PLACE_TYPES)}

ROAD_CLASSES = {
    "motorway": "expressway",
    "trunk": "trunk (usually a national highway)",
    "primary": "primary (usually a state highway)",
    "secondary": "secondary (usually a major district road)",
}

ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"

CAVEATS = [
    "Roads are listed where they cross a flood zone; raised roads may be passable, "
    "and a 200 m radar pixel cannot tell - these are not 'flooded roads'.",
    f"Villages are OpenStreetMap points inside a zone or within {BUFFER_M} m of it; "
    "a village whose point lies outside can still be affected.",
    "OpenStreetMap is incomplete in much of rural India: a village missing from this "
    "list is not evidence that it is dry.",
    "Only the listed zones are searched; scattered flood pixels outside them are not.",
]

KM_PER_DEG = 111.32


class PlacesUnavailable(RuntimeError):
    pass


# ------------------------------------------------------------- the query

def zone_outlines(result):
    """[(zone_id, rank, GeoJSON geometry)] for the listed zones with outlines."""
    out = []
    for feature in (result.get("zones_geojson") or {}).get("features") or []:
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        if props.get("kind") == "outline" and geometry.get("type") in ("Polygon", "MultiPolygon"):
            out.append((props.get("id"), props.get("rank"), geometry))
    out.sort(key=lambda z: z[1] or 10**6)
    return out


def _bbox(geometry, margin_deg):
    rings = geometry["coordinates"] if geometry["type"] == "Polygon" else [
        r for poly in geometry["coordinates"] for r in poly]
    lons = [p[0] for r in rings for p in r]
    lats = [p[1] for r in rings for p in r]
    return (min(lats) - margin_deg, min(lons) - margin_deg,
            max(lats) + margin_deg, max(lons) + margin_deg)


# A zone whose box is bigger than this is asked about on its own.
BIG_BOX_KM2 = 400
# The search shape sent to Overpass: the zone outline grown by the buffer and
# simplified to at most this many points per part, at most MAX_PARTS parts.
MAX_POINTS = 60
MAX_PARTS = 6


def box_km2(geometry):
    s, w, n, e = _bbox(geometry, 0)
    return (n - s) * KM_PER_DEG * (e - w) * KM_PER_DEG * math.cos(math.radians((n + s) / 2))


def search_shapes(geometry, margin_deg):
    """Overpass `poly:` filters for a zone: its outline plus the buffer.

    Asking for a zone's bounding BOX was what failed live on Assam, July 2026:
    zones along the Brahmaputra are long and thin, so a 781 km2 zone had a box
    of about 8,000 km2, and every public server timed out or answered 504.
    The outline asks for a tenth of that. None when the outline cannot be used
    (the caller then falls back to the box).
    """
    try:
        from shapely.geometry import shape
        from shapely.validation import make_valid
        grown = make_valid(shape(geometry)).buffer(margin_deg)
        parts = list(getattr(grown, "geoms", [grown]))
        parts = sorted((p for p in parts if p.geom_type == "Polygon"), key=lambda p: -p.area)[:MAX_PARTS]
        out = []
        for part in parts:
            tolerance = margin_deg / 4
            ring = part.exterior.simplify(tolerance)
            while len(ring.coords) > MAX_POINTS:
                tolerance *= 2
                ring = part.exterior.simplify(tolerance)
            out.append(" ".join(f"{lat:.5f} {lon:.5f}" for lon, lat in list(ring.coords)[:-1]))
        return out or None
    except Exception:                            # noqa: BLE001 - the box still works
        return None


def overpass_query(zones, buffer_m=BUFFER_M):
    """One Overpass QL query covering every zone's outline, widened by the buffer."""
    margin = buffer_m / 1000 / KM_PER_DEG * 1.5     # a little extra for longitude
    statements = []
    for _, _, geometry in zones:
        polys = search_shapes(geometry, margin)
        if polys:
            filters = [f'(poly:"{p}")' for p in polys]
        else:
            s, w, n, e = (round(v, 5) for v in _bbox(geometry, margin))
            filters = [f"({s},{w},{n},{e})"]
        for area in filters:
            statements.append(f'node["place"~"^({"|".join(PLACE_TYPES)})$"]{area};')
            statements.append(f'way["highway"~"^({"|".join(ROAD_CLASSES)})$"]{area};')
    return f"[out:json][timeout:{TIMEOUT_S - 10}];(" + "".join(statements) + ");out tags geom;"


def _cache_path(query):
    return CACHE_DIR / f"{hashlib.sha256(query.encode()).hexdigest()[:24]}.json"


def fetch(query, now=None):
    """Overpass JSON for a query, from the disk cache when fresh."""
    now = now or time.time()
    path = _cache_path(query)
    if path.exists():
        entry = json.loads(path.read_text(encoding="utf-8"))
        if now - entry.get("fetched", 0) < CACHE_TTL_S:
            return entry["data"], entry["fetched"]
    import urllib.error

    tried = []
    data = None
    for url in OVERPASS_URLS:
        request = urllib.request.Request(
            url, data=urllib.parse.urlencode({"data": query}).encode(),
            headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            tried.append(f"{_host(url)}: HTTP {exc.code}")
            if exc.code not in RETRY_STATUS:
                break                            # a real error: the next server would agree
        except Exception as exc:                 # noqa: BLE001 - timeouts, DNS, bad JSON
            tried.append(f"{_host(url)}: {str(exc)[:80]}")
    if data is None:
        raise PlacesUnavailable("OpenStreetMap (Overpass) could not be reached: "
                                + "; ".join(tried))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched": now, "data": data}), encoding="utf-8")
    return data, now


def _host(url):
    return urllib.parse.urlparse(url).netloc or url


def clock():
    return time.monotonic()


def chunks(zones, size=ZONES_PER_QUERY):
    """Zones in rank order, a few per query - a very large zone on its own,
    so one heavy zone cannot sink the small ones queried with it."""
    out, group = [], []
    for zone in zones:
        if box_km2(zone[2]) > BIG_BOX_KM2:
            if group:
                out.append(group)
                group = []
            out.append([zone])
            continue
        group.append(zone)
        if len(group) == size:
            out.append(group)
            group = []
    if group:
        out.append(group)
    return out


def fetch_zones(zones, fetcher=None):
    """Overpass elements for every zone, a few zones per query.

    Returns (osm, fetched_at, failed_zone_ids). A chunk that fails is left
    out and named, so twenty zones looked up are not lost to five that timed
    out; if every chunk fails, PlacesUnavailable.
    """
    fetcher = fetcher or fetch
    elements, seen, failed, errors, latest = [], set(), [], [], 0
    started = clock()
    for group in chunks(zones):
        if clock() - started >= LOOKUP_BUDGET_S:
            failed.extend(zid for zid, _, _ in group)
            errors.append(f"time budget of {LOOKUP_BUDGET_S} s used up")
            continue
        try:
            osm, fetched = fetcher(overpass_query(group))
        except PlacesUnavailable as exc:
            failed.extend(zid for zid, _, _ in group)
            errors.append(str(exc))
            continue
        latest = max(latest, fetched)
        for element in osm.get("elements") or []:
            key = (element.get("type"), element.get("id"))
            if key not in seen:
                seen.add(key)
                elements.append(element)
    if len(failed) == len(zones):
        raise PlacesUnavailable(errors[0] if errors else "no zones could be looked up")
    return {"elements": elements}, latest, failed


# -------------------------------------------------------------- matching

class _Local:
    """Kilometres on a plane centred on the zones: good to well under 1% over
    a district, which is all a 500 m buffer and road lengths need."""

    def __init__(self, lat0):
        self.kx = KM_PER_DEG * math.cos(math.radians(lat0))
        self.ky = KM_PER_DEG

    def xy(self, lon, lat):
        return lon * self.kx, lat * self.ky

    def geometry(self, geometry):
        """The zone outline on the local plane, made valid.

        Zone outlines are vectorised from 200 m pixels and then simplified,
        and the result can touch or cross itself. Shapely then refuses to
        intersect a road with it ("TopologyException: side location
        conflict"), which surfaced live as a 500 from /places. make_valid
        repairs the ring without moving it; anything non-polygonal it leaves
        behind (stray lines, points) is dropped.
        """
        from shapely.geometry import MultiPolygon, Polygon, shape
        from shapely.ops import transform
        from shapely.validation import make_valid

        local = transform(lambda x, y, z=None: (x * self.kx, y * self.ky), shape(geometry))
        if local.is_valid:
            return local
        repaired = make_valid(local)
        parts = [g for g in getattr(repaired, "geoms", [repaired])
                 if isinstance(g, (Polygon, MultiPolygon))]
        if not parts:
            return local.buffer(0)
        return parts[0] if len(parts) == 1 else MultiPolygon(
            [p for g in parts for p in getattr(g, "geoms", [g])])


def match(osm, zones, buffer_m=BUFFER_M):
    """Places and roads per zone, from an Overpass response."""
    from shapely.geometry import LineString, Point

    if not zones:
        return []
    lats = [p[1] for _, _, g in zones for p in _bbox_points(g)]
    local = _Local(sum(lats) / len(lats))
    shapes = [(zid, rank, local.geometry(g)) for zid, rank, g in zones]
    buffered = [(zid, rank, s, s.buffer(buffer_m / 1000)) for zid, rank, s in shapes]

    per_zone = {zid: {"zone": zid, "rank": rank, "places": [], "roads": {}}
                for zid, rank, _, _ in buffered}
    for element in osm.get("elements") or []:
        tags = element.get("tags") or {}
        if element.get("type") == "node" and tags.get("place") in PLACE_TYPES:
            point = Point(*local.xy(element["lon"], element["lat"]))
            for zid, _, outline, area in buffered:
                if area.contains(point):
                    per_zone[zid]["places"].append({
                        "name": tags.get("name:en") or tags.get("name") or "(unnamed)",
                        "name_local": tags.get("name") if tags.get("name:en") else None,
                        "type": tags["place"],
                        "lat": round(element["lat"], 5), "lon": round(element["lon"], 5),
                        "inside": outline.contains(point),
                        "osm_id": element.get("id"),
                    })
        elif element.get("type") == "way" and tags.get("highway") in ROAD_CLASSES:
            coords = [local.xy(p["lon"], p["lat"]) for p in element.get("geometry") or []]
            if len(coords) < 2:
                continue
            line = LineString(coords)
            for zid, _, outline, _ in buffered:
                try:
                    km = line.intersection(outline).length
                except Exception:                # noqa: BLE001 - one bad road, not the list
                    continue
                if km <= 0:
                    continue
                key = (tags.get("ref") or "", tags.get("name") or "", tags["highway"])
                road = per_zone[zid]["roads"].setdefault(key, {
                    "ref": tags.get("ref"), "name": tags.get("name:en") or tags.get("name"),
                    "class": tags["highway"], "class_label": ROAD_CLASSES[tags["highway"]],
                    "km": 0.0})
                road["km"] += km

    zones_out = []
    for zid, _, _, _ in buffered:
        z = per_zone[zid]
        seen, places = set(), []
        for p in sorted(z["places"], key=lambda p: (not p["inside"], PLACE_ORDER[p["type"]], p["name"])):
            if p["osm_id"] in seen:
                continue
            seen.add(p["osm_id"])
            places.append(p)
        roads = sorted(({**r, "km": round(r["km"], 2)} for r in z["roads"].values() if r["km"] >= 0.05),
                       key=lambda r: (-r["km"], r["ref"] or "", r["name"] or ""))
        zones_out.append({"zone": zid, "rank": z["rank"], "places": places, "roads": roads})
    return zones_out


def _bbox_points(geometry):
    s, w, n, e = _bbox(geometry, 0)
    return [(w, s), (e, n)]


def totals(zones_out):
    places = {}
    for z in zones_out:
        for p in z["places"]:
            places[p["osm_id"]] = p
    by_type = {}
    for p in places.values():
        by_type[p["type"]] = by_type.get(p["type"], 0) + 1
    road_km = {}
    for z in zones_out:
        for r in z["roads"]:
            road_km[r["class"]] = round(road_km.get(r["class"], 0) + r["km"], 2)
    return {"places": len(places), "by_type": by_type, "road_km_by_class": road_km}


# ---------------------------------------------------------------- driver

def for_result(result, fetcher=None):
    """The `places` block for a stored flood result, or {"error": reason}."""
    zones = zone_outlines(result or {})
    summary = (result or {}).get("zones_summary") or {}
    if not zones:
        return {"error": "No zone outlines in this result, so there is nothing to look up."}
    try:
        osm, fetched, failed = fetch_zones(zones, fetcher)
    except PlacesUnavailable as exc:
        return {"error": str(exc)}
    looked_up = [z for z in zones if z[0] not in failed]
    try:
        zones_out = match(osm, looked_up)
    except Exception as exc:                     # noqa: BLE001 - names are never worth a 500
        return {"error": f"Village and road names could not be matched to the zones: "
                         f"{type(exc).__name__}: {str(exc)[:160]}"}
    caveats = list(CAVEATS)
    if failed:
        caveats.insert(0, f"OpenStreetMap did not answer for zones {', '.join(map(str, failed))}; "
                          "their villages and roads are not listed. Look them up again later.")
    return {
        "source": "OpenStreetMap via Overpass API",
        "attribution": ATTRIBUTION,
        "fetched_at": datetime.fromtimestamp(fetched, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "buffer_m": BUFFER_M,
        "zones_searched": len(looked_up),
        "zones_not_answered": failed,
        "zones_found": summary.get("count", len(zones)),
        "zones": zones_out,
        "totals": totals(zones_out),
        "caveats": caveats,
    }
