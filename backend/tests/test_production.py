"""
The production layer: sign-in and roles, CSRF, limits, error handling,
security headers, retention, background jobs and readiness.

Sign-in is off for the rest of the suite (conftest.py); every test here turns
it on, against a throwaway user database.
"""

import io
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core import auth, security  # noqa: E402

PASSWORD = "Correct-Horse-Battery-7"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789")
    auth._failures.clear()
    return tmp_path


# ------------------------------------------------------------ passwords, tokens

def test_passwords_are_salted_hashes_and_verify(db):
    one, two = auth.hash_password(PASSWORD), auth.hash_password(PASSWORD)
    assert one != two and PASSWORD not in one
    assert auth.verify_password(PASSWORD, one) and not auth.verify_password("wrong", one)


def test_weak_passwords_and_bad_usernames_are_refused(db):
    with pytest.raises(auth.AuthError, match="at least 8"):
        auth.create_user("asha", "Sh0rt!")
    with pytest.raises(auth.AuthError, match="must not contain your name"):
        auth.create_user("asha.k", "Asha.k-is-great-9")
    with pytest.raises(auth.AuthError, match="Usernames"):
        auth.create_user("a b", PASSWORD)


def test_a_bad_login_never_says_which_part_was_wrong_and_locks_after_five(db):
    auth.create_user("asha", PASSWORD, "analyst")
    for _ in range(auth.MAX_FAILURES):
        with pytest.raises(auth.AuthError) as info:
            auth.authenticate("asha", "Nope-nope-nope-1")
        assert info.value.status == 401
    with pytest.raises(auth.AuthError) as info:
        auth.authenticate("asha", PASSWORD)
    assert info.value.status == 423, "locked even with the right password"
    with pytest.raises(auth.AuthError) as unknown:
        auth.authenticate("nobody", "whatever whatever")
    assert unknown.value.message == "Wrong username or password, or the account is disabled."


def test_tokens_are_signed_expire_and_follow_the_account(db):
    user = auth.create_user("asha", PASSWORD, "analyst")
    token, expires = auth.issue_token(user, now=1000)
    assert auth.read_token(token, now=1001)["role"] == "analyst"
    body, sig = token.split(".")
    assert auth.read_token(body + "." + sig[:-2] + "AA", now=1001) is None, "tampered"
    assert auth.read_token(token, now=expires + 1) is None, "expired"
    auth.update_user("asha", role="viewer")
    assert auth.read_token(token, now=1001)["role"] == "viewer", "role change applies at once"
    auth.update_user("asha", active=False)
    assert auth.read_token(token, now=1001) is None, "deactivation applies at once"


def test_the_last_admin_cannot_be_removed_or_demoted(db):
    auth.create_user("root1", PASSWORD, "admin")
    with pytest.raises(auth.AuthError, match="last active admin"):
        auth.update_user("root1", role="viewer")
    with pytest.raises(auth.AuthError, match="last active admin"):
        auth.delete_user("root1", acting="someone")
    auth.create_user("root2", PASSWORD, "admin")
    with pytest.raises(auth.AuthError, match="your own"):
        auth.update_user("root2", role="viewer", acting="root2")
    auth.update_user("root2", role="viewer", acting="root1")


def test_bootstrap_admin_happens_once(db, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "chief")
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)
    assert auth.bootstrap_admin()["role"] == "admin"
    monkeypatch.setenv("ADMIN_PASSWORD", "a different password")
    assert auth.bootstrap_admin() is None, "never resets an existing install"


def test_roles_by_route():
    assert auth.required_role("GET", "/health") is None
    assert auth.required_role("GET", "/assets/index.js") is None, "the web app itself is public"
    assert auth.required_role("GET", "/analyze/abc/report.pdf") == "viewer"
    assert auth.required_role("POST", "/analyze") == "analyst"
    assert auth.required_role("POST", "/jobs/analyze") == "analyst"
    assert auth.required_role("GET", "/admin/users") == "admin"
    assert auth.required_role("DELETE", "/cache") == "admin"


# ------------------------------------------------------------- the API, signed in

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
    for name, role in (("admin1", "admin"), ("analyst1", "analyst"), ("viewer1", "viewer")):
        auth.create_user(name, PASSWORD, role)
    return TestClient(main.app, raise_server_exceptions=False), S


def login(client, name):
    response = client.post("/auth/login", json={"username": name, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


BODY = {"region": "testland", "post_start": "2018-08-01", "post_end": "2018-08-31", "use_llm": False}


def test_signed_out_requests_are_refused_but_health_and_the_app_load(app):
    client, _ = app
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    refused = client.get("/analyses")
    assert refused.status_code == 401
    assert refused.json()["detail"]["error"] == "login_required"


def test_each_role_can_do_exactly_its_part(app):
    client, _ = app
    viewer, analyst, admin = (login(client, n) for n in ("viewer1", "analyst1", "admin1"))
    assert client.get("/analyses", headers=bearer(viewer)).status_code == 200
    assert client.post("/analyze", json=BODY, headers=bearer(viewer)).status_code == 403
    assert client.post("/analyze", json=BODY, headers=bearer(analyst)).status_code == 200
    assert client.get("/admin/users", headers=bearer(analyst)).status_code == 403
    assert client.get("/admin/users", headers=bearer(admin)).status_code == 200


def test_the_cookie_works_for_links_and_needs_the_csrf_header_to_change_things(app):
    client, _ = app
    response = client.post("/auth/login", json={"username": "analyst1", "password": PASSWORD})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
    assert client.get("/analyses").status_code == 200, "the cookie alone reads"
    refused = client.post("/analyze", json=BODY)
    assert refused.status_code == 403 and refused.json()["detail"]["error"] == "csrf"
    assert client.post("/analyze", json=BODY, headers={"X-Requested-With": "antardrishti"}).status_code == 200
    client.post("/auth/logout")
    assert client.get("/analyses").status_code == 401


def test_admin_manages_users_through_the_api(app):
    client, _ = app
    admin = bearer(login(client, "admin1"))
    created = client.post("/admin/users", headers=admin,
                          json={"username": "district.officer", "password": PASSWORD, "role": "viewer"})
    assert created.status_code == 200 and created.json()["role"] == "viewer"
    assert "password_hash" not in json.dumps(client.get("/admin/users", headers=admin).json())
    assert client.patch("/admin/users/district.officer", headers=admin,
                        json={"role": "analyst"}).json()["role"] == "analyst"
    assert client.patch("/admin/users/admin1", headers=admin, json={"active": False}).status_code == 409
    assert client.delete("/admin/users/district.officer", headers=admin).status_code == 200


def test_quota_requests_are_rate_limited_per_user(app, monkeypatch):
    client, _ = app
    monkeypatch.setenv("ANALYSES_PER_HOUR", "2")
    token = bearer(login(client, "analyst1"))
    assert client.post("/analyze", json=BODY, headers=token).status_code == 200
    assert client.post("/analyze", json=BODY, headers=token).status_code == 200
    third = client.post("/analyze", json=BODY, headers=token)
    assert third.status_code == 429 and int(third.headers["Retry-After"]) > 0
    assert client.get("/analyses", headers=token).status_code == 200, "reading is not limited"


def test_an_oversized_body_is_refused_before_it_is_read(app, monkeypatch):
    client, _ = app
    monkeypatch.setenv("MAX_REQUEST_MB", "1")
    token = bearer(login(client, "analyst1"))
    big = b"x" * (2 * 1024 * 1024)
    response = client.post("/boundary/parse", headers=token,
                           files={"file": ("big.geojson", big, "application/json")})
    assert response.status_code == 413


def test_an_unexpected_error_is_a_500_with_an_incident_not_a_traceback(app, monkeypatch):
    client, _ = app
    from pipeline import analysis
    monkeypatch.setattr(analysis, "analyse_flood",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret internals")))
    response = client.post("/analyze", json=BODY, headers=bearer(login(client, "analyst1")))
    assert response.status_code == 500
    body = response.text
    assert "incident" in body and "secret internals" not in body and "Traceback" not in body


def test_security_headers_and_no_data_directory(app):
    client, _ = app
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "X-Request-ID" in response.headers
    assert client.get("/data/cache/anything.json").status_code == 404, \
        "the cache, raw data and uploaded boundaries are no longer served"


def test_cors_allows_only_the_configured_origins(app):
    client, _ = app
    good = client.options("/analyses", headers={"Origin": "http://localhost:5173",
                                                "Access-Control-Request-Method": "GET"})
    bad = client.options("/analyses", headers={"Origin": "https://evil.example",
                                               "Access-Control-Request-Method": "GET"})
    assert good.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert bad.headers.get("access-control-allow-origin") is None


# ------------------------------------------------------------------ jobs

def wait(client, job_id, headers, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}", headers=headers).json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job still {job['status']}")


def test_a_background_job_reports_its_steps_and_returns_the_result(app):
    client, S = app
    token = bearer(login(client, "analyst1"))
    started = client.post("/jobs/analyze", json=BODY, headers=token)
    assert started.status_code == 200
    job = wait(client, started.json()["job_id"], token)
    assert job["status"] == "done", job
    steps = [s["text"] for s in job["steps"]]
    for expected in ("Finding the area", "Measuring flood water", "Writing and checking the report"):
        assert expected in steps
    assert job["result"]["request_id"] and job["result"]["evidence"]


def test_a_job_is_private_to_its_owner_and_admins(app):
    client, _ = app
    owner = bearer(login(client, "analyst1"))
    job_id = client.post("/jobs/analyze", json=BODY, headers=owner).json()["job_id"]
    wait(client, job_id, owner)
    assert client.get(f"/jobs/{job_id}", headers=bearer(login(client, "viewer1"))).status_code == 404
    assert client.get(f"/jobs/{job_id}", headers=bearer(login(client, "admin1"))).status_code == 200


def test_a_refusal_inside_a_job_keeps_its_status_and_message(app, monkeypatch):
    client, _ = app
    import main
    from fastapi import HTTPException

    def not_found(payload):
        raise HTTPException(status_code=404, detail={"error": "region_not_found", "message": "No Atlantis."})

    monkeypatch.setattr(main, "resolve_area_or_fail", not_found)
    token = bearer(login(client, "analyst1"))
    job = wait(client, client.post("/jobs/analyze", json=BODY, headers=token).json()["job_id"], token)
    assert job["status"] == "failed"
    assert job["error"]["status"] == 404 and job["error"]["detail"]["message"] == "No Atlantis."


def test_jobs_left_running_by_a_restart_are_marked_failed(db):
    from core import jobs
    job_id = jobs.submit("analyze", {}, "x", lambda: {"ok": True}, run_inline=True)
    from core import store
    store.collection("jobs").update(job_id, set={"status": "running"})
    jobs.fail_interrupted()
    job = jobs.get(job_id)
    assert job["status"] == "failed" and job["error"]["detail"]["error"] == "interrupted"


# --------------------------------------------------------- limits on files

def test_a_zip_bomb_is_refused_before_extraction(monkeypatch):
    pytest.importorskip("shapely")
    from geo import boundary_upload as B
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", b"\0" * (30 * 1024 * 1024))       # 30 MB of zeros, ~30 kB zipped
    data = buffer.getvalue()
    assert len(data) < 200_000
    with pytest.raises(B.UnreadableBoundary, match="expands"):
        B.parse("bomb.kmz", data)


def test_old_uploads_are_purged(tmp_path):
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text("{}"), new.write_text("{}")
    import os
    os.utime(old, (time.time() - 30 * 86400,) * 2)
    assert security.purge_old_files(tmp_path, days=7) == 1
    assert not old.exists() and new.exists()


# ------------------------------------------------------------- readiness

def test_ready_reports_each_dependency(app, monkeypatch):
    client, _ = app
    from core import earth_engine
    monkeypatch.setattr(earth_engine, "initialize", lambda force=False: None)
    monkeypatch.setenv("GROQ_API_KEY", "x")
    ok = client.get("/ready")
    assert ok.status_code == 200 and ok.json()["checks"]["earth_engine"]["ok"]

    def down(force=False):
        raise earth_engine.EarthEngineUnreachable("Cannot reach Google's servers")
    monkeypatch.setattr(earth_engine, "initialize", down)
    not_ready = client.get("/ready")
    assert not_ready.status_code == 503
    assert "Cannot reach" in not_ready.json()["checks"]["earth_engine"]["message"]


def test_a_signed_out_caller_can_ask_whether_sign_in_is_needed(app):
    """Found live: /auth/me sat behind the login wall, answered 401, and the
    smoke check (and the web app) read that as "sign-in is off"."""
    client, _ = app
    response = client.get("/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["user"] is None and body["auth_required"] is True
    token = login(client, "viewer1")
    assert client.get("/auth/me", headers=bearer(token)).json()["user"]["username"] == "viewer1"


def test_a_job_answered_from_the_cache_still_says_what_it_did(app):
    """Found live: a job whose result was already stored finished with no
    steps, and the smoke check's "job reported its steps" failed."""
    client, _ = app
    token = bearer(login(client, "analyst1"))
    assert client.post("/analyze", json=BODY, headers=token).status_code == 200   # now cached
    job = wait(client, client.post("/jobs/analyze", json=BODY, headers=token).json()["job_id"], token)
    steps = [s["text"] for s in job["steps"]]
    assert steps == ["Checking for a stored result", "Found it - no new satellite computation needed"]
