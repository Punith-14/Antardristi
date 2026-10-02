"""
B6: choosing the sensor and method in the website.

The catalogue the form reads must carry each option's accuracy from the
detectors' own constants, and each option's flags (needs a baseline, works
in latest-pass mode) must match what the endpoint actually accepts - the
frontend mirrors those flags, so a wrong flag would be a form that sends
requests the server refuses.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

pytest.importorskip("ee")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import flood_scenario as S
    import main
    from core import cache
    from detection import sar

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    region = S.install(monkeypatch)
    monkeypatch.setattr(main, "resolve_area_or_fail", lambda payload: (region, dict(S.META)))
    monkeypatch.setattr(sar, "latest_acquisition", lambda region: {
        "date": "2018-08-01", "relative_orbit": 63, "recent_passes": ["2018-08-01"],
        "polarisations": "VV+VH", "lookback_days": 60})

    import numpy as np
    from fake_ee import FakeFloat
    monkeypatch.setattr(sar, "baseline_composite",
                        lambda *a, **k: FakeFloat(np.full(S.SHAPE, -12.0)))
    monkeypatch.setattr(sar, "change_mask", lambda post, pre, **k: post.mask())
    return TestClient(main.app), S


def options():
    from pipeline import analysis
    return {o["key"]: o for o in analysis.method_options(200)}


# ------------------------------------------------------------- the catalogue

def test_the_catalogue_offers_all_three_with_their_own_numbers():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import main
    from detection import optical, sar

    methods = {m["key"]: m for m in
               TestClient(main.app).get("/analyses").json()["flood"]["methods"]}
    assert set(methods) == {"radar_threshold", "optical_threshold", "radar_change"}
    assert methods["radar_threshold"]["validation"] == sar.rule_for_scale(200)["validation"]
    assert methods["optical_threshold"]["validation"] == optical.VALIDATION
    assert methods["radar_change"]["validation"] == sar.CHANGE_VALIDATION
    # Caveats travel with the numbers they qualify.
    assert methods["optical_threshold"]["caveat"] == optical.VALIDATION_CAVEAT
    assert methods["radar_change"]["caveat"] == sar.CHANGE_VALIDATION_CAVEAT
    assert sum(1 for m in methods.values() if m["default"]) == 1


def test_the_numbers_are_read_not_copied(monkeypatch):
    """Change a constant and the catalogue follows - no second copy to drift."""
    from detection import optical
    monkeypatch.setattr(optical, "VALIDATION", {"dataset": "x", "iou": 0.1,
                                                "precision": 0.2, "recall": 0.3})
    assert options()["optical_threshold"]["validation"]["iou"] == 0.1


def test_the_rule_text_names_the_threshold_in_use():
    from detection import optical, sar
    o = options()
    assert f"{sar.rule_for_scale(200)['db']} dB" in o["radar_threshold"]["rule"]
    assert str(optical.THRESHOLD) in o["optical_threshold"]["rule"]


# ------------------------------------- the flags match what the server accepts

def body(S, option, baseline, **extra):
    return {"region": "testland", "post_start": S.POST[0], "post_end": S.POST[1],
            "pre_start": S.PRE[0] if baseline else None,
            "pre_end": S.PRE[1] if baseline else None,
            "sensor": option["sensor"],
            "method": None if option["method"] == "threshold" else option["method"],
            "scale": 200, "use_llm": False, **extra}


@pytest.mark.parametrize("key", ["radar_threshold", "optical_threshold", "radar_change"])
def test_every_option_runs_when_its_needs_are_met(client, key):
    app, S = client
    option = options()[key]
    response = app.post("/analyze", json=body(S, option, baseline=True))
    assert response.status_code == 200, response.text
    assert response.json()["observation"]["sensor_used"] == option["sensor"]


@pytest.mark.parametrize("key", ["radar_threshold", "optical_threshold", "radar_change"])
def test_needs_baseline_matches_the_server(client, key):
    app, S = client
    option = options()[key]
    response = app.post("/analyze", json=body(S, option, baseline=False))
    assert (response.status_code == 400) == option["needs_baseline"], response.text


@pytest.mark.parametrize("key", ["radar_threshold", "optical_threshold", "radar_change"])
def test_supports_latest_matches_the_server(client, key):
    app, S = client
    option = options()[key]
    request = body(S, option, baseline=False, latest=True, post_start=None, post_end=None)
    response = app.post("/analyze", json=request)
    assert (response.status_code == 200) == option["supports_latest"], response.text
    if not option["supports_latest"]:
        assert response.status_code == 400
        assert "Latest-pass mode" in response.json()["detail"]


@pytest.mark.parametrize("limit", [0, 101])
def test_an_impossible_cloud_limit_is_refused_in_words(client, limit):
    app, S = client
    response = app.post("/analyze", json=body(S, options()["optical_threshold"],
                                              baseline=False, cloud_limit=limit))
    assert response.status_code == 400
    assert "cloud_limit must be a whole percentage" in response.json()["detail"]


def test_a_series_refuses_a_mixed_or_missing_sensor(client):
    app, S = client
    response = app.post("/analyze/series", json={"region": "testland", "start": "2018-08-01",
                                                 "end": "2018-08-31", "sensor": None})
    assert response.status_code in (400, 422)
