"""
Orbital elements for the Sentinel-1 satellites, from CelesTrak.

CelesTrak publishes General Perturbations (GP) elements - the same data as a
TLE - for every tracked object. We ask for every object whose name starts with
SENTINEL-1 in the JSON (OMM) format, which unlike the TLE format has no
five-digit catalogue-number limit.

CelesTrak asks users not to download the same data more than once every two
hours (the data is only updated that often), so the answer is cached on disk
and refreshed at most every REFRESH_S. If CelesTrak cannot be reached the last
good copy is served and marked stale; with no copy at all the list is empty
and the reason is given. Positions are worked out from these elements in the
browser (SGP4), so the server never has to compute anything per second.
"""

import json
import threading
import urllib.request
from datetime import datetime, timezone

from core import paths

URL = "https://celestrak.org/NORAD/elements/gp.php?NAME=SENTINEL-1&FORMAT=JSON"
REFRESH_S = 2 * 3600
TIMEOUT_S = 20
USER_AGENT = "Antardrishti/1.0 (flood mapping for India; final-year project)"

RETRY_AFTER_FAILURE_S = 15 * 60

_lock = threading.Lock()
_last_failure = {"at": None, "reason": None}

# OMM fields the browser's SGP4 (satellite.js json2satrec) needs.
OMM_FIELDS = ("OBJECT_NAME", "OBJECT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
              "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY", "EPHEMERIS_TYPE",
              "CLASSIFICATION_TYPE", "NORAD_CAT_ID", "ELEMENT_SET_NO", "REV_AT_EPOCH", "BSTAR",
              "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT")


def cache_file():
    return paths.CACHE / "orbits" / "sentinel1_gp.json"


def _now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url=URL, timeout=TIMEOUT_S):
    """The raw JSON list from CelesTrak. Raises on any failure."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    data = json.loads(body)
    if not isinstance(data, list):
        raise ValueError("CelesTrak did not return a list")
    return data


def label(object_name):
    """'SENTINEL-1C' -> 'Sentinel-1C'."""
    name = str(object_name or "").strip().upper()
    return "Sentinel-" + name.split("-", 1)[1] if name.startswith("SENTINEL-") else name.title()


def parse(records):
    """Sentinel-1 records only, each {name, key, norad, epoch, omm}, sorted by name.

    'key' is the short form ESA's plans use (S1C). Records missing the
    elements SGP4 needs are left out rather than half-shown.
    """
    out = []
    for record in records or []:
        name = str(record.get("OBJECT_NAME", "")).strip().upper()
        if not name.startswith("SENTINEL-1"):
            continue
        if any(record.get(f) is None for f in ("EPOCH", "MEAN_MOTION", "INCLINATION", "NORAD_CAT_ID")):
            continue
        out.append({
            "name": label(name),
            "key": "S1" + name.split("SENTINEL-1", 1)[1][:1],
            "norad": int(record["NORAD_CAT_ID"]),
            "epoch": record["EPOCH"],
            "omm": {f: record[f] for f in OMM_FIELDS if f in record},
        })
    return sorted(out, key=lambda s: s["name"])


def _read_cache():
    try:
        return json.loads(cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cache(payload):
    path = cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def elements(now=None, fetcher=fetch):
    """{satellites, fetched_at, stale, error, source} - fresh, cached or stale."""
    now = now or _now()
    with _lock:
        cached = _read_cache()
        if cached:
            age = (now - datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))).total_seconds()
            if 0 <= age < REFRESH_S:
                return {**cached, "stale": False, "error": None, "source": URL}
        # After a failure, wait before asking again: a down server should not
        # cost every visitor a 20-second timeout.
        failed = _last_failure["at"]
        if failed is not None and 0 <= (now - failed).total_seconds() < RETRY_AFTER_FAILURE_S:
            reason = _last_failure["reason"]
            if cached:
                return {**cached, "stale": True, "error": reason, "source": URL}
            return {"satellites": [], "fetched_at": None, "stale": True, "error": reason, "source": URL}
        try:
            satellites = parse(fetcher())
            if not satellites:
                raise ValueError("no Sentinel-1 elements in the reply")
            payload = {"satellites": satellites, "fetched_at": _iso(now)}
            _write_cache(payload)
            _last_failure.update(at=None, reason=None)
            return {**payload, "stale": False, "error": None, "source": URL}
        except Exception as exc:                 # noqa: BLE001 - any failure falls back
            reason = f"CelesTrak could not be reached ({type(exc).__name__})."
            _last_failure.update(at=now, reason=reason)
            if cached:
                return {**cached, "stale": True, "error": reason, "source": URL}
            return {"satellites": [], "fetched_at": None, "stale": True, "error": reason, "source": URL}
