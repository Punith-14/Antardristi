"""
One place that starts Earth Engine.

There were three copies of this before - in analysis.py, regions.py and nearly
a fourth in footprint.py - and the fourth is what surfaced the problem: a new
code path reached ee.Geometry without passing through any of the existing
initialisers and died with "Earth Engine client library not initialized" at
request time rather than at startup.

Duplicated setup fails that way by nature. Every new entry point has to
remember to call its own copy, and forgetting is invisible until the request
that needs it.

Two different failures used to share one message. "Earth Engine is not
authenticated" was raised even when the real cause was that this computer
could not reach Google at all (no internet, DNS, VPN or firewall) - which
sends you off re-authenticating for nothing. They are now told apart:

    EarthEngineUnreachable     the network: Google's servers could not be reached
    EarthEngineNotAuthenticated the credentials or project are missing or refused

Both subclass RuntimeError, so existing `except RuntimeError` still catches
them, and main.py turns both into a 503 with the message instead of a 500
with a traceback.
"""

import os
import socket

import ee

_ready = False


class EarthEngineUnavailable(RuntimeError):
    """Earth Engine could not be started. The message says what to do."""


class EarthEngineUnreachable(EarthEngineUnavailable):
    pass


class EarthEngineNotAuthenticated(EarthEngineUnavailable):
    pass


# Names of exception types that mean "the network", wherever they come from:
# socket, urllib3, requests and google-auth all wrap each other.
_NETWORK_TYPES = (
    "gaierror", "NameResolutionError", "ConnectionError", "MaxRetryError",
    "NewConnectionError", "TransportError", "Timeout", "ConnectTimeout",
    "ReadTimeout", "ConnectionRefusedError", "ConnectionResetError",
)
_NETWORK_TEXT = (
    "getaddrinfo failed", "failed to resolve", "name or service not known",
    "temporary failure in name resolution", "max retries exceeded",
    "connection refused", "connection reset", "timed out", "network is unreachable",
)


def is_network_error(exc):
    """True if anything in the exception's cause chain is a network failure."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (socket.gaierror, ConnectionError, TimeoutError)):
            return True
        if type(exc).__name__ in _NETWORK_TYPES:
            return True
        text = str(exc).lower()
        if any(t in text for t in _NETWORK_TEXT):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def service_account_credentials():
    """Service-account credentials when configured, else None.

    For deployment. A personal `earthengine authenticate` login works on a
    developer's machine; a server should run as a Google Cloud service
    account registered for Earth Engine:

        EE_SERVICE_ACCOUNT=name@project.iam.gserviceaccount.com
        EE_PRIVATE_KEY_FILE=/run/secrets/ee-key.json   (never committed)
    """
    account = os.environ.get("EE_SERVICE_ACCOUNT", "").strip()
    key_file = os.environ.get("EE_PRIVATE_KEY_FILE", "").strip()
    if not account and not key_file:
        return None
    if not (account and key_file):
        raise EarthEngineNotAuthenticated(
            "Set both EE_SERVICE_ACCOUNT and EE_PRIVATE_KEY_FILE, or neither.")
    if not os.path.exists(key_file):
        raise EarthEngineNotAuthenticated(
            f"EE_PRIVATE_KEY_FILE points to {key_file}, which does not exist.")
    return ee.ServiceAccountCredentials(account, key_file)


def initialize(force=False):
    """Start Earth Engine once per process.

    Idempotent: ee.Initialize does a network round trip to fetch the algorithm
    list, and calling it on every request adds that latency to every analysis.
    A failure is not remembered, so the next request tries again - once the
    network is back, the app recovers without a restart.
    """
    global _ready
    if _ready and not force:
        return

    project_id = os.environ.get("EE_PROJECT_ID")
    try:
        credentials = service_account_credentials()
        if credentials is not None:
            # A server: a Google Cloud service account, not a person's login.
            ee.Initialize(credentials, project=project_id)
        elif project_id:
            ee.Initialize(project=project_id)
        else:
            ee.Initialize()
    except EarthEngineUnavailable:
        raise
    except Exception as exc:
        if is_network_error(exc):
            raise EarthEngineUnreachable(
                "Cannot reach Google's servers to start Earth Engine (the address "
                "could not be resolved or the connection failed). Check this "
                "computer's internet connection, VPN, proxy or firewall, then try "
                "again - no re-authentication is needed for this."
            ) from exc
        missing = "" if project_id else (
            " EE_PROJECT_ID is not set in backend/.env - add your Earth Engine "
            "Cloud project ID there."
        )
        raise EarthEngineNotAuthenticated(
            "Earth Engine refused to start: the credentials or project were not "
            "accepted. Run `earthengine authenticate` and check EE_PROJECT_ID in "
            f"backend/.env.{missing} ({type(exc).__name__}: {str(exc)[:160]})"
        ) from exc

    _ready = True


def is_ready():
    return _ready


def configuration_problems():
    """Things wrong with the setup that can be seen without the network."""
    problems = []
    if not os.environ.get("EE_PROJECT_ID"):
        problems.append("EE_PROJECT_ID is not set in backend/.env")
    return problems
