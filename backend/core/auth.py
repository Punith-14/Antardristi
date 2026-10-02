"""
Accounts, roles and sessions - with nothing but the standard library.

Three roles, each including the ones below it:

    viewer    reads results, maps, PDFs and downloads
    analyst   also runs analyses, asks questions, uploads boundaries/photos
    admin     also manages users and the cache

Passwords are stored as PBKDF2-SHA256 hashes (240,000 iterations, a random
salt each). Sessions are HMAC-signed tokens carrying the username, role and
expiry; every request re-reads the user, so deactivating someone or changing
their role takes effect at once rather than when their token expires.

A browser holds the token in an HttpOnly, SameSite=Strict cookie, which is
what lets plain download links (PDF, GeoTIFF) work while logged in. A script
sends it as "Authorization: Bearer <token>". State-changing requests that
arrive on the cookie must also carry the X-Requested-With header, which a
cross-site form cannot set - the standard defence against CSRF.

Repeated failed logins lock that username for LOCKOUT_MINUTES.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from core import paths, settings

ROLES = ("viewer", "analyst", "admin")
RANK = {role: i for i, role in enumerate(ROLES)}
COOKIE = "antardrishti_session"
CSRF_HEADER = "x-requested-with"
PBKDF2_ITERATIONS = 240_000
MIN_PASSWORD = 10
MAX_FAILURES = 5
LOCKOUT_MINUTES = 15

_lock = threading.Lock()
_random_key = secrets.token_bytes(32)
_failures = {}


class AuthError(Exception):
    """A refusal with an HTTP status and a message for the user."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------- storage

def db_path():
    return os.environ.get("DATABASE_PATH") or str(paths.DATA / "app.db")


def connect():
    from pathlib import Path
    Path(db_path()).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path(), check_same_thread=False, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("""CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        last_login TEXT)""")
    return connection


@contextmanager
def _db():
    """A connection that commits on success and is always closed."""
    connection = connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _public(row):
    return None if row is None else {
        "username": row["username"], "role": row["role"], "active": bool(row["active"]),
        "created_at": row["created_at"], "last_login": row["last_login"]}


# --------------------------------------------------------------- passwords

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password, stored):
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt),
                                     int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def check_password_policy(password, username=""):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise AuthError(400, f"Passwords need at least {MIN_PASSWORD} characters.")
    if username and username.lower() in password.lower():
        raise AuthError(400, "The password must not contain the username.")


def _check_username(username):
    if not isinstance(username, str) or not (3 <= len(username) <= 40) or not all(
            c.isalnum() or c in "._-@" for c in username):
        raise AuthError(400, "Usernames are 3-40 letters, digits or . _ - @.")


# ------------------------------------------------------------------ users

def create_user(username, password, role="viewer"):
    _check_username(username)
    if role not in ROLES:
        raise AuthError(400, f"Role must be one of {', '.join(ROLES)}.")
    check_password_policy(password, username)
    with _lock, _db() as db:
        if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise AuthError(409, f"User {username!r} already exists.")
        db.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                   (username, hash_password(password), role, _now()))
    return get_user(username)


def get_user(username):
    with _db() as db:
        return _public(db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())


def list_users():
    with _db() as db:
        return [_public(r) for r in db.execute("SELECT * FROM users ORDER BY username")]


def count_admins(active_only=True):
    with _db() as db:
        sql = "SELECT COUNT(*) FROM users WHERE role = 'admin'" + (" AND active = 1" if active_only else "")
        return db.execute(sql).fetchone()[0]


def update_user(username, role=None, active=None, password=None, acting=None):
    user = get_user(username)
    if user is None:
        raise AuthError(404, f"No user {username!r}.")
    if role is not None and role not in ROLES:
        raise AuthError(400, f"Role must be one of {', '.join(ROLES)}.")
    losing_admin = user["role"] == "admin" and user["active"] and (
        (role is not None and role != "admin") or active is False)
    if losing_admin and count_admins() <= 1:
        raise AuthError(409, "This is the last active admin; make another admin first.")
    if acting == username and (active is False or (role is not None and role != user["role"])):
        raise AuthError(409, "You cannot change your own role or deactivate yourself.")
    with _lock, _db() as db:
        if role is not None:
            db.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
        if active is not None:
            db.execute("UPDATE users SET active = ? WHERE username = ?", (int(bool(active)), username))
        if password is not None:
            check_password_policy(password, username)
            db.execute("UPDATE users SET password_hash = ? WHERE username = ?",
                       (hash_password(password), username))
    return get_user(username)


def delete_user(username, acting=None):
    user = get_user(username)
    if user is None:
        raise AuthError(404, f"No user {username!r}.")
    if acting == username:
        raise AuthError(409, "You cannot delete your own account.")
    if user["role"] == "admin" and user["active"] and count_admins() <= 1:
        raise AuthError(409, "This is the last active admin.")
    with _lock, _db() as db:
        db.execute("DELETE FROM users WHERE username = ?", (username,))


def bootstrap_admin():
    """Create the first admin from ADMIN_USERNAME / ADMIN_PASSWORD, once.

    Does nothing when any user exists, so the environment variables can stay
    in .env without resetting anyone's password on restart.
    """
    username = os.environ.get("ADMIN_USERNAME", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not username or not password:
        return None
    with _db() as db:
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return None
    return create_user(username, password, "admin")


# ------------------------------------------------------------------ login

def _locked(username, now):
    times = [t for t in _failures.get(username, []) if now - t < LOCKOUT_MINUTES * 60]
    _failures[username] = times
    return len(times) >= MAX_FAILURES


def authenticate(username, password, now=None):
    """The user, or AuthError(401 / 423). One message for every bad login,
    so it does not reveal which usernames exist."""
    now = now or time.time()
    username = (username or "").strip()
    if _locked(username, now):
        raise AuthError(423, f"Too many failed attempts. Try again in {LOCKOUT_MINUTES} minutes.")
    with _db() as db:
        row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    ok = row is not None and verify_password(password or "", row["password_hash"]) and row["active"]
    if not ok:
        if row is None:
            verify_password(password or "", hash_password("timing-equaliser"))
        _failures.setdefault(username, []).append(now)
        raise AuthError(401, "Wrong username or password, or the account is disabled.")
    _failures.pop(username, None)
    with _lock, _db() as db:
        db.execute("UPDATE users SET last_login = ? WHERE username = ?", (_now(), username))
    return get_user(username)


# ----------------------------------------------------------------- tokens

def _key():
    key = settings.secret_key()
    return key.encode() if key else _random_key


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(user, now=None):
    now = int(now or time.time())
    payload = {"u": user["username"], "exp": now + settings.session_hours() * 3600}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}", payload["exp"]


def read_token(token, now=None):
    """The current user for a token, or None. Re-reads the user every time."""
    if not token or token.count(".") != 1:
        return None
    body, signature = token.split(".")
    expected = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("exp", 0) < (now or time.time()):
        return None
    user = get_user(payload.get("u"))
    return user if user and user["active"] else None


# ------------------------------------------------------------------ rules

# /auth/me is public on purpose: it is how the web app (and the smoke check)
# learn whether sign-in is needed at all. Behind the login wall it answered a
# signed-out caller with 401, which both read as "sign-in is off".
PUBLIC = {("GET", "/"), ("GET", "/health"), ("GET", "/ready"), ("GET", "/auth/me"),
          ("POST", "/auth/login"), ("POST", "/auth/logout")}

# Paths the API answers. Anything else is the web app itself (HTML, JS, CSS,
# images), which is public so the login page can load.
API_PREFIXES = ("/analyze", "/ask", "/route", "/analyses", "/regions", "/detect", "/cache",
                "/query", "/boundary", "/jobs", "/admin", "/auth", "/outputs", "/health",
                "/ready", "/docs", "/openapi.json", "/redoc")


def is_api(path):
    return path == "/" or any(path == p or path.startswith(p + "/") or path.startswith(p + "?")
                              for p in API_PREFIXES)


def required_role(method, path):
    """None for public, else the lowest role allowed."""
    if (method, path) in PUBLIC or method == "OPTIONS":
        return None
    if not is_api(path):
        return None
    if path.startswith("/admin") or path.startswith("/cache") or method == "DELETE":
        return "admin"
    if method in ("GET", "HEAD"):
        return "viewer"
    return "analyst"


def allows(user, role):
    return role is None or (user is not None and RANK[user["role"]] >= RANK[role])
