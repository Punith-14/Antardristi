"""
C2: the terrain check in the flood path - gated on notebook 09's measurement.

With no measured rule (sar.TERRAIN_RULE None) nothing changes: the golden
test in test_optical_flood.py holds that. With a rule, dark pixels on
implausible terrain leave the flood extent, their area is REPORTED as its own
evidence record, the baseline gets the same check, the accuracy quoted is the
rule's own, and the check can be switched off.

The scenario (flood_scenario.py): 64 km2 of flood at rows 2-9, cols 2-9 and
16 km2 of pre-event water at rows 2-3. Here a "hill" covers rows 2-5, cols
0-11: 32 km2 of the flood, all of the pre-event water, and 8 km2 of ground
outside the flood that was never dark.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
EVALUATION = BACKEND.parent / "evaluation"
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

pytest.importorskip("ee")

RULE = {
    "hand_source": "MERIT/Hydro/v1_0_1",
    "max_hand_m": 15.0,
    "max_slope_deg": 20.0,
    "validation": {"dataset": "Sen1Floods11 HandLabeled test split (88 chips) at 200 m "
                              "with the terrain check, thresholds chosen on the train split",
                   "iou": 0.65, "precision": 0.86, "recall": 0.72},
    "india": {"event": "Assam, August 2016", "chips": 28, "iou": 0.74,
              "precision": 0.91, "recall": 0.79, "ci95": [0.58, 0.84]},
}


@pytest.fixture
def scenario(monkeypatch):
    import flood_scenario as S
    from detection import sar
    from fake_ee import FakeImage, grid

    region = S.install(monkeypatch)
    hill = grid(S.SHAPE, (2, 0, 6, 12))
    monkeypatch.setattr(sar, "TERRAIN_RULE", RULE)
    monkeypatch.setattr(sar, "terrain_implausible",
                        lambda region, rule: FakeImage(hill, name="terrain_implausible"))
    return S, region


def run(scenario, baseline=True, **kwargs):
    from pipeline import analysis
    S, region = scenario
    return analysis.analyse_flood(
        region, dict(S.META), *S.POST,
        *(S.PRE if baseline else (None, None)),
        force_sensor=kwargs.pop("force_sensor", "sentinel-1"), scale=100, **kwargs)


def ev(result):
    return {e["quantity"]: e for e in result["evidence"]}


def test_hill_water_leaves_the_extent_and_is_reported(scenario):
    result = run(scenario, baseline=False)
    e = ev(result)
    assert e["flood_extent"]["value"] == 32.0
    assert e["water_excluded_by_terrain"]["value"] == 32.0
    assert "more than 15 m above the nearest drainage" in e["water_excluded_by_terrain"]["method"]
    assert "slopes steeper than 20 degrees" in e["water_excluded_by_terrain"]["method"]
    assert "not counted" in e["flood_extent"]["method"]
    assert result["terrain"]["applied"] is True
    assert result["terrain"]["excluded_km2"] == 32.0


def test_the_accuracy_quoted_is_the_rule_measured_with_the_check(scenario):
    result = run(scenario, baseline=False)
    notes = " ".join(result["unobserved"]["notes"])
    assert ev(result)["flood_extent"]["confidence"] == RULE["validation"]["precision"]
    assert f"IoU {RULE['validation']['iou']}" in notes
    assert "held-out Indian chips (28 chips" in notes and "IoU 0.74" in notes


def test_provenance_names_the_terrain_data_and_its_limits(scenario):
    from detection import sar
    result = run(scenario, baseline=False)
    ids = [d["id"] for d in result["provenance"]["datasets"]]
    assert "MERIT/Hydro/v1_0_1" in ids and sar.COPERNICUS_DEM in ids
    assert sar.TERRAIN_CAVEAT in result["provenance"]["known_confusions"]


def test_the_baseline_gets_the_same_check(scenario):
    """Pre-event water sits on the hill. Without the check on the baseline too,
    net new water would be 32 - 16 = 16: a hill counted in one window only."""
    e = ev(run(scenario))
    assert e["baseline_water_extent"]["value"] == 0.0
    assert e["net_new_water"]["value"] == 32.0
    assert "same terrain check" in e["baseline_water_extent"]["method"]


def test_switched_off_gives_the_old_map_and_says_so(scenario):
    result = run(scenario, baseline=False, terrain_check=False)
    e = ev(result)
    assert e["flood_extent"]["value"] == 64.0
    assert "water_excluded_by_terrain" not in e
    assert result["terrain"] == {**result["terrain"], "applied": False,
                                 "reason": "switched off in the request"}
    from detection import sar
    assert ev(result)["flood_extent"].get("confidence") != RULE["validation"]["precision"]


def test_optical_is_not_checked_and_says_why(scenario):
    result = run(scenario, baseline=False, force_sensor="sentinel-2")
    assert result["terrain"]["applied"] is False
    assert result["terrain"]["reason"] == "measured for radar only"
    assert "water_excluded_by_terrain" not in ev(result)


def test_change_detection_is_not_checked_and_says_why(scenario, monkeypatch):
    from detection import sar
    from fake_ee import FakeFloat
    S, _ = scenario
    monkeypatch.setattr(sar, "baseline_composite",
                        lambda *a, **k: FakeFloat(np.full(S.SHAPE, -12.0)))
    monkeypatch.setattr(sar, "change_mask", lambda post, pre, **k: post.mask())
    result = run(scenario, method="change")
    assert result["terrain"]["applied"] is False
    assert "change detection" in result["terrain"]["reason"]


def test_without_a_rule_nothing_is_said_or_changed(scenario, monkeypatch):
    from detection import sar
    monkeypatch.setattr(sar, "TERRAIN_RULE", None)
    result = run(scenario, baseline=False)
    assert result["terrain"] is None
    assert ev(result)["flood_extent"]["value"] == 64.0


def test_the_excluded_ground_gets_its_own_map_layer(scenario, monkeypatch):
    from pipeline import analysis
    result = run(scenario, baseline=False)

    class Stub:
        def __init__(self, image):
            self.image = image

        def getMapId(self, _):
            return {"tile_fetcher": type("T", (), {"url_format": f"tiles/{self.image.name}"})()}

    monkeypatch.setattr(analysis.ee, "Image", Stub)
    urls = analysis.tile_urls(result)
    assert "terrain_excluded_tiles" in urls
    excluded = result["_internal"]["terrain_excluded"]
    assert int((excluded.data & excluded.valid).sum()) == 32


# ------------------------------------------------------------ the report

def test_the_report_names_the_excluded_ground_and_still_verifies(scenario):
    from pipeline.report import build_report
    result = run(scenario, baseline=False)
    report, verification = build_report(result, prefer_llm=False)
    assert "was not counted" in report["text"] and "32.0 km2" in report["text"]
    assert verification["passed"], verification.get("unsupported_claims")
    assert verification["caveats"]["completeness"] == 1.0


# ------------------------------------------------------ the request and cache

def test_cache_keys_move_only_when_a_rule_exists(monkeypatch):
    pytest.importorskip("fastapi")
    import main
    from detection import sar

    request = main.FloodRequest(region="kerala", post_start="2018-08-01", post_end="2018-08-31")
    monkeypatch.setattr(sar, "TERRAIN_RULE", None)
    plain = request.cache_key()
    assert "terrain_rule" not in plain and "terrain_check" not in plain

    monkeypatch.setattr(sar, "TERRAIN_RULE", RULE)
    with_rule = request.cache_key()
    assert with_rule["terrain_rule"] == sar.terrain_version(RULE)
    off = main.FloodRequest(region="kerala", post_start="2018-08-01", post_end="2018-08-31",
                            terrain_check=False).cache_key()
    assert "terrain_rule" not in off and off["terrain_check"] is False
    assert len({json.dumps(k, sort_keys=True) for k in (plain, with_rule, off)}) == 3


def test_the_catalogue_reports_the_rule_or_none(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import main
    from detection import sar

    monkeypatch.setattr(sar, "TERRAIN_RULE", None)
    assert TestClient(main.app).get("/analyses").json()["flood"]["terrain"] is None
    monkeypatch.setattr(sar, "TERRAIN_RULE", RULE)
    terrain = TestClient(main.app).get("/analyses").json()["flood"]["terrain"]
    assert terrain["max_hand_m"] == 15.0 and "drainage" in terrain["text"]


def test_a_geotiff_of_a_pre_terrain_result_is_not_rebuilt_under_the_check(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import flood_scenario as S
    import main
    from core import cache
    from detection import sar

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    region = S.install(monkeypatch)
    monkeypatch.setattr(main, "resolve_area_or_fail", lambda p: (region, dict(S.META)))
    monkeypatch.setattr(sar, "TERRAIN_RULE", None)
    client = TestClient(main.app)
    rid = client.post("/analyze", json={"region": "testland", "post_start": S.POST[0],
                                        "post_end": S.POST[1], "use_llm": False}).json()["request_id"]

    monkeypatch.setattr(sar, "TERRAIN_RULE", RULE)          # notebook 09 shipped
    response = client.get(f"/analyze/{rid}/export/flood.zip")
    assert response.status_code == 409
    assert "terrain check has changed" in response.json()["detail"]


def test_the_geotiff_codes_excluded_ground_as_3():
    from fake_ee import FakeImage
    from pipeline import export_gis as G
    import test_gis_export as TG

    shape = (1, 5)
    valid = np.array([[1, 1, 1, 1, 0]], bool)
    flood = np.array([[1, 0, 0, 0, 0]], bool)
    permanent = np.array([[0, 1, 0, 0, 0]], bool)
    excluded = np.array([[0, 0, 1, 0, 1]], bool)
    out = G.coded_image(FakeImage(flood, valid), FakeImage(valid), FakeImage(permanent, valid),
                        lambda v: TG.Codes(np.full(shape, v)),
                        terrain_excluded=FakeImage(excluded, valid)).data
    assert out.tolist() == [[1, 2, 3, 0, 255]], "excluded but unobserved stays 255"


def test_flood_layers_carries_the_excluded_mask(scenario):
    from pipeline import analysis
    S, region = scenario
    layers = analysis.flood_layers(region, *S.POST, sensor="sentinel-1", scale=100)
    assert layers["flood"].area_km2() == 32.0
    assert layers["terrain_excluded"].area_km2() == 32.0
    off = analysis.flood_layers(region, *S.POST, sensor="sentinel-1", scale=100,
                                terrain_check=False)
    assert off["flood"].area_km2() == 64.0 and off["terrain_excluded"] is None


# ------------------------------------------------- tied to the measurement

def test_a_shipped_rule_is_notebook_09s_and_passed_its_decision():
    from detection import sar
    if sar.TERRAIN_RULE is None:
        pytest.skip("no terrain rule shipped - notebook 09 not run, or it did not pass")
    results = EVALUATION / "terrain_results.json"
    assert results.exists(), "sar.TERRAIN_RULE is set but terrain_results.json is missing"
    measured = json.loads(results.read_text(encoding="utf-8"))
    assert measured["decision"]["ship"] is True, measured["decision"]["reasons"]
    assert sar.TERRAIN_RULE == measured["terrain_rule_for_backend"]


class Call:
    """Records Earth Engine calls on a chain, by name and arguments."""

    def __init__(self, log, name="root"):
        self.log, self.name = log, name

    def __getattr__(self, attr):
        def call(*args, **kwargs):
            self.log.append((attr, args))
            return Call(self.log, attr)
        return call


@pytest.mark.parametrize("rule,expect", [
    ({**RULE}, {"hnd": True, "slope": True}),
    ({**RULE, "max_slope_deg": None}, {"hnd": True, "slope": False}),
    ({**RULE, "max_hand_m": None}, {"hnd": False, "slope": True}),
])
def test_the_earth_engine_mask_uses_only_the_tests_the_rule_sets(monkeypatch, rule, expect):
    from detection import sar
    log = []
    fake = type("EE", (), {})()
    fake.Image = lambda *a: (log.append(("Image", a)), Call(log, "Image"))[1]
    fake.Image.constant = lambda v: (log.append(("constant", (v,))), Call(log, "constant"))[1]
    fake.ImageCollection = lambda *a: (log.append(("ImageCollection", a)), Call(log, "IC"))[1]
    fake.Terrain = type("T", (), {"slope": staticmethod(
        lambda dem: (log.append(("slope", ())), Call(log, "slope"))[1])})
    monkeypatch.setattr(sar, "ee", fake)

    sar.terrain_implausible("region", rule)
    calls = [name for name, _ in log]
    assert (("select", ("hnd",)) in log) == expect["hnd"]
    assert ("slope" in calls) == expect["slope"]
    # Missing terrain is plausible: HAND unmasks to -1, slope to 0, before ">".
    if expect["hnd"]:
        assert ("unmask", (-1,)) in log and ("gt", (rule["max_hand_m"],)) in log
    if expect["slope"]:
        assert ("unmask", (0,)) in log and ("gt", (rule["max_slope_deg"],)) in log
    assert calls[-1] == "rename" and ("clip", ("region",)) in log


def test_a_measured_do_not_ship_keeps_the_check_off():
    """Notebook 09 said DO NOT SHIP (test gain's range included zero; Indian
    recall fell 0.024). Filling TERRAIN_RULE anyway would contradict the
    measurement on file."""
    from detection import sar
    results = EVALUATION / "terrain_results.json"
    if not results.exists():
        pytest.skip("notebook 09 not run")
    measured = json.loads(results.read_text(encoding="utf-8"))
    if not measured["decision"]["ship"]:
        assert sar.TERRAIN_RULE is None
        assert measured["terrain_rule_for_backend"] is None


def test_the_negative_result_quoted_in_sar_py_is_the_measured_one():
    results = EVALUATION / "terrain_results.json"
    if not results.exists():
        pytest.skip("notebook 09 not run")
    measured = json.loads(results.read_text(encoding="utf-8"))
    if measured["decision"]["ship"]:
        pytest.skip("shipped - the comment no longer applies")
    import re
    # The comment wraps; read it as one line.
    text = re.sub(r"\s*\n#\s*", " ", (BACKEND / "detection" / "sar.py").read_text(encoding="utf-8"))
    test200 = measured["results"]["test 200 m"]
    assert f"{test200['before']['iou']:.3f} -> {test200['after']['iou']:.3f}" in text
    low, high = measured["gain_ci95"]["test 200 m"]
    assert f"({low:.3f} to +{high:.3f})" in text
    india = measured["india_200m"]
    assert f"{india['before']['recall']:.3f} -> {india['after']['recall']:.3f}" in text
