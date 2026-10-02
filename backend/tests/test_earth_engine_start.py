"""
Earth Engine failing to start: a network failure must not be reported as
"not authenticated", and the API must answer 503 with the reason, not a 500
traceback. Found live: DNS could not resolve oauth2.googleapis.com, and the
message sent the user off to re-authenticate.
"""

import socket
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

pytest.importorskip("ee")

from core import earth_engine as E  # noqa: E402


def dns_failure():
    """The live chain: gaierror -> urllib3 -> requests -> google.auth."""
    try:
        try:
            raise socket.gaierror(11001, "getaddrinfo failed")
        except socket.gaierror as inner:
            raise type("TransportError", (Exception,), {})(
                "HTTPSConnectionPool(host='oauth2.googleapis.com', port=443): Max retries "
                "exceeded with url: /token") from inner
    except Exception as outer:
        return outer


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(E, "_ready", False)


def test_a_dns_failure_is_reported_as_the_network(monkeypatch):
    def fail(**_):
        raise dns_failure()

    monkeypatch.setattr(E.ee, "Initialize", fail)
    with pytest.raises(E.EarthEngineUnreachable, match="internet connection") as info:
        E.initialize()
    assert "re-authentication is needed" in str(info.value)
    assert isinstance(info.value, RuntimeError), "old `except RuntimeError` still works"


def test_a_refused_credential_is_reported_as_authentication(monkeypatch):
    def fail(**_):
        raise Exception("Please authorize access to your Earth Engine account")

    monkeypatch.setattr(E.ee, "Initialize", fail)
    monkeypatch.delenv("EE_PROJECT_ID", raising=False)
    with pytest.raises(E.EarthEngineNotAuthenticated, match="earthengine authenticate") as info:
        E.initialize()
    assert "EE_PROJECT_ID is not set" in str(info.value)


def test_a_failure_is_not_remembered_so_the_next_request_retries(monkeypatch):
    calls = []

    def flaky(**_):
        calls.append(1)
        if len(calls) == 1:
            raise dns_failure()

    monkeypatch.setattr(E.ee, "Initialize", flaky)
    with pytest.raises(E.EarthEngineUnreachable):
        E.initialize()
    E.initialize()
    assert E.is_ready() and len(calls) == 2


def test_the_project_is_passed_when_set(monkeypatch):
    seen = {}
    monkeypatch.setattr(E.ee, "Initialize", lambda **kw: seen.update(kw))
    monkeypatch.setenv("EE_PROJECT_ID", "my-project")
    E.initialize()
    assert seen == {"project": "my-project"}
    assert E.configuration_problems() == []


def test_the_api_answers_503_with_the_reason(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import main
    from core import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)

    def fail(**_):
        raise dns_failure()

    monkeypatch.setattr(E.ee, "Initialize", fail)
    response = TestClient(main.app, raise_server_exceptions=False).post("/analyze", json={
        "region": "kerala", "post_start": "2018-08-01", "post_end": "2018-08-31"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "earth_engine_network"
    assert "internet connection" in detail["message"]


def test_a_service_account_is_used_when_configured(monkeypatch, tmp_path):
    key = tmp_path / "key.json"
    key.write_text("{}")
    seen = {}
    monkeypatch.setenv("EE_SERVICE_ACCOUNT", "svc@proj.iam.gserviceaccount.com")
    monkeypatch.setenv("EE_PRIVATE_KEY_FILE", str(key))
    monkeypatch.setenv("EE_PROJECT_ID", "proj")
    monkeypatch.setattr(E.ee, "ServiceAccountCredentials", lambda a, k: ("CREDS", a, k))
    monkeypatch.setattr(E.ee, "Initialize", lambda *a, **k: seen.update(args=a, kwargs=k))
    E.initialize()
    assert seen["args"][0] == ("CREDS", "svc@proj.iam.gserviceaccount.com", str(key))
    assert seen["kwargs"] == {"project": "proj"}


def test_a_half_configured_service_account_says_what_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("EE_SERVICE_ACCOUNT", "svc@proj.iam.gserviceaccount.com")
    monkeypatch.delenv("EE_PRIVATE_KEY_FILE", raising=False)
    with pytest.raises(E.EarthEngineNotAuthenticated, match="both EE_SERVICE_ACCOUNT"):
        E.initialize()
    monkeypatch.setenv("EE_PRIVATE_KEY_FILE", str(tmp_path / "missing.json"))
    with pytest.raises(E.EarthEngineNotAuthenticated, match="does not exist"):
        E.initialize()
