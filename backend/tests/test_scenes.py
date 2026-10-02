"""
B5: exactly which satellite images a result came from.

The scene list is read in the same Earth Engine call as the "images taken"
days, so the two cannot disagree; post-event and baseline scenes are kept
apart; the list is capped but the total never is; and a line of Earth
Engine code loads the same scenes again.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

pytest.importorskip("ee")

from core import scenes as SC  # noqa: E402
from fake_ee import FakeCollection, FakeFloat  # noqa: E402


def ms(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


def s1(index, when, orbit=63, direction="DESCENDING", platform="A"):
    return {"system:index": index, "system:time_start": ms(when),
            "relativeOrbitNumber_start": orbit, "orbitProperties_pass": direction,
            "platform_number": platform}


POST_SCENES = [
    s1("S1B_IW_GRDH_1SDV_20180813T005213_B", "2018-08-13T00:52:13", platform="B"),
    s1("S1A_IW_GRDH_1SDV_20180801T005213_A1", "2018-08-01T00:52:13"),
    s1("S1A_IW_GRDH_1SDV_20180801T005238_A2", "2018-08-01T00:52:38"),
]
PRE_SCENES = [
    s1("S1A_IW_GRDH_1SDV_20180508T005210_P", "2018-05-08T00:52:10"),
    s1("S1B_IW_GRDH_1SDV_20180520T005211_Q", "2018-05-20T00:52:11", platform="B"),
]


@pytest.fixture
def local_calls(monkeypatch):
    monkeypatch.setattr(SC, "_one_call",
                        lambda parts: {k: v.getInfo() for k, v in parts.items()})


# ------------------------------------------------------------- pure helpers

def test_rows_are_oldest_first_with_readable_platform_and_pass():
    rows = SC.rows_from("sentinel-1", {
        "id": ["b", "a"], "time": [ms("2018-08-13T00:52:13"), ms("2018-08-01T00:52:13")],
        "relative_orbit": [63, 63], "pass": ["DESCENDING", "ASCENDING"],
        "platform": ["B", "A"]})
    assert [r["id"] for r in rows] == ["a", "b"]
    assert rows[0] == {"id": "a", "acquired": "2018-08-01T00:52:13Z", "date": "2018-08-01",
                       "platform": "Sentinel-1A", "relative_orbit": 63, "pass": "ascending"}


def test_a_short_property_list_pads_rather_than_shifts():
    """Pinning one scene's orbit on another would be worse than a blank."""
    rows = SC.rows_from("sentinel-1", {"id": ["a", "b"], "time": [1, 2],
                                       "relative_orbit": [63]})
    assert rows[1]["relative_orbit"] is None


def test_optical_rows_carry_cloud_and_tile():
    rows = SC.rows_from("sentinel-2", {"id": ["x"], "time": [ms("2018-08-15T05:10:00")],
                                       "cloud_pct": [12.345], "platform": ["Sentinel-2A"],
                                       "tile": ["43PFL"]})
    assert rows[0]["cloud_pct"] == 12.3
    assert rows[0]["tile"] == "43PFL"
    assert rows[0]["platform"] == "Sentinel-2A"
    assert "pass" not in rows[0]


def test_the_snippet_loads_exactly_the_listed_scenes():
    code = SC.reproduce_snippet("COPERNICUS/S1_GRD", ["a", "b"])
    assert 'ee.ImageCollection("COPERNICUS/S1_GRD")' in code
    assert 'ee.Filter.inList(\n    "system:index"' in code
    assert '"a",' in code and '"b",' in code
    compile(code, "<snippet>", "exec")          # valid Python
    assert "Only the first" not in code


def test_a_capped_list_says_its_snippet_is_not_the_whole_set():
    code = SC.reproduce_snippet("COPERNICUS/S1_GRD", ["a"], truncated=True,
                                window=("2018-08-01", "2018-08-31"))
    assert "Only the first 1 scenes are listed" in code
    assert "2018-08-01 to 2018-08-31" in code
    compile(code, "<snippet>", "exec")


def test_no_scenes_no_snippet():
    assert SC.reproduce_snippet("COPERNICUS/S1_GRD", []) is None


# ------------------------------------------------------------- one EE call

def test_describe_lists_every_scene_and_its_days(local_calls):
    block = SC.describe(FakeCollection(3, POST_SCENES), "sentinel-1",
                        ("2018-08-01", "2018-08-31"))
    assert block["total"] == 3 and block["listed"] == 3 and not block["truncated"]
    assert [s["date"] for s in block["scenes"]] == ["2018-08-01", "2018-08-01", "2018-08-13"]
    assert block["days"] == ["2018-08-01", "2018-08-13"]
    assert block["passes"] == ["descending"]
    assert {s["platform"] for s in block["scenes"]} == {"Sentinel-1A", "Sentinel-1B"}
    assert block["collection"] == "COPERNICUS/S1_GRD"
    assert block["window"] == ("2018-08-01", "2018-08-31")


def test_describe_is_one_round_trip(monkeypatch):
    calls = []

    def one(parts):
        calls.append(sorted(parts))
        return {k: v.getInfo() for k, v in parts.items()}

    monkeypatch.setattr(SC, "_one_call", one)
    SC.describe(FakeCollection(3, POST_SCENES), "sentinel-1")
    assert len(calls) == 1
    assert "total" in calls[0] and "all_times" in calls[0]


def test_a_long_window_is_capped_but_the_total_and_days_are_not(local_calls, monkeypatch):
    many = [s1(f"S{i:03d}", f"2018-08-{1 + i % 28:02d}T00:52:{i % 60:02d}") for i in range(70)]
    block = SC.describe(FakeCollection(70, many), "sentinel-1", cap=50)
    assert block["total"] == 70
    assert block["listed"] == 50 and len(block["scenes"]) == 50
    assert block["truncated"] is True
    assert len(block["days"]) == 28, "days come from every scene, not the listed 50"
    assert "Only the first 50" in block["reproduce"]


def test_a_failed_list_is_an_error_entry_not_a_failed_result():
    class Broken:
        def limit(self, *a):
            raise RuntimeError("quota")

    block = SC.describe_safely(Broken(), "sentinel-1", ("a", "b"))
    assert "quota" in block["error"]


# ------------------------------------------------------- the real SAR detector

def test_sar_images_taken_days_come_from_the_scene_list(local_calls, monkeypatch):
    """detect_water runs for real over a fake collection: the days it reports
    are the scene list's, so "images taken" and "scenes used" agree."""
    from detection import sar
    composite = FakeFloat(np.full((4, 4), -25.0))
    monkeypatch.setattr(sar, "get_collection",
                        lambda *a, **k: FakeCollection(3, POST_SCENES, image=composite))
    monkeypatch.setattr(sar, "speckle_filter", lambda image, radius=50: image)
    monkeypatch.setattr(sar, "fuse", lambda image, polarisations=None: image)

    _, _, info = sar.detect_water(None, "2018-08-01", "2018-08-31", scale=200)
    assert info["scenes"]["total"] == 3
    assert info["acquisition_days"] == info["scenes"]["days"] == ["2018-08-01", "2018-08-13"]


# ------------------------------------------------------------ the flood result

def scene_detector(S):
    """The scenario's SAR detector, plus the scene list for its window."""
    def detect(region, start_date, end_date, **kwargs):
        mask, composite, info = S.fake_sar_detect(region, start_date, end_date, **kwargs)
        rows = POST_SCENES if start_date == S.POST[0] else PRE_SCENES
        block = SC.describe(FakeCollection(len(rows), rows), "sentinel-1",
                            (start_date, end_date))
        info.update(scenes=block, acquisition_days=block["days"])
        return mask, composite, info
    return detect


def test_post_and_baseline_scenes_are_kept_apart(monkeypatch):
    import flood_scenario as S
    from detection import sar
    from pipeline import analysis

    region = S.install(monkeypatch)
    monkeypatch.setattr(sar, "detect_water", scene_detector(S))
    result = analysis.analyse_flood(region, dict(S.META), *S.POST, *S.PRE,
                                    force_sensor="sentinel-1")

    post, baseline = result["scenes"]["post"], result["scenes"]["baseline"]
    assert [s["id"] for s in post["scenes"]][0].endswith("_A1")
    assert {s["date"] for s in baseline["scenes"]} == {"2018-05-08", "2018-05-20"}
    assert not set(s["id"] for s in post["scenes"]) & set(s["id"] for s in baseline["scenes"])
    # "Images taken" and "scenes used" describe the same images.
    assert result["acquisition"]["days"] == post["days"]
    # Provenance, not evidence: no evidence record quotes a scene.
    assert not any("scene" in e["quantity"] for e in result["evidence"]
                   if e["quantity"] != "scene_count")


def test_change_detection_lists_the_baseline_it_compared_against(monkeypatch):
    import flood_scenario as S
    from detection import sar
    from pipeline import analysis

    region = S.install(monkeypatch)
    monkeypatch.setattr(sar, "detect_water", scene_detector(S))
    monkeypatch.setattr(sar, "get_collection",
                        lambda *a, **k: FakeCollection(2, PRE_SCENES))
    monkeypatch.setattr(sar, "baseline_composite",
                        lambda *a, **k: FakeFloat(np.full(S.SHAPE, -12.0)))
    monkeypatch.setattr(sar, "change_mask", lambda post, pre, **k: post.mask())
    result = analysis.analyse_flood(region, dict(S.META), *S.POST, *S.PRE,
                                    force_sensor="sentinel-1", method="change")

    baseline = result["scenes"]["baseline"]
    assert baseline["total"] == 2
    assert baseline["window"] == S.PRE


def test_single_window_results_have_no_baseline_list(monkeypatch):
    import flood_scenario as S
    from detection import sar
    from pipeline import analysis

    region = S.install(monkeypatch)
    monkeypatch.setattr(sar, "detect_water", scene_detector(S))
    result = analysis.analyse_flood(region, dict(S.META), *S.POST,
                                    force_sensor="sentinel-1")
    assert result["scenes"]["baseline"] is None
    assert result["scenes"]["post"]["total"] == 3


# ------------------------------------------------------------------- the PDF

def result_with_scenes(total=3, rows=None):
    post = SC.summary("sentinel-1", SC.rows_from("sentinel-1", {
        "id": [r["system:index"] for r in (rows or POST_SCENES)],
        "time": [r["system:time_start"] for r in (rows or POST_SCENES)],
        "relative_orbit": [63] * len(rows or POST_SCENES),
        "pass": ["DESCENDING"] * len(rows or POST_SCENES),
        "platform": [r["platform_number"] for r in (rows or POST_SCENES)],
    }), total, [r["system:time_start"] for r in (rows or POST_SCENES)],
        ("2018-08-01", "2018-08-31"))
    return {"request_id": "abc123", "region": {"name": "Testland"},
            "period": {"post": {"start": "2018-08-01", "end": "2018-08-31"}},
            "scenes": {"post": post, "baseline": None}}


def test_the_pdf_outline_lists_each_scene():
    from pipeline import export_pdf
    view = export_pdf.outline(result_with_scenes())["scenes"]
    assert len(view) == 1
    assert view[0]["title"] == "After the event, 2018-08-01 to 2018-08-31"
    assert view[0]["rows"][0]["acquired"] == "2018-08-01 00:52:13 UTC"
    assert view[0]["rows"][0]["detail"] == "orbit 63, descending"
    assert view[0]["note"] is None


def test_the_pdf_says_how_many_scenes_it_left_out():
    from pipeline import export_pdf
    many = [s1(f"S{i:03d}", f"2018-08-{1 + i % 28:02d}T00:52:00") for i in range(40)]
    view = export_pdf.outline(result_with_scenes(total=40, rows=many))["scenes"]
    assert len(view[0]["rows"]) == export_pdf.PDF_SCENE_LIMIT
    assert view[0]["note"].startswith(f"{export_pdf.PDF_SCENE_LIMIT} of 40 scenes shown")


def test_a_result_without_scenes_has_no_scene_section():
    from pipeline import export_pdf
    assert export_pdf.outline({"request_id": "x"})["scenes"] is None


def test_the_rendered_pdf_carries_the_scene_ids():
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    from io import BytesIO
    from pipeline import export_pdf

    pdf = export_pdf.render(result_with_scenes())
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(BytesIO(pdf)).pages)
    assert "Satellite scenes used" in text
    assert "S1A_IW_GRDH_1SDV_20180801T005213" in text.replace("\n", "")
    assert "Sentinel-1B" in text
