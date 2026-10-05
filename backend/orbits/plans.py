"""
ESA's published Sentinel-1 acquisition plans: when each satellite is
scheduled to switch its radar on, in which mode, and over which ground.

A satellite flying over a place does not mean it records there - ESA plans
the acquisitions. ESA publishes that plan as KML files on Sentinel Online,
one series per satellite, each usually covering about 20 days and named after
its window:

    s1c_mp_user_20261002t172332_20261024t194000   (Sentinel-1C, 2 to 24 Oct)

Newer files replace older ones when the plan changes (for example, at short
notice for the Copernicus Emergency Management Service), so for each
satellite we use the most recently started file that still covers now.

Each data take in a file has its satellite, mode (IW/EW/SM/WV), polarisation,
UTC start and stop, absolute and relative orbit, and its ground footprint as a
ring of lon,lat points. "Next planned image of an area" is the first data take
still to finish whose footprint intersects the area.

The page is re-read at most every PAGE_REFRESH_S; a KML file never changes
once published, so each is parsed once and kept. Plans change: a planned
image can still be cancelled or moved, which the API says alongside it.
"""

import json
import re
import threading
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from core import paths

PAGE = "https://sentinels.copernicus.eu/copernicus/sentinel-1/acquisition-plans"
PAGE_REFRESH_S = 6 * 3600
RETRY_AFTER_FAILURE_S = 15 * 60
TIMEOUT_S = 30
USER_AGENT = "Antardrishti/1.0 (flood mapping for India; final-year project)"

# The host is optional: the page links some files with a full address and
# others relative to the site. Requiring the host silently dropped every
# relatively-linked file - in October 2026 that was all of Sentinel-1D and the
# newest Sentinel-1C plans, so the app showed one satellite and an out-of-date
# plan with no image of India in it.
LINK = re.compile(
    r"(?:https?://sentinels\.copernicus\.eu)?/documents/d/sentinel/"
    r"(s1([a-z])_mp_user_(\d{8}t\d{6})_(\d{8}t\d{6})[A-Za-z0-9_-]*)",
    re.IGNORECASE)

MODES = {"IW": "Interferometric Wide swath", "EW": "Extra Wide swath", "SM": "Stripmap", "WV": "Wave"}
POLARISATIONS = {"DV": "VV+VH", "DH": "HH+HV", "SV": "VV", "SH": "HH", "HH": "HH", "VV": "VV"}

_lock = threading.Lock()
_page = {"links": None, "fetched_at": None, "failed_at": None, "error": None}


def _now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def _stamp(text):
    """'20261002t172332' -> aware UTC datetime."""
    return datetime.strptime(text.upper(), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def _utc(text):
    """'2026-10-02T17:26:43' (UTC, no zone in the file) -> aware datetime."""
    return datetime.fromisoformat(text.strip().replace("Z", "")).replace(tzinfo=timezone.utc)


def _get(url, timeout=TIMEOUT_S):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode(response.headers.get_content_charset() or "iso-8859-1",
                                      errors="replace")


# ------------------------------------------------------------ the list page

def links(html):
    """Every plan file linked from the page: [{name, url, satellite, start, end}].

    The satellite comes from the file name, not from the heading it sits
    under - the page has listed a Sentinel-1D file under Sentinel-1C before.
    Duplicates (the same file linked twice) are dropped.
    """
    seen = {}
    for match in LINK.finditer(html or ""):
        name = match.group(1).lower()
        if name in seen:
            continue
        try:
            start, end = _stamp(match.group(3)), _stamp(match.group(4))
        except ValueError:
            continue
        seen[name] = {
            "name": name,
            "url": f"https://sentinels.copernicus.eu/documents/d/sentinel/{match.group(1)}",
            "satellite": "S1" + match.group(2).upper(),
            "start": start,
            "end": end,
        }
    return list(seen.values())


def current(files, now):
    """Per satellite, the file to trust now: the latest-starting one whose
    window has started and not ended. A satellite with no such file is left
    out - it is not being planned (for example, after its mission ended)."""
    chosen = {}
    for f in files:
        if not (f["start"] <= now < f["end"]):
            continue
        best = chosen.get(f["satellite"])
        if best is None or f["start"] > best["start"]:
            chosen[f["satellite"]] = f
    return [chosen[k] for k in sorted(chosen)]


# ------------------------------------------------------------ a plan file

def _value(placemark, name):
    for data in placemark.iter():
        if data.tag.endswith("Data") and data.get("name") == name:
            for child in data:
                if child.tag.endswith("value"):
                    return (child.text or "").strip()
    return None


def _ring(placemark):
    for node in placemark.iter():
        if node.tag.endswith("coordinates") and node.text:
            points = []
            for token in node.text.split():
                parts = token.split(",")
                if len(parts) >= 2:
                    points.append([round(float(parts[0]), 4), round(float(parts[1]), 4)])
            return points
    return []


def parse_kml(text):
    """The data takes in one plan file, in time order."""
    root = ET.fromstring(text.encode("iso-8859-1", errors="replace") if isinstance(text, str) else text)
    takes = []
    for placemark in root.iter():
        if not placemark.tag.endswith("Placemark"):
            continue
        start, stop = _value(placemark, "ObservationTimeStart"), _value(placemark, "ObservationTimeStop")
        ring = _ring(placemark)
        if not start or not stop or len(ring) < 3:
            continue
        mode = _value(placemark, "Mode") or ""
        pol = _value(placemark, "Polarisation") or ""

        def number(key):
            raw = _value(placemark, key)
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None

        takes.append({
            "satellite": (_value(placemark, "SatelliteId") or "").upper(),
            "datatake": _value(placemark, "DatatakeId"),
            "mode": mode,
            "mode_label": MODES.get(mode, mode),
            "polarisation": POLARISATIONS.get(pol, pol),
            "start": _iso(_utc(start)),
            "stop": _iso(_utc(stop)),
            "duration_s": number("ObservationDuration"),
            "orbit_absolute": number("OrbitAbsolute"),
            "orbit_relative": number("OrbitRelative"),
            "footprint": ring,
        })
    return sorted(takes, key=lambda t: t["start"])


def _kml_cache(name):
    return paths.CACHE / "orbits" / "plans" / f"{name}.json"


def datatakes_for(file, fetcher=_get):
    """The parsed data takes of one plan file, from disk when already read."""
    path = _kml_cache(file["name"])
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    takes = parse_kml(fetcher(file["url"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(takes), encoding="utf-8")
    return takes


# ------------------------------------------------------------ the whole plan

def plan(now=None, fetcher=_get):
    """{files, datatakes, fetched_at, stale, error, source} for every
    satellite currently being planned."""
    now = now or _now()
    with _lock:
        fresh = _page["fetched_at"] and (now - _page["fetched_at"]).total_seconds() < PAGE_REFRESH_S
        waiting = _page["failed_at"] and (now - _page["failed_at"]).total_seconds() < RETRY_AFTER_FAILURE_S
        if not fresh and not waiting:
            try:
                found = links(fetcher(PAGE))
                if not found:
                    raise ValueError("no plan files linked from the page")
                _page.update(links=found, fetched_at=now, failed_at=None, error=None)
            except Exception as exc:             # noqa: BLE001 - any failure falls back
                _page.update(failed_at=now,
                             error=f"ESA's acquisition-plan page could not be read ({type(exc).__name__}).")
        files = current(_page["links"] or [], now)

    takes, problems = [], []
    for f in files:
        try:
            takes.extend(datatakes_for(f, fetcher))
        except Exception as exc:                 # noqa: BLE001 - one bad file must not hide the rest
            problems.append(f"{f['name']} could not be read ({type(exc).__name__}).")
    error = " ".join(p for p in [_page["error"], *problems] if p) or None
    return {
        "files": [{**{k: f[k] for k in ("name", "url", "satellite")},
                   "start": _iso(f["start"]), "end": _iso(f["end"])} for f in files],
        "datatakes": sorted(takes, key=lambda t: t["start"]),
        "fetched_at": _iso(_page["fetched_at"]),
        "stale": bool(_page["error"]),
        "error": error,
        "source": PAGE,
    }


def reset():
    """Forget the cached page (tests)."""
    with _lock:
        _page.update(links=None, fetched_at=None, failed_at=None, error=None)


# ------------------------------------------------------------ over an area

def footprint_shape(take):
    from shapely.geometry import Polygon
    from shapely.validation import make_valid
    polygon = Polygon(take["footprint"])
    return polygon if polygon.is_valid else make_valid(polygon)


def upcoming(datatakes, shape, now=None, limit=5, horizon_days=30):
    """Planned data takes over `shape` (a shapely geometry) that have not
    finished yet, soonest first. One that is under way says so."""
    now = now or _now()
    horizon = now + timedelta(days=horizon_days)
    out = []
    for take in datatakes:
        stop, start = _utc(take["stop"]), _utc(take["start"])
        if stop <= now or start > horizon:
            continue
        try:
            if not footprint_shape(take).intersects(shape):
                continue
        except Exception:                        # noqa: BLE001 - a broken ring is skipped, not fatal
            continue
        out.append({**take, "in_progress": start <= now < stop})
        if len(out) >= limit:
            break
    return out
