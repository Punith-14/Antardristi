"""
The protections a public service needs and a prototype skips.

    headers        nosniff, no framing, no referrer leakage, a content policy
    body size      refused at the door when Content-Length is too big, before
                   the upload is read into memory
    rate limits    per user (or per address when not logged in), for the
                   requests that spend Earth Engine and Groq quota
    errors         an unexpected exception becomes a 500 with an incident id
                   - logged with its traceback, never sent to the browser
    request log    one structured line per request: who, what, how long
    retention      uploaded boundaries and overlay images older than the
                   retention period are deleted
"""

import json
import logging
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from core import settings

log = logging.getLogger("antardrishti")


# ------------------------------------------------------------------ logging

class _JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                 "message": record.getMessage()}
        entry.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            entry["traceback"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging():
    if getattr(configure_logging, "_done", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter() if settings.log_format() == "json"
                         else logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    configure_logging._done = True


def event(message, **fields):
    log.info(message, extra={"fields": fields})


# ------------------------------------------------------------------ headers

# Leaflet and its tiles: OpenStreetMap, Esri World Imagery (the satellite
# base map) and Earth Engine tile servers are images from other origins;
# everything else is this origin.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "img-src 'self' data: blob: https://*.tile.openstreetmap.org https://earthengine.googleapis.com "
    "https://server.arcgisonline.com; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # Not "no-referrer": OpenStreetMap's tile servers refuse requests that
    # carry no Referer ("Access blocked" tiles). This sends only the origin
    # (http://127.0.0.1:8000) to other sites - never a path - and the full
    # address only within this site, which is also the browsers' default.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}


def add_security_headers(response, path):
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    # The policy is for pages; API JSON and file downloads do not need it.
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    return response


# --------------------------------------------------------------- body size

def body_too_large(headers):
    """The refusal message, or None. Checked before the body is read."""
    try:
        length = int(headers.get("content-length") or 0)
    except ValueError:
        return "Content-Length is not a number."
    limit = settings.max_request_bytes()
    if length > limit:
        return (f"The request is {length / 1e6:.1f} MB; the limit is "
                f"{limit / 1024 / 1024:.0f} MB.")
    return None


# ------------------------------------------------------------- rate limits

class RateLimiter:
    """Sliding one-hour window per key. In memory, for one server process -
    the deployment guide runs one, and says what to use for several."""

    def __init__(self, window_s=3600):
        self.window_s = window_s
        self.hits = {}
        self.lock = threading.Lock()

    def allow(self, key, limit, now=None):
        """(allowed, retry_after_seconds)."""
        now = now if now is not None else time.monotonic()
        with self.lock:
            q = self.hits.setdefault(key, deque())
            while q and now - q[0] >= self.window_s:
                q.popleft()
            if len(q) >= limit:
                return False, int(self.window_s - (now - q[0])) + 1
            q.append(now)
            return True, 0

    def used(self, key, now=None):
        """How many hits `key` has in the current window (nothing is recorded)."""
        now = now if now is not None else time.monotonic()
        with self.lock:
            return sum(1 for t in self.hits.get(key, ()) if now - t < self.window_s)

    def reset(self):
        with self.lock:
            self.hits.clear()


ANALYSIS_LIMITER = RateLimiter()
SIGNUP_LIMITER = RateLimiter()

# Requests that spend Earth Engine or Groq quota.
QUOTA_PATHS = ("/analyze", "/ask", "/jobs", "/analyze/series", "/analyze/surface",
               "/analyze-upload", "/query")


def spends_quota(method, path):
    if method != "POST":
        return path.endswith("/report/hi") or path.endswith("/export/flood.zip") \
            or path.endswith("/places") or path == "/satellites/area"
    return any(path == p or path.startswith(p + "/") for p in QUOTA_PATHS)


# ----------------------------------------------------------------- errors

def incident_id():
    return uuid.uuid4().hex[:12]


# -------------------------------------------------------------- retention

def purge_old_files(directory, days=None, patterns=("*",), now=None):
    """Delete files older than `days` in `directory`. Returns how many."""
    days = settings.upload_retention_days() if days is None else days
    now = now or time.time()
    directory = Path(directory)
    if not directory.exists():
        return 0
    removed = 0
    for pattern in patterns:
        for path in directory.glob(pattern):
            if path.is_file() and now - path.stat().st_mtime > days * 86400:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
    return removed
