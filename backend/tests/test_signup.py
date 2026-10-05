"""
Self sign-up, the History list and the satellite base map.

Anyone may create an account and gets SIGNUP_ROLE straight away - the user
type they pick says who they are, not what they may do. Admin is never
self-chosen. The History list is built from finished jobs and leads with the
same figure the evidence record holds.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core import auth, history, security  # noqa: E402

PASSWORD = "Monsoon-River-2026!"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789")
    auth._failures.clear()
    return tmp_path


# ------------------------------------------------------------- accounts

def test_sign_up_gives_the_signup_role_at_once_whatever_the_type(db):
    for i, kind in enumerate(auth.USER_TYPES):
        user = auth.register(f"Person {i}", f"p{i}@example.org", PASSWORD, "DDMA Morigaon", kind)
        assert user["role"] == auth.SIGNUP_ROLE == "analyst"
        assert user["active"] and user["user_type"] == kind
        assert user["user_type_label"] == auth.USER_TYPES[kind]
        assert user["full_name"] == f"Person {i}" and user["organisation"] == "DDMA Morigaon"


def test_admin_is_not_a_user_type_and_unknown_types_are_refused(db):
    assert "admin" not in auth.USER_TYPES
    for bad in ("admin", "", "superuser"):
        with pytest.raises(auth.AuthError, match="Choose what you are"):
            auth.register("Asha Rao", "asha@example.org", PASSWORD, "", bad)


def test_the_email_is_the_login_and_is_case_insensitive(db):
    auth.register("Asha Rao", "Asha.Rao@Example.org", PASSWORD, "", "gis")
    assert auth.get_user("asha.rao@example.org")["email"] == "asha.rao@example.org"
    assert auth.authenticate("ASHA.RAO@example.org", PASSWORD)["username"] == "asha.rao@example.org"


def test_sign_up_checks_its_fields(db):
    with pytest.raises(auth.AuthError, match="Enter your name"):
        auth.register("  ", "a@example.org", PASSWORD, "", "student")
    with pytest.raises(auth.AuthError, match="email address"):
        auth.register("Asha", "not-an-email", PASSWORD, "", "student")
    with pytest.raises(auth.AuthError, match="at least 8"):
        auth.register("Asha", "asha@example.org", "Sh0rt!", "", "student")
    with pytest.raises(auth.AuthError, match="must not contain your name"):
        auth.register("Asha", "asha@example.org", "Asha-2026-Monsoon", "", "student")
    auth.register("Asha", "asha@example.org", PASSWORD, "", "student")
    with pytest.raises(auth.AuthError) as twice:
        auth.register("Asha", "asha@example.org", PASSWORD, "", "student")
    assert twice.value.status == 409 and "Log in instead" in twice.value.message


def test_an_older_user_file_gains_the_profile_columns(db):
    path = db / "app.db"
    old = sqlite3.connect(path)
    old.execute("""CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
        role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
        last_login TEXT)""")
    old.execute("INSERT INTO users VALUES ('punith', ?, 'admin', 1, '2026-09-01T00:00:00Z', NULL)",
                (auth.hash_password(PASSWORD),))
    old.commit()
    old.close()
    user = auth.authenticate("punith", PASSWORD)
    assert user["role"] == "admin" and user["full_name"] is None and user["user_type_label"] is None


def test_routes_signup_is_public_and_history_needs_sign_in():
    assert auth.required_role("POST", "/auth/signup") is None
    assert auth.required_role("GET", "/history") == "viewer"
    assert auth.required_role("POST", "/jobs/surface") == "analyst"


def test_the_satellite_base_map_is_allowed_by_the_page_policy():
    assert "https://server.arcgisonline.com" in security.CONTENT_SECURITY_POLICY
    assert "https://*.tile.openstreetmap.org" in security.CONTENT_SECURITY_POLICY


# ------------------------------------------------------------- history rows

RESULT = {
    "request_id": "abc123",
    "region": {"name": "Morigaon"},
    "period": {"post": {"start": "2026-07-01", "end": "2026-07-10"}},
    "evidence": [
        {"id": "E1", "quantity": "flood_extent", "value": 684.2, "unit": "km2"},
        {"id": "E2", "quantity": "flood_extent_fraction", "value": 4.1, "unit": "percent"},
    ],
    "population": {"people_in_flood": {"low": 180000, "high": 210000}},
    "verification": {"passed": True},
}


def test_a_history_row_leads_with_the_evidence_figure():
    row = history.summarise({"id": "j1", "kind": "analyze", "status": "done", "result": RESULT,
                             "created_at": "2026-07-11T00:00:00Z"}, {"region": "morigaon"})
    assert row["headline"] == {"label": "Flooded", "value": 684.2, "unit": "km2"}
    assert row["people"] == {"low": 180000, "high": 210000}
    assert row["place"] == "Morigaon" and row["analysis"] == "Flood extent"
    assert row["period"] == {"start": "2026-07-01", "end": "2026-07-10"}
    assert row["verified"] and row["request_id"] == "abc123"


def test_surface_rows_use_their_own_area_and_unfinished_jobs_are_left_out():
    surface = {"request_id": "s1", "analysis_label": "Vegetation health",
               "evidence": [{"id": "E1", "quantity": "vegetation_area", "value": 12.5, "unit": "km2"}]}
    row = history.summarise({"id": "j2", "kind": "surface", "status": "done", "result": surface},
                            {"region": "punjab", "post_start": "2023-09-01"})
    assert row["headline"]["label"] == "Vegetation area" and row["analysis"] == "Vegetation health"
    assert row["place"] == "Punjab"
    assert history.summarise({"id": "j3", "kind": "analyze", "status": "running"}) is None


def test_a_series_row_reports_its_peak_month():
    series = {"points": [{"label": "2026-06", "value": 10.0}, {"label": "2026-07", "value": 30.0},
                         {"label": "2026-08", "value": None}]}
    row = history.summarise({"id": "j4", "kind": "series", "status": "done", "result": series},
                            {"region": "assam", "start": "2026-06-01", "end": "2026-08-31"})
    assert row["months"] == 3 and row["headline"]["value"] == 30.0
    assert "2026-07" in row["headline"]["label"] and row["request_id"] is None


# ------------------------------------------------------------- the API

@pytest.fixture
def app(db, tmp_path, monkeypatch):
    pytest.importorskip("ee")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import flood_scenario as S
    import main
    from core import cache

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    region = S.install(monkeypatch)
    monkeypatch.setattr(main, "resolve_area_or_fail", lambda payload: (region, dict(S.META)))
    return TestClient(main.app, raise_server_exceptions=False)


SIGNUP = {"full_name": "Asha Rao", "email": "asha@ddma.example.org", "password": PASSWORD,
          "organisation": "DDMA Morigaon", "user_type": "ddma"}


def test_signing_up_through_the_api_signs_you_in_with_full_access(app):
    me = app.get("/auth/me").json()
    assert me["signup"]["enabled"] and {"key": "student", "label": "Student"} in me["signup"]["user_types"]
    response = app.post("/auth/signup", json={**SIGNUP, "role": "admin"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["role"] == "analyst", "a role sent with the form is ignored"
    assert "antardrishti_session" in response.cookies
    assert app.get("/auth/me").json()["user"]["username"] == "asha@ddma.example.org"
    started = app.post("/jobs/analyze", headers={"X-Requested-With": "antardrishti"},
                       json={"region": "testland", "post_start": "2018-08-01",
                             "post_end": "2018-08-31", "use_llm": False})
    assert started.status_code == 200, "a new account can run analyses at once"


def test_sign_up_can_be_closed_and_is_rate_limited(app, monkeypatch):
    monkeypatch.setenv("SIGNUP_ENABLED", "false")
    closed = app.post("/auth/signup", json=SIGNUP)
    assert closed.status_code == 403 and closed.json()["detail"]["error"] == "signup_closed"
    monkeypatch.setenv("SIGNUP_ENABLED", "true")
    monkeypatch.setenv("SIGNUPS_PER_HOUR", "2")
    codes = [app.post("/auth/signup", json={**SIGNUP, "email": f"u{i}@example.org"}).status_code
             for i in range(3)]
    assert codes == [200, 200, 429]


def test_history_lists_your_own_finished_analyses(app):
    import time
    a = app.post("/auth/signup", json=SIGNUP).json()["token"]
    b = app.post("/auth/signup", json={**SIGNUP, "email": "other@example.org"}).json()["token"]
    headers = {"Authorization": f"Bearer {a}"}
    job = app.post("/jobs/analyze", headers=headers,
                   json={"region": "testland", "post_start": "2018-08-01",
                         "post_end": "2018-08-31", "use_llm": False}).json()["job_id"]
    deadline = time.time() + 20
    while app.get(f"/jobs/{job}", headers=headers).json()["status"] not in ("done", "failed"):
        assert time.time() < deadline
        time.sleep(0.05)
    body = app.get("/history", headers=headers).json()
    assert body["usage"]["used"] >= 1 and body["usage"]["limit"] > 0
    items = body["items"]
    assert len(items) == 1 and items[0]["job_id"] == job
    assert items[0]["headline"]["label"] == "Flooded" and items[0]["request_id"]
    assert app.get("/history", headers={"Authorization": f"Bearer {b}"}).json()["items"] == []
    app.cookies.clear()
    assert app.get("/history").status_code == 401


def test_map_tiles_get_the_referer_openstreetmap_requires():
    """Found live: with "no-referrer" every OSM tile came back "Access blocked"."""
    policy = security.SECURITY_HEADERS["Referrer-Policy"]
    assert policy == "strict-origin-when-cross-origin", "origin only, never the path"
