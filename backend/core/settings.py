"""
Every production setting in one place, read from the environment (.env).

Read at call time, not import time, so a test - or an operator - can change
one without restarting Python. Each has a safe default: a fresh checkout is
locked down (login required, CORS limited to the local frontend) rather than
open.

    AUTH_REQUIRED          true    login needed for everything but /health
    SECRET_KEY             (none)  signs session tokens; without it a random
                                   key is made per process and every restart
                                   logs everyone out (a warning says so)
    SESSION_HOURS          12
    CORS_ORIGINS           http://localhost:5173,http://127.0.0.1:5173
    MAX_REQUEST_MB         25      bodies above this are refused before reading
    MAX_UNZIPPED_MB        100     a zip/KMZ may not expand beyond this
    ANALYSES_PER_HOUR      60      per user, for anything that spends quota
    UPLOAD_RETENTION_DAYS  7       uploaded boundaries and overlay images
    JOB_WORKERS            2       analyses run at once in the background
    LOG_FORMAT             json    or "text"
"""

import os


def _bool(name, default):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def auth_required():
    return _bool("AUTH_REQUIRED", True)


def secret_key():
    return os.environ.get("SECRET_KEY", "").strip() or None


def session_hours():
    return _int("SESSION_HOURS", 12)


def cors_origins():
    raw = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [o.strip() for o in raw.split(",") if o.strip()]


def max_request_bytes():
    return _int("MAX_REQUEST_MB", 25) * 1024 * 1024


def max_unzipped_bytes():
    return _int("MAX_UNZIPPED_MB", 100) * 1024 * 1024


def analyses_per_hour():
    return _int("ANALYSES_PER_HOUR", 60)


def upload_retention_days():
    return _int("UPLOAD_RETENTION_DAYS", 7)


def job_workers():
    return max(1, _int("JOB_WORKERS", 2))


def log_format():
    return os.environ.get("LOG_FORMAT", "json").strip().lower()


def problems():
    """Setup problems an operator should see at startup, in words."""
    out = []
    if auth_required() and not secret_key():
        out.append("SECRET_KEY is not set: sessions are signed with a random key and "
                   "everyone is logged out on every restart")
    if "*" in cors_origins():
        out.append("CORS_ORIGINS contains '*': any website can call this API from a "
                   "logged-in browser")
    return out
