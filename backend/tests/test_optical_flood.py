"""
The optical flood path, and the property it exists for: a Sentinel-1 result
and a Sentinel-2 result for the same place and window differ only by sensor.

Before this, /analyze with sensor="sentinel-2" raised NotImplementedError,
and the only optical water number came from /analyze/surface - a different
quantity (water_area, permanent water included), a different baseline
(net_change), and an accuracy figure scored against a different reference
(WorldCover permanent water). A SAR-versus-optical table built from those
would mostly have measured the difference between two questions.

Earth Engine is replaced by numpy grids (fake_ee.py, flood_scenario.py), so
the whole flood path runs here with every expected number worked out by hand.
"""

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

pytest.importorskip("ee")

import flood_scenario as S  # noqa: E402
from detection import optical, surface  # noqa: E402
from pipeline import analysis  # noqa: E402

GOLDEN = TESTS / "golden" / "sar_flood_before_optical.json"


def ev(result):
    return {item["quantity"]: item for item in result["evidence"]}


# ------------------------------------------------ the refactor changed nothing

@pytest.mark.parametrize("baseline", [True, False])
def test_the_sar_path_is_unchanged_by_the_refactor(monkeypatch, baseline):
    """Captured from the SAR code before it was restructured to share its
    arithmetic with the optical path. The user has confirmed live SAR numbers
    (IoU 0.489, 620.3 km2, 89 zones); none of them may move."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    now = json.loads(json.dumps(
        S.comparable(S.run(monkeypatch, "sentinel-1", baseline=baseline)),
        sort_keys=True,
    ))

    # The one deliberate addition since the golden was captured: the relative
    # orbit, recorded so a time series can tell which months share a viewing
    # geometry. Checked explicitly rather than by regenerating the golden, so
    # this test still proves nothing ELSE moved.
    assert now["observation"].pop("relative_orbit") == 63

    assert now == golden["with_baseline" if baseline else "single"]


# ---------------------------------------------------- the numbers, by hand

def test_optical_measures_the_flood_the_scenario_contains(monkeypatch):
    result = S.run(monkeypatch, "sentinel-2")
    e = ev(result)

    assert e["flood_extent"]["value"] == 64.0
    assert e["permanent_water_area"]["value"] == 9.0
    # 400 km2 minus the 48 km2 post-window cloud.
    assert result["observation"]["observable_area_km2"] == 352.0
    assert e["flood_extent_fraction"]["value"] == round(64 / 352 * 100, 2)


def test_cloud_is_unobserved_not_dry(monkeypatch):
    """The Kerala 2019 failure: counting cloud as dry ground reported less
    water during a major flood. Cloud is subtracted from what was seen."""
    result = S.run(monkeypatch, "sentinel-2")
    assert result["unobserved"]["masked_area_km2"] == 48.0
    assert ev(result)["flood_extent"]["spatial_support_km2"] == 352.0


def test_the_baseline_is_measured_only_where_both_windows_were_clear(monkeypatch):
    """Cloud moves between windows. Without restricting to common ground, the
    cloud moving would be reported as water appearing or vanishing."""
    e = ev(S.run(monkeypatch, "sentinel-2"))

    # 400 - 48 (post cloud) - 32 (pre cloud), the two clouds not overlapping.
    assert e["baseline_water_extent"]["spatial_support_km2"] == 320.0
    assert e["baseline_water_extent"]["value"] == 16.0
    assert e["flood_extent_in_common_area"]["value"] == 64.0


def test_net_new_water_adds_up_from_the_records_it_cites(monkeypatch):
    """Any reader can check E(net) = E(post) - E(pre) from the cited ids. If
    it were derived from the full extent while the baseline covered less
    ground, the check would fail - correctly."""
    result = S.run(monkeypatch, "sentinel-2")
    by_id = {item["id"]: item for item in result["evidence"]}
    net = ev(result)["net_new_water"]

    post_id, pre_id = net["derived_from"]
    assert by_id[post_id]["quantity"] == "flood_extent_in_common_area"
    assert by_id[pre_id]["quantity"] == "baseline_water_extent"
    assert net["value"] == round(by_id[post_id]["value"] - by_id[pre_id]["value"], 1)


def test_a_cloud_over_the_flood_is_the_case_optical_cannot_flag(monkeypatch):
    """Move the post-window cloud onto half the flood.

    The measured extent halves, and the masked area records the 32 km2 that
    was not seen - so the number is not wrong about what it covers. But
    coverage is still 92%, above the 90% at which the coverage warning fires,
    so nothing tells the reader that the part not seen was the flood.

    Region-level coverage cannot know where the cloud sat. This test pins that
    limitation down rather than pretending it away, because it is the
    strongest argument in the results chapter for radar in the monsoon: SAR
    would report all 64 km2 here.
    """
    cloudy = S.grid(S.SHAPE, (2, 2, 10, 6), value=False)
    monkeypatch.setattr(S, "S2_POST_VALID", cloudy)
    optical_result = S.run(monkeypatch, "sentinel-2", baseline=False)
    radar_result = S.run(monkeypatch, "sentinel-1", baseline=False)

    assert ev(optical_result)["flood_extent"]["value"] == 32.0
    assert ev(radar_result)["flood_extent"]["value"] == 64.0

    assert optical_result["observation"]["observable_area_km2"] == 368.0
    assert optical_result["unobserved"]["masked_area_km2"] == 32.0
    assert not any("could be observed" in n for n in optical_result["unobserved"]["notes"])


# ----------------------------------------------------- like for like

def test_both_sensors_emit_the_same_quantities(monkeypatch):
    """The property the whole feature exists for. The optical path adds one
    record, flood_extent_in_common_area, because its baseline covers less
    ground than its extent; everything SAR emits, optical emits too."""
    sar_q = set(ev(S.run(monkeypatch, "sentinel-1")))
    opt_q = set(ev(S.run(monkeypatch, "sentinel-2")))

    assert sar_q <= opt_q, f"optical is missing {sar_q - opt_q}"
    assert opt_q - sar_q == {"flood_extent_in_common_area"}


def test_both_sensors_report_the_same_flood_when_both_can_see_it(monkeypatch):
    """Same ground truth, both sensors clear over the flood: the answers match.
    Any difference in a real comparison is then the sensors, not the code."""
    sar_e = ev(S.run(monkeypatch, "sentinel-1"))
    opt_e = ev(S.run(monkeypatch, "sentinel-2"))

    for quantity in ("flood_extent", "permanent_water_area", "baseline_water_extent",
                     "net_new_water", "zone_count"):
        assert sar_e[quantity]["value"] == opt_e[quantity]["value"], quantity


def test_both_use_the_same_permanent_water_mask(monkeypatch):
    for sensor in ("sentinel-1", "sentinel-2"):
        method = ev(S.run(monkeypatch, sensor))["permanent_water_area"]["method"]
        assert analysis.GSW in method


def test_each_result_names_its_own_sensor_everywhere(monkeypatch):
    result = S.run(monkeypatch, "sentinel-2")
    assert result["observation"]["sensor_used"] == "sentinel-2"
    assert result["provenance"]["datasets"][0]["id"] == optical.S2_COLLECTION
    assert "Sentinel-2" in ev(result)["flood_extent"]["method"]
    assert "Sentinel-1" not in ev(result)["flood_extent"]["method"]


# -------------------------------------------- no borrowed accuracy

def test_the_optical_flood_rule_claims_no_confidence_it_has_not_earned(monkeypatch):
    """0.466 was measured for surface water against WorldCover permanent
    water. Attaching it to a flood extent would describe a different task."""
    monkeypatch.setattr(optical, "VALIDATION", None)
    e = ev(S.run(monkeypatch, "sentinel-2"))
    assert "confidence" not in e["flood_extent"]


def test_the_result_says_why_there_is_no_accuracy_figure(monkeypatch):
    monkeypatch.setattr(optical, "VALIDATION", None)
    notes = " ".join(S.run(monkeypatch, "sentinel-2")["unobserved"]["notes"])
    assert "not yet been scored against flood labels" in notes
    # Names the number it is disclaiming, so a reader who has seen 0.466
    # elsewhere knows it does not apply.
    assert str(surface.ANALYSES["water_extent"]["validation"]["iou"]) in notes


def test_the_disclaimer_reads_the_number_it_disclaims():
    """Written from surface.ANALYSES, not restated - or it drifts."""
    assert str(surface.ANALYSES["water_extent"]["validation"]["iou"]) in optical.NOT_VALIDATED_NOTE
    assert str(optical.THRESHOLD) in optical.NOT_VALIDATED_NOTE


def test_once_measured_the_validation_flows_through_like_sars(monkeypatch):
    """When notebook 05 fills VALIDATION, confidence and the accuracy note
    appear exactly as they do for SAR - no second code path to forget."""
    measured = {"dataset": "Sen1Floods11 HandLabeled (S2, 446 chips)",
                "iou": 0.5, "precision": 0.8, "recall": 0.6}
    monkeypatch.setattr(optical, "VALIDATION", measured)
    result = S.run(monkeypatch, "sentinel-2")

    assert ev(result)["flood_extent"]["confidence"] == 0.8
    notes = " ".join(result["unobserved"]["notes"])
    assert "IoU 0.5" in notes
    assert "not yet been scored" not in notes


RESULTS = BACKEND.parent / "evaluation" / "optical_flood_results.json"


def test_the_flood_threshold_is_not_the_borrowed_surface_one():
    """-0.15 was right for permanent water against WorldCover. On flood
    labels it scored precision 0.369 - it called most land water. The flood
    path has its own threshold because it was measured to need one."""
    assert optical.THRESHOLD != surface.ANALYSES["water_extent"]["threshold"]


def test_the_validation_is_the_notebooks_measurement_at_the_shipped_threshold():
    """Tied to the file notebook 05 wrote, not to a number copied by hand.
    If the threshold changes without re-running the notebook, or the numbers
    are edited, this fails."""
    if not RESULTS.exists():
        pytest.skip("evaluation/optical_flood_results.json not present")
    measured = json.loads(RESULTS.read_text(encoding="utf-8"))
    at_threshold = measured["sweep"].get(str(optical.THRESHOLD))
    assert at_threshold, f"notebook 05 did not score MNDWI > {optical.THRESHOLD}"

    for metric in ("iou", "precision", "recall"):
        assert optical.VALIDATION[metric] == round(at_threshold[metric], 3), metric
    assert str(measured["chips_scored"]) in optical.VALIDATION["dataset"]


def test_the_borrowed_threshold_really_was_worse():
    """The reason for the change, kept checkable."""
    if not RESULTS.exists():
        pytest.skip("evaluation/optical_flood_results.json not present")
    sweep = json.loads(RESULTS.read_text(encoding="utf-8"))["sweep"]
    borrowed = sweep[str(surface.ANALYSES["water_extent"]["threshold"])]
    assert borrowed["iou"] < sweep[str(optical.THRESHOLD)]["iou"]
    assert borrowed["precision"] < 0.5


def test_a_validated_optical_result_carries_its_score_and_why_it_flatters(monkeypatch):
    result = S.run(monkeypatch, "sentinel-2")
    notes = " ".join(result["unobserved"]["notes"])

    assert ev(result)["flood_extent"]["confidence"] == optical.VALIDATION["precision"]
    assert f"IoU {optical.VALIDATION['iou']}" in notes
    # The caveat, in full: labels, cloud, product level.
    assert "flatters optical" in notes
    assert "cloud-covered ground was never scored" in notes
    assert "Level-1C" in notes and "Level-2A" in notes
    assert "not yet been scored" not in notes


def test_the_caveat_is_one_note_not_three():
    """Every note must survive into the generated report. Too many and the
    model drops some, fails completeness, and falls back every time."""
    assert optical.VALIDATION_CAVEAT.count(". ") == 0


def test_an_unvalidated_result_is_not_drawn_as_confidently_as_a_validated_one(monkeypatch):
    monkeypatch.setattr(optical, "VALIDATION", None)
    optical_style = S.run(monkeypatch, "sentinel-2")["map"]["overlay_style"]
    sar_style = S.run(monkeypatch, "sentinel-1")["map"]["overlay_style"]
    assert optical_style != "solid"
    assert sar_style == "translucent"


# ------------------------------------------------------------ routing

def test_an_unknown_sensor_is_refused_by_name(monkeypatch):
    S.install(monkeypatch)
    with pytest.raises(ValueError, match="sentinel-1, sentinel-2"):
        analysis.analyse_flood(S.FakeRegion(S.SHAPE), dict(S.META), *S.POST,
                               force_sensor="landsat-8")


def test_auto_routing_falls_back_to_radar_when_no_optical_scene_is_clear(monkeypatch):
    """Scene metadata said optical was fine; the composite found nothing
    usable. Radar sees through cloud - use it and say that is why."""
    S.install(monkeypatch)

    def no_clear_scenes(*_, **__):
        raise surface.NoOpticalImagery(S.POST[0], S.POST[1], 5, 40)

    monkeypatch.setattr(optical, "detect_water", no_clear_scenes)
    monkeypatch.setattr(analysis, "choose_sensor",
                        lambda *a, **k: ("sentinel-2", "optical_conditions_acceptable", 0.2, 5))

    result = analysis.analyse_flood(S.FakeRegion(S.SHAPE), dict(S.META), *S.POST)
    assert result["observation"]["sensor_used"] == "sentinel-1"
    assert result["observation"]["sensor_reason"] == "no_cloud_free_optical_scenes"


def test_a_forced_optical_request_with_no_clear_scene_is_not_silently_swapped(monkeypatch):
    """If the caller asked for Sentinel-2, answering with Sentinel-1 would be
    answering a different question. It raises, and /analyze turns that into
    an honest no-data response."""
    S.install(monkeypatch)

    def no_clear_scenes(*_, **__):
        raise surface.NoOpticalImagery(S.POST[0], S.POST[1], 5, 40)

    monkeypatch.setattr(optical, "detect_water", no_clear_scenes)
    with pytest.raises(surface.NoOpticalImagery):
        analysis.analyse_flood(S.FakeRegion(S.SHAPE), dict(S.META), *S.POST,
                               force_sensor="sentinel-2")


def test_a_baseline_with_no_clear_scene_is_a_note_not_a_crash(monkeypatch):
    S.install(monkeypatch)
    real = S.fake_optical_detect

    def pre_is_cloudy(region, start_date, end_date, **kwargs):
        if start_date == S.PRE[0]:
            raise surface.NoOpticalImagery(start_date, end_date, 3, 40)
        return real(region, start_date, end_date, **kwargs)

    monkeypatch.setattr(optical, "detect_water", pre_is_cloudy)
    result = analysis.analyse_flood(
        S.FakeRegion(S.SHAPE), dict(S.META), *S.POST, *S.PRE, force_sensor="sentinel-2",
    )
    assert "net_new_water" not in ev(result)
    assert any("Baseline could not be computed" in n for n in result["unobserved"]["notes"])
    assert result["_internal"]["baseline_mask"] is None


# --------------------------------------------------------- the endpoint

MAIN = (BACKEND / "main.py").read_text(encoding="utf-8")


def test_analyze_no_longer_answers_optical_with_a_501():
    body = MAIN.split("def analyze(payload: FloodRequest):")[1].split("@app.")[0]
    assert "501" not in body
    assert "surface.NoOpticalImagery" in body


def test_an_unknown_sensor_is_a_400():
    body = MAIN.split("def analyze(payload: FloodRequest):")[1].split("@app.")[0]
    assert "except ValueError" in body
    assert "status_code=400" in body


def test_adding_cloud_limit_did_not_rekey_the_cache():
    """A new request field changes model_dump(), which is the cache key. Left
    unhandled, every stored analysis would get a new request_id and re-run
    against Earth Engine quota."""
    pytest.importorskip("fastapi")
    import main
    from core import cache

    old_request = {
        "region": "kerala", "bbox": None, "point": None, "radius_km": None,
        "polygon": None, "post_start": "2018-08-01", "post_end": "2018-08-31",
        "pre_start": None, "pre_end": None, "sensor": "sentinel-1", "scale": 100,
        "generate_report": True, "use_llm": True,
    }
    assert main.FloodRequest(**old_request).cache_key() == old_request
    assert (cache.key_for(main.FloodRequest(**old_request).cache_key())
            == cache.key_for(old_request))


def test_a_set_cloud_limit_does_change_the_key():
    """Two optical runs with different cloud filters are different analyses."""
    pytest.importorskip("fastapi")
    import main

    base = {"region": "kerala", "post_start": "2018-08-01", "post_end": "2018-08-31",
            "sensor": "sentinel-2"}
    assert (main.FloodRequest(**base).cache_key()
            != main.FloodRequest(**base, cloud_limit=20).cache_key())
