"""
Accounts, roles and sessions.

Three roles, each including the ones below it:

    viewer    reads results, maps, PDFs and downloads
    analyst   also runs analyses, asks questions, uploads boundaries/photos
    admin     also manages users and the cache

Passwords are stored as PBKDF2-SHA256 hashes (240,000 iterations, a random
salt each) and must pass the rules in core/passwords.py. Sessions are
HMAC-signed tokens carrying the username, a token version and the expiry;
every request re-reads the user, so deactivating someone, changing their
role, or "sign out everywhere" (which bumps the version) takes effect at once.

A browser holds the token in an HttpOnly, SameSite=Strict cookie, which is
what lets plain download links (PDF, GeoTIFF) work while logged in. A script
sends it as "Authorization: Bearer <token>". State-changing requests that
arrive on the cookie must also carry the X-Requested-With header, which a
cross-site form cannot set - the standard defence against CSRF.

Repeated failed logins lock that username for LOCKOUT_MINUTES. Every login,
successful or not, is recorded (login_events) for the user and the admin.

Anyone can create an account from the sign-up page. They say who they are
(USER_TYPES) and get SIGNUP_ROLE - admin is never self-chosen. When email is
set up (settings.smtp), the account must confirm its email before it can log
in; one-time links for that and for password resets live in `tokens`, stored
only as SHA-256 hashes.

Storage is core/store.py: MongoDB when configured, SQLite otherwise. A user
document's _id is its username (the email, for self-registered accounts).
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from core import passwords, settings, store

ROLES = ("viewer", "analyst", "admin")

# Who people say they are at sign-up. Every type gets the same access
# (SIGNUP_ROLE); the type tells the admin who the users are.
USER_TYPES = {
    "ddma": "District official (DDMA)",
    "sdma": "State official (SDMA)",
    "gis": "GIS analyst",
    "researcher": "Researcher",
    "student": "Student",
}
SIGNUP_ROLE = "analyst"
PROFILE_FIELDS = ("full_name", "email", "organisation", "user_type")
RANK = {role: i for i, role in enumerate(ROLES)}
COOKIE = "antardrishti_session"
CSRF_HEADER = "x-requested-with"
PBKDF2_ITERATIONS = 240_000
MIN_PASSWORD = passwords.MIN_LENGTH
MAX_FAILURES = 5
LOCKOUT_MINUTES = 15
VERIFY_HOURS = 24
RESET_MINUTES = 30

_lock = threading.Lock()
_random_key = secrets.token_bytes(32)
_failures = {}
_migrated = set()


class AuthError(Exception):
    """A refusal with an HTTP status, a message for the user and a code."""

    def __init__(self, status, message, code="auth"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


# ---------------------------------------------------------------- storage

def db_path():
    return store.sqlite_path()


def connect():
    """A raw SQLite connection to DATABASE_PATH (legacy tables, scripts)."""
    from pathlib import Path
    Path(db_path()).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path(), check_same_thread=False, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _migrate_legacy_users():
    """Copy accounts from the old `users` table (before the document store)
    into the store, once per database file. Nothing is deleted."""
    if store.mongo_uri() or db_path() in _migrated:
        return
    _migrated.add(db_path())
    try:
        with connect() as db:
            if not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
                return
            rows = db.execute("SELECT * FROM users").fetchall()
    except sqlite3.Error:
        return
    users = store.collection("users")
    for row in rows:
        data = dict(row)
        if users.get(data["username"]):
            continue
        doc = {"_id": data["username"], "password_hash": data["password_hash"], "role": data["role"],
               "active": bool(data["active"]), "created_at": data["created_at"],
               "last_login": data.get("last_login"), "verified": True, "tv": 0}
        for field in PROFILE_FIELDS:
            doc[field] = data.get(field)
        try:
            users.insert(doc)
        except store.DuplicateKey:
            pass


def _users():
    _migrate_legacy_users()
    return store.collection("users")


def _now():
    return store.now_iso()


def _public(doc):
    if doc is None:
        return None
    user = {"username": doc["_id"], "role": doc["role"], "active": bool(doc.get("active", True)),
            "created_at": doc.get("created_at"), "last_login": doc.get("last_login"),
            "verified": bool(doc.get("verified", True))}
    for field in PROFILE_FIELDS:
        user[field] = doc.get(field)
    user["user_type_label"] = USER_TYPES.get(doc.get("user_type"))
    return user


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


def check_password_policy(password, username="", *personal):
    found = passwords.problem(password, (username, *personal))
    if found:
        raise AuthError(400, found, "weak_password")


def _check_username(username):
    if not isinstance(username, str) or not (3 <= len(username) <= 80) or not all(
            c.isalnum() or c in "._-@+" for c in username):
        raise AuthError(400, "Usernames are 3-80 letters, digits or . _ - @ +.")


EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _clean(text, field, limit, required=True):
    text = (text or "").strip()
    if required and not text:
        raise AuthError(400, f"Enter your {field}.")
    if len(text) > limit:
        raise AuthError(400, f"Keep the {field} under {limit} characters.")
    return text


# ------------------------------------------------------------------ users

def create_user(username, password, role="viewer", verified=True, **profile):
    _check_username(username)
    if role not in ROLES:
        raise AuthError(400, f"Role must be one of {', '.join(ROLES)}.")
    check_password_policy(password, username, profile.get("full_name") or "")
    doc = {"_id": username, "password_hash": hash_password(password), "role": role, "active": True,
           "created_at": _now(), "last_login": None, "verified": bool(verified), "tv": 0}
    for field in PROFILE_FIELDS:
        doc[field] = profile.get(field)
    try:
        _users().insert(doc)
    except store.DuplicateKey as exc:
        raise AuthError(409, f"An account for {username!r} already exists. Log in instead.",
                        "exists") from exc
    return get_user(username)


def register(full_name, email, password, organisation="", user_type=""):
    """Self sign-up: an active SIGNUP_ROLE account.

    The email is the username, lower-cased, so it is also what people log in
    with. Verified at once unless email verification is switched on.
    """
    full_name = _clean(full_name, "name", 80)
    email = _clean(email, "email", 80).lower()
    if not EMAIL.match(email):
        raise AuthError(400, "That doesn't look like an email address.")
    organisation = _clean(organisation, "organisation", 120, required=False)
    if user_type not in USER_TYPES:
        raise AuthError(400, "Choose what you are, so we know who uses Antardrishti.")
    return create_user(email, password, SIGNUP_ROLE,
                       verified=not settings.email_verification_required(),
                       full_name=full_name, email=email, organisation=organisation,
                       user_type=user_type)


def get_user(username):
    if not username:
        return None
    return _public(_users().get(username))


def _find_login(name):
    """The user document for a username or an email."""
    users = _users()
    doc = users.get(name)
    if doc is None and "@" in name:
        doc = users.get(name.lower()) or users.find_one({"email": name.lower()})
    return doc


def list_users():
    return [_public(d) for d in _users().find(sort=[("_id", 1)])]


def count_admins(active_only=True):
    flt = {"role": "admin"}
    if active_only:
        flt["active"] = True
    return _users().count(flt)


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
    changes = {}
    if role is not None:
        changes["role"] = role
    if active is not None:
        changes["active"] = bool(active)
    if password is not None:
        check_password_policy(password, username, user.get("full_name") or "")
        changes["password_hash"] = hash_password(password)
    if changes:
        inc = {"tv": 1} if password is not None else None
        _users().update(username, set=changes, inc=inc)
    return get_user(username)


def delete_user(username, acting=None):
    user = get_user(username)
    if user is None:
        raise AuthError(404, f"No user {username!r}.")
    if acting == username:
        raise AuthError(409, "You cannot delete your own account.")
    if user["role"] == "admin" and user["active"] and count_admins() <= 1:
        raise AuthError(409, "This is the last active admin.")
    _users().delete(username)


def bootstrap_admin():
    """Create the first admin from ADMIN_USERNAME / ADMIN_PASSWORD, once.

    Does nothing when any user exists, so the environment variables can stay
    in .env without resetting anyone's password on restart.
    """
    username = os.environ.get("ADMIN_USERNAME", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not username or not password:
        return None
    if _users().count():
        return None
    return create_user(username, password, "admin")


# --------------------------------------------------------- account changes

def change_password(username, current, new):
    """The signed-in user changes their own password. Every other session
    (other browsers, phones) is signed out; the caller gets a fresh token."""
    doc = _users().get(username)
    if doc is None or not verify_password(current or "", doc["password_hash"]):
        raise AuthError(400, "Your current password is not right.", "wrong_password")
    if current == new:
        raise AuthError(400, "Choose a password different from the current one.", "same_password")
    check_password_policy(new, username, doc.get("full_name") or "")
    _users().update(username, set={"password_hash": hash_password(new)}, inc={"tv": 1})
    record_event(username, "password_changed", True)
    return get_user(username)


def sign_out_everywhere(username):
    _users().update(username, inc={"tv": 1})
    record_event(username, "signed_out_everywhere", True)


# ------------------------------------------------------------ login records

def record_event(username, kind, ok, address=None, detail=None):
    try:
        store.collection("login_events").insert({
            "_id": secrets.token_hex(10), "username": (username or "")[:80], "kind": kind,
            "ok": bool(ok), "address": address, "detail": detail, "at": _now(),
            "expire_at": store.iso_in(days=90)})
    except Exception:                            # noqa: BLE001 - a record must never block a login
        pass


def events(username=None, limit=50):
    flt = {"username": username} if username else None
    return [{k: e.get(k) for k in ("username", "kind", "ok", "address", "detail", "at")}
            for e in store.collection("login_events").find(flt, sort=[("at", -1)], limit=limit)]


# ------------------------------------------------------------------ login

def _locked(username, now):
    times = [t for t in _failures.get(username, []) if now - t < LOCKOUT_MINUTES * 60]
    _failures[username] = times
    return len(times) >= MAX_FAILURES


def authenticate(username, password, now=None, address=None):
    """The user, or AuthError(401 / 403 / 423). One message for every bad
    login, so it does not reveal which usernames exist."""
    now = now or time.time()
    username = (username or "").strip()
    if _locked(username, now):
        record_event(username, "login", False, address, "locked")
        raise AuthError(423, f"Too many failed attempts. Try again in {LOCKOUT_MINUTES} minutes.", "locked")
    doc = _find_login(username)
    ok = doc is not None and verify_password(password or "", doc["password_hash"]) and doc.get("active", True)
    if not ok:
        if doc is None:
            verify_password(password or "", hash_password("timing-equaliser"))
        _failures.setdefault(username, []).append(now)
        record_event(username, "login", False, address, "wrong password or unknown user")
        raise AuthError(401, "Wrong username or password, or the account is disabled.", "bad_login")
    _failures.pop(username, None)
    if not doc.get("verified", True) and settings.email_verification_required():
        record_event(doc["_id"], "login", False, address, "email not verified")
        raise AuthError(403, "Please confirm your email first - we sent you a link when you signed up.",
                        "unverified")
    _users().update(doc["_id"], set={"last_login": _now()})
    record_event(doc["_id"], "login", True, address)
    return get_user(doc["_id"])


# ----------------------------------------------------------- one-time links

def _token_hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def create_link_token(username, purpose, minutes):
    """A one-time secret for an emailed link. Only its hash is stored."""
    raw = secrets.token_urlsafe(32)
    tokens = store.collection("tokens")
    tokens.delete_many({"username": username, "purpose": purpose})
    tokens.insert({"_id": _token_hash(raw), "username": username, "purpose": purpose,
                   "created_at": _now(), "expire_at": store.iso_in(minutes=minutes)})
    return raw


def use_link_token(raw, purpose):
    """The username a valid link belongs to; the link is spent. Raises AuthError."""
    tokens = store.collection("tokens")
    record = tokens.get(_token_hash(raw or ""))
    if record is None or record.get("purpose") != purpose:
        raise AuthError(400, "This link is not valid. It may have been used already.", "bad_link")
    tokens.delete(record["_id"])
    if record["expire_at"] < _now():
        raise AuthError(400, "This link has expired. Ask for a new one.", "expired_link")
    return record["username"]


def verification_link_token(username):
    return create_link_token(username, "verify", VERIFY_HOURS * 60)


def verify_email(raw):
    username = use_link_token(raw, "verify")
    _users().update(username, set={"verified": True})
    record_event(username, "email_verified", True)
    return get_user(username)


def reset_link_token(email):
    """A reset token for the account with this email/username, or None (the
    caller says the same thing either way, so accounts cannot be probed)."""
    doc = _find_login((email or "").strip())
    if doc is None or not doc.get("active", True):
        return None, None
    return doc, create_link_token(doc["_id"], "reset", RESET_MINUTES)


def reset_password(raw, new):
    username = use_link_token(raw, "reset")
    doc = _users().get(username)
    if doc is None:
        raise AuthError(400, "This link is not valid.", "bad_link")
    check_password_policy(new, username, doc.get("full_name") or "")
    # Reading the email proves the address, so the account is verified too.
    _users().update(username, set={"password_hash": hash_password(new), "verified": True}, inc={"tv": 1})
    record_event(username, "password_reset", True)
    return get_user(username)


def purge_expired():
    """Spent and expired links and old login records."""
    now = _now()
    return (store.collection("tokens").delete_many({"expire_at": {"$lt": now}})
            + store.collection("login_events").delete_many({"expire_at": {"$lt": now}}))


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
    doc = _users().get(user["username"]) or {}
    payload = {"u": user["username"], "v": doc.get("tv", 0), "exp": now + settings.session_hours() * 3600}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}", payload["exp"]


def read_token(token, now=None):
    """The current user for a token, or None. Re-reads the user every time,
    so a disabled account or a bumped token version ends the session."""
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
    doc = _users().get(payload.get("u")) if payload.get("u") else None
    if doc is None or not doc.get("active", True) or doc.get("tv", 0) != payload.get("v", 0):
        return None
    return _public(doc)


# ------------------------------------------------------------------ rules

# /auth/me is public on purpose: it is how the web app (and the smoke check)
# learn whether sign-in is needed at all. Behind the login wall it answered a
# signed-out caller with 401, which both read as "sign-in is off".
PUBLIC = {("GET", "/"), ("GET", "/health"), ("GET", "/ready"), ("GET", "/auth/me"),
          ("POST", "/auth/login"), ("POST", "/auth/logout"), ("POST", "/auth/signup"),
          ("POST", "/auth/verify"), ("POST", "/auth/resend"), ("POST", "/auth/forgot"),
          ("POST", "/auth/reset"), ("GET", "/auth/password-rules"), ("GET", "/satellites")}

# Paths the API answers. Anything else is the web app itself (HTML, JS, CSS,
# images), which is public so the login page can load.
API_PREFIXES = ("/analyze", "/ask", "/route", "/analyses", "/regions", "/detect", "/cache",
                "/query", "/boundary", "/jobs", "/history", "/pictures", "/satellites", "/admin",
                "/auth", "/outputs", "/health", "/ready", "/docs", "/openapi.json", "/redoc")

# Signed-in users may delete or edit their OWN things here (the endpoint
# checks ownership); every other DELETE is for admins.
OWN_DELETE_PREFIXES = ("/history/", "/pictures/")


def is_api(path):
    return path == "/" or any(path == p or path.startswith(p + "/") or path.startswith(p + "?")
                              for p in API_PREFIXES)


def required_role(method, path):
    """None for public, else the lowest role allowed."""
    if (method, path) in PUBLIC or method == "OPTIONS":
        return None
    if not is_api(path):
        return None
    if path.startswith("/admin") or path.startswith("/cache"):
        return "admin"
    if method == "DELETE":
        return "viewer" if path.startswith(OWN_DELETE_PREFIXES) else "admin"
    if method in ("GET", "HEAD"):
        return "viewer"
    if path.startswith("/auth/") or path.startswith("/history/"):
        return "viewer"               # your own account and your own History
    return "analyst"


def allows(user, role):
    return role is None or (user is not None and RANK[user["role"]] >= RANK[role])
