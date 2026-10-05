"""
Passwords, account changes, login records, email verification and resets.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core import auth, mailer, passwords  # noqa: E402

GOOD = "Monsoon-River-2026!"


# ------------------------------------------------------------ the rules

@pytest.mark.parametrize("password, reason", [
    ("Ab1!", "at least 8"),
    ("abcdefg1!", "capital letter"),
    ("ABCDEFG1!", "small letter"),
    ("Abcdefgh!", "number"),
    ("Abcdefgh1", "symbol"),
    ("Password@123", "too common"),
    ("India@2026", "too common"),
    ("Qwerty#2026", "too common"),
    ("Aaaaaaa1!", None),
])
def test_password_rules(password, reason):
    found = passwords.problem(password)
    if reason is None:
        assert found is None
    else:
        assert found and reason in found


def test_a_password_made_from_your_name_or_email_is_refused():
    assert "must not contain" in passwords.problem("Asha-Floods-9!", ("Asha Rao", "asha@ddma.gov.in"))
    assert passwords.problem(GOOD, ("Asha Rao", "asha@ddma.gov.in")) is None


def test_the_checklist_reports_each_rule():
    assert passwords.checks("abc") == {"length": False, "upper": False, "lower": True,
                                       "digit": False, "symbol": False}
    assert all(passwords.checks(GOOD).values())


# ------------------------------------------------------------ the API

@pytest.fixture
def app(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import main
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789")
    auth._failures.clear()
    mailer._sent.clear()
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def smtp(monkeypatch):
    """Email switched on, with a fake mail server that keeps what it was given."""
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            pass

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setenv("SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("SMTP_USER", "antardrishti@example.org")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    return sent


SIGNUP = {"full_name": "Asha Rao", "email": "asha@ddma.example.org", "password": GOOD,
          "organisation": "DDMA", "user_type": "ddma"}
CSRF = {"X-Requested-With": "antardrishti"}


def token_from(message):
    url = message["X-Antardrishti-Link"]
    return url.split("token=", 1)[1]


def test_the_form_can_ask_for_the_rules(app):
    rules = app.get("/auth/password-rules").json()
    assert rules["min_length"] == 8 and [r["key"] for r in rules["rules"]][:2] == ["length", "upper"]


def test_a_weak_password_is_refused_at_sign_up_with_the_reason(app):
    response = app.post("/auth/signup", json={**SIGNUP, "password": "Password@123"})
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "weak_password"
    assert "common" in response.json()["detail"]["message"]


def test_changing_the_password_ends_other_sessions_but_not_this_one(app):
    phone = app.post("/auth/signup", json=SIGNUP).json()["token"]
    laptop = app.post("/auth/login", json={"username": SIGNUP["email"], "password": GOOD}).json()["token"]
    changed = app.post("/auth/password", headers={"Authorization": f"Bearer {laptop}"},
                       json={"current": GOOD, "new": "Brahmaputra-Rises-77#"})
    assert changed.status_code == 200, changed.text
    new_token = changed.json()["token"]
    assert app.get("/auth/me", headers={"Authorization": f"Bearer {phone}"}).json()["user"] is None
    assert app.get("/auth/me", headers={"Authorization": f"Bearer {new_token}"}).json()["user"]
    wrong = app.post("/auth/password", headers={"Authorization": f"Bearer {new_token}"},
                     json={"current": "not it", "new": "Another-Good-1!"})
    assert wrong.status_code == 400 and wrong.json()["detail"]["error"] == "wrong_password"


def test_sign_out_everywhere_ends_every_session(app):
    token = app.post("/auth/signup", json=SIGNUP).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert app.post("/auth/logout-all", headers=headers).status_code == 200
    assert app.get("/auth/me", headers=headers).json()["user"] is None


def test_every_login_is_recorded_for_the_user_and_the_admin(app):
    token = app.post("/auth/signup", json=SIGNUP).json()["token"]
    app.post("/auth/login", json={"username": SIGNUP["email"], "password": "Wrong-Guess-1!"})
    mine = app.get("/auth/activity", headers={"Authorization": f"Bearer {token}"}).json()["events"]
    kinds = [(e["kind"], e["ok"]) for e in mine]
    assert ("login", False) in kinds and ("signup", True) in kinds
    auth.create_user("admin1", "Admin-Strong-Pass-1!", "admin")
    admin = app.post("/auth/login", json={"username": "admin1", "password": "Admin-Strong-Pass-1!"}).json()["token"]
    everyone = app.get("/admin/logins", headers={"Authorization": f"Bearer {admin}"}).json()["events"]
    assert any(e["username"] == SIGNUP["email"] for e in everyone)
    assert app.get("/admin/logins", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_with_email_set_up_a_new_account_must_confirm_its_address(app, smtp):
    response = app.post("/auth/signup", json=SIGNUP)
    body = response.json()
    assert response.status_code == 200 and body["verification_sent"] and body["user"] is None
    assert "antardrishti_session" not in response.cookies, "no session before confirming"
    blocked = app.post("/auth/login", json={"username": SIGNUP["email"], "password": GOOD})
    assert blocked.status_code == 403 and blocked.json()["detail"]["error"] == "unverified"

    assert len(smtp) == 1 and smtp[0]["To"] == SIGNUP["email"]
    confirmed = app.post("/auth/verify", json={"token": token_from(smtp[0])})
    assert confirmed.status_code == 200 and confirmed.json()["user"]["verified"]
    again = app.post("/auth/verify", json={"token": token_from(smtp[0])})
    assert again.status_code == 400, "a link works once"
    assert app.post("/auth/login", json={"username": SIGNUP["email"], "password": GOOD}).status_code == 200


def test_resend_says_the_same_for_any_address(app, smtp):
    app.post("/auth/signup", json=SIGNUP)
    first = app.post("/auth/resend", json={"email": SIGNUP["email"]}).json()
    nobody = app.post("/auth/resend", json={"email": "nobody@example.org"}).json()
    assert first["message"] == nobody["message"]
    assert len(smtp) == 2, "the sign-up email and one resend"


def test_forgot_password_emails_a_one_time_link_and_says_the_same_for_strangers(app, smtp):
    app.post("/auth/signup", json=SIGNUP)
    app.post("/auth/verify", json={"token": token_from(smtp[0])})
    old_session = app.post("/auth/login", json={"username": SIGNUP["email"], "password": GOOD}).json()["token"]
    asked = app.post("/auth/forgot", json={"email": SIGNUP["email"]}).json()
    stranger = app.post("/auth/forgot", json={"email": "nobody@example.org"}).json()
    assert asked["message"] == stranger["message"]
    reset_mail = smtp[-1]
    assert "Reset" in reset_mail["Subject"]

    weak = app.post("/auth/reset", json={"token": token_from(reset_mail), "password": "password"})
    assert weak.status_code == 400
    # A refused password must not burn the link... it does, by design: ask again.
    app.post("/auth/forgot", json={"email": SIGNUP["email"]})
    done = app.post("/auth/reset", json={"token": token_from(smtp[-1]), "password": "New-Strong-Pass-42!"})
    assert done.status_code == 200 and done.json()["user"]["username"] == SIGNUP["email"]
    assert app.get("/auth/me", headers={"Authorization": f"Bearer {old_session}"}).json()["user"] is None
    assert app.post("/auth/login", json={"username": SIGNUP["email"],
                                         "password": "New-Strong-Pass-42!"}).status_code == 200


def test_an_expired_link_is_refused(app, monkeypatch):
    user = auth.register("Ravi K", "ravi@example.org", GOOD, "", "student")
    raw = auth.create_link_token(user["username"], "reset", minutes=-1)
    with pytest.raises(auth.AuthError, match="expired"):
        auth.reset_password(raw, "Fresh-Strong-Pass-1!")


def test_without_email_nobody_is_locked_out(app):
    response = app.post("/auth/signup", json=SIGNUP)
    assert response.json()["user"]["verified"], "verification needs email; without it accounts work at once"
    me = app.get("/auth/me").json()
    assert me["signup"]["verification"] is False and me["signup"]["email"] is False
