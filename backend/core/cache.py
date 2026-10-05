"""
Analysis cache.

A Kerala analysis takes a couple of minutes and consumes Earth Engine compute
against a 150 EECU-hour monthly budget. Running the same query twice is money
and time thrown away, and during a demo it is the difference between an instant
answer and an awkward silence.

Keyed by a hash of the request. Two homes, chosen by core/store.py:

    MongoDB (MONGODB_URI set)  the `analyses` collection, the result
                               compressed - about 8x smaller, which matters
                               on the 512 MB free Atlas tier
    otherwise                  one JSON file per result under data/cache,
                               which survives a restart and can be read by hand
"""

import hashlib
import json
import os
import time
from pathlib import Path

from core import paths

CACHE_DIR = paths.CACHE
DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 30   # imagery for a past window never changes


def key_for(payload):
    """Stable hash of the request parameters. Doubles as the public request_id.

    Exposed so callers can fetch a stored analysis by id instead of trying to
    reconstruct the exact request. Rebuilding the key from assumed defaults is
    how the geojson route silently missed the cache.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


_key = key_for


def _path(key):
    return CACHE_DIR / f"{key}.json"


def _mongo_entry(key):
    from core import store
    if not store.mongo_uri():
        return False
    doc = store.collection("analyses").get(key, blob=True)
    if not doc or doc.get("_blob") is None:
        return None
    return {"stored_at": doc.get("stored_at_ts", 0), "request": doc.get("request"),
            "response": store.unpack(doc["_blob"])}


def get_by_id(request_id):
    """Stored response by request_id, ignoring TTL."""
    entry = _mongo_entry(request_id)
    if entry is not False:
        return (entry or {}).get("response")
    path = _path(request_id)
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("response")
    except (json.JSONDecodeError, OSError):
        return None


def request_by_id(request_id):
    """The request a stored response answered, or None.

    What a download rebuilds from: Earth Engine images are not stored, so a
    GeoTIFF is recomputed from the same request that produced the numbers.
    """
    entry = _mongo_entry(request_id)
    if entry is not False:
        return (entry or {}).get("request")
    path = _path(request_id)
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("request")
    except (json.JSONDecodeError, OSError):
        return None


def get(payload, ttl=DEFAULT_TTL_SECONDS):
    """Cached response, or None."""
    if os.environ.get("ANTARDRISHTI_NO_CACHE"):
        return None

    entry = _mongo_entry(_key(payload))
    if entry is None:
        return None
    if entry is False:
        path = _path(_key(payload))
        if not path.exists():
            return None
        try:
            with path.open(encoding="utf-8") as fh:
                entry = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    if ttl and time.time() - entry.get("stored_at", 0) > ttl:
        return None

    response = entry.get("response") or {}
    response["_cache"] = {
        "hit": True,
        "stored_at": entry.get("stored_at"),
        "age_seconds": int(time.time() - entry.get("stored_at", 0)),
    }
    return response


def put(payload, response):
    # Never cache the internal Earth Engine objects; they are not serialisable
    # and are meaningless in another process.
    stored = {k: v for k, v in response.items() if not k.startswith("_")}

    from core import store
    if store.mongo_uri():
        try:
            col = store.collection("analyses")
            key = _key(payload)
            col.delete(key)
            col.insert({"_id": key, "request": json.loads(json.dumps(payload, default=str)),
                        "stored_at": store.now_iso(), "stored_at_ts": time.time()},
                       blob=store.pack(stored))
        except Exception:                        # noqa: BLE001 - a failed cache write must never fail the request
            pass
        return response

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(_key(payload))

    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump({"stored_at": time.time(), "request": payload, "response": stored}, fh)
    except OSError:
        pass          # a failed cache write must never fail the request

    return response


def clear():
    from core import store
    if store.mongo_uri():
        return store.collection("analyses").delete_many({})
    if not CACHE_DIR.exists():
        return 0
    removed = 0
    for path in CACHE_DIR.glob("*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def stats():
    from core import store
    if store.mongo_uri():
        return {"entries": store.collection("analyses").count(), "backend": "mongodb"}
    if not CACHE_DIR.exists():
        return {"entries": 0, "bytes": 0}
    files = list(CACHE_DIR.glob("*.json"))
    return {
        "entries": len(files),
        "bytes": sum(f.stat().st_size for f in files),
    }
