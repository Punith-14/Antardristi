"""
Change detection: darker than its own dry self, not just dark.

Built to reach the water notebook 04 showed the darkness rule cannot: 28.3%
of missed water brighter than -10 dB, a different population rather than a
mistuned threshold. It is UNVALIDATED - Sen1Floods11 has no pre-event radar -
so a large part of what is tested here is that it never claims otherwise:
no borrowed accuracy, an explicit opt-in, loud notes.

The flood path runs for real on numpy grids (fake_ee, flood_scenario), with a
scenario built so each branch of the rule has one patch of ground to find or
to refuse.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
EVALUATION = BACKEND.parent / "evaluation"
for path in (BACKEND, TESTS, EVALUATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import change as C  # noqa: E402  (evaluation/change.py)
from metrics import evaluate  # noqa: E402


# ============================================================ the numpy rule

def arrays(*values):
    return [np.array(v, dtype=np.float64) for v in values]


def test_dark_pixels_are_water_as_before():
    post, pre = arrays([-24.0], [-8.0])
    pred, _ = C.change_rule()(post, pre)
    assert pred.tolist() == [True]


def test_a_pixel_much_darker_than_its_dry_self_is_water():
    """-17 dB is not dark enough on its own. Seven dB below its dry value, it
    has changed - and that is the water the darkness rule misses."""
    post, pre = arrays([-17.0], [-10.0])
    assert C.darkness_rule()(post, pre)[0].tolist() == [False]
    assert C.change_rule()(post, pre)[0].tolist() == [True]


def test_a_big_drop_on_bright_ground_is_not_water():
    """A harvested field or a moving shadow can drop 5 dB and still sit at
    -11 dB, far too bright for open water. The ceiling refuses it."""
    post, pre = arrays([-11.0], [-6.0])
    assert C.change_rule()(post, pre)[0].tolist() == [False]


def test_a_small_drop_is_noise_not_water():
    post, pre = arrays([-16.0], [-14.0])
    assert C.change_rule()(post, pre)[0].tolist() == [False]


def test_a_pixel_with_no_history_falls_back_to_the_darkness_rule():
    """Missing baseline is not evidence against water, and dropping such
    pixels would score the change rule on fewer pixels than its rival."""
    post, pre = arrays([-24.0, -16.0], [np.nan, np.nan])
    pred, info = C.change_rule()(post, pre)
    assert pred.tolist() == [True, False]
    assert info["pixels_without_history"] == 2


def test_the_change_rule_never_finds_less_than_the_darkness_rule():
    """It is the darkness rule plus a branch. Any pixel the shipped rule
    calls water, this calls water - so its recall cannot go down."""
    rng = np.random.default_rng(1)
    post = rng.uniform(-30, 0, 5000)
    pre = rng.uniform(-30, 0, 5000)
    pre[rng.random(5000) < 0.1] = np.nan
    dark, _ = C.darkness_rule()(post, pre)
    change, _ = C.change_rule()(post, pre)
    assert not np.any(dark & ~change)


def test_missing_post_data_is_never_water():
    post, pre = arrays([np.nan], [-8.0])
    assert C.change_rule()(post, pre)[0].tolist() == [False]


def test_fused_is_the_mean_and_nan_where_a_band_is_missing():
    out = C.fused(np.array([-20.0, -10.0]), np.array([-26.0, np.nan]))
    assert out[0] == pytest.approx(-23.0)
    assert np.isnan(out[1])


def test_info_says_how_many_pixels_the_change_branch_added():
    post, pre = arrays([-24.0, -17.0, -17.0], [-8.0, -10.0, -15.0])
    _, info = C.change_rule()(post, pre)
    assert info["pixels_from_darkness"] == 1
    assert info["pixels_added_by_change"] == 1


# --------------------------------------------------------------- recovery

def test_recovery_is_binned_like_notebook_04():
    """The question change detection was built to answer is 'how much of the
    missed water does it recover, and of which kind?' - in the same bins
    notebook 04 used, so the two line up."""
    post = np.array([-18.0, -14.0, -12.0, -8.0, -24.0])
    truth = np.array([1, 1, 1, 1, 1])
    shipped = post < -20
    change = np.array([True, True, False, False, True])

    result = C.recovery(post, truth, shipped, change)
    assert result["marginal"] == {"missed": 1, "recovered": 1}     # -18
    assert result["moderate"] == {"missed": 2, "recovered": 1}     # -14, -12
    assert result["bright"] == {"missed": 1, "recovered": 0}       # -8


def test_the_price_is_counted_too():
    truth = np.array([0, 0, 1])
    shipped = np.array([False, False, False])
    change = np.array([True, False, True])
    assert C.added_false_positives(truth, shipped, change) == 1


def test_scoring_uses_the_same_metric_code_as_every_other_rule():
    post, pre = arrays([-24.0, -17.0, -8.0], [-8.0, -10.0, -8.0])
    truth = np.array([1, 1, 0])
    pred, _ = C.change_rule()(post, pre)
    assert evaluate(pred, truth)["iou"] == 1.0


# ===================================================== two implementations

def test_the_scored_rule_is_the_rule_that_ships():
    """evaluation/change.py and detection/sar.py are two implementations of
    one rule. Different constants and the notebook measures something nobody
    runs."""
    pytest.importorskip("ee")
    from detection import sar

    assert C.DARK_DB == sar.CHANGE_DARK_DB == sar.DEFAULT_DB
    assert C.DROP_DB == sar.CHANGE_DROP_DB
    assert C.CEILING_DB == sar.CHANGE_CEILING_DB


def test_the_backend_rule_matches_the_numpy_rule_pixel_for_pixel():
    """Run sar.change_mask - the Earth Engine code itself - on fake images
    and compare with the numpy mirror on the same values."""
    pytest.importorskip("ee")
    from detection import sar
    from fake_ee import FakeFloat

    rng = np.random.default_rng(7)
    post = rng.uniform(-30, 0, (40, 40))
    pre = rng.uniform(-30, 0, (40, 40))
    pre[rng.random((40, 40)) < 0.1] = np.nan

    backend = sar.change_mask(FakeFloat(post), FakeFloat(pre))
    numpy_pred, _ = C.change_rule()(post, pre)

    assert np.array_equal(backend.data & backend.valid, numpy_pred)


RESULTS = BACKEND.parent / "evaluation" / "change_results.json"


def test_the_change_validation_is_notebook_06s_measurement_at_the_shipped_settings():
    """Tied to the file the notebook wrote. Change the constants without
    re-running it, or edit the numbers, and this fails."""
    pytest.importorskip("ee")
    from detection import sar
    if not RESULTS.exists():
        pytest.skip("evaluation/change_results.json not present")
    measured = json.loads(RESULTS.read_text(encoding="utf-8"))
    cell = measured["sweep_iou"].get(f"{sar.CHANGE_DROP_DB}|{sar.CHANGE_CEILING_DB}")
    assert cell, "notebook 06 did not score the shipped settings"
    for metric in ("iou", "precision", "recall"):
        assert sar.CHANGE_VALIDATION[metric] == round(cell[metric], 3), metric
    assert str(measured["chips_scored"]) in sar.CHANGE_VALIDATION["dataset"]


def test_change_detection_only_earned_its_place_by_raising_iou():
    """The rule the notebook applies: a method replaces or joins the default
    only if IoU rises. The first guess did not, and is kept here as the
    reason it was replaced."""
    pytest.importorskip("ee")
    from detection import sar
    if not RESULTS.exists():
        pytest.skip("evaluation/change_results.json not present")
    measured = json.loads(RESULTS.read_text(encoding="utf-8"))
    darkness = next(v["micro"]["iou"] for k, v in measured["head_to_head"].items()
                    if k.startswith("darkness"))
    first_guess = measured["sweep_iou"]["-3.0|-15.0"]["iou"]
    shipped = measured["sweep_iou"][f"{sar.CHANGE_DROP_DB}|{sar.CHANGE_CEILING_DB}"]["iou"]
    assert first_guess < darkness < shipped


def test_change_detection_recovers_no_bright_water_and_says_why():
    """The ceiling excludes anything brighter than it, so water above -10 dB
    is unreachable by construction. Recorded rather than hidden."""
    pytest.importorskip("ee")
    from detection import sar
    assert sar.CHANGE_CEILING_DB < -10.0
    if RESULTS.exists():
        bright = json.loads(RESULTS.read_text(encoding="utf-8"))["recovery_by_bin"]["bright"]
        assert bright["recovered"] == 0


# ============================================================ fetch script

def test_events_are_named_by_everything_before_the_last_underscore():
    from scripts import fetch_pre_event as F
    assert F.event_of("Sri-Lanka_249079") == "sri-lanka"
    assert F.event_of("USA_1082482") == "usa"


def test_the_baseline_window_ends_before_the_flood_with_a_gap():
    from scripts import fetch_pre_event as F
    start, end = F.pre_window("2018-08-20", window_days=60, gap_days=15)
    assert end == "2018-08-05"
    assert start == "2018-06-06"


def test_metadata_keys_are_found_whatever_they_are_called():
    from scripts import fetch_pre_event as F
    geojson = {"features": [
        {"properties": {"location": "Sri Lanka", "s1_date": "2017-05-28T00:00:00"}},
        {"properties": {"location": "India", "s1_date": "2016-08-12"}},
    ]}
    dates = F.event_dates_from_metadata(geojson)
    assert dates["sri-lanka"].isoformat() == "2017-05-28"
    assert dates["india"].isoformat() == "2016-08-12"


def test_unrecognised_metadata_fails_loudly_with_the_keys_it_saw():
    from scripts import fetch_pre_event as F
    with pytest.raises(ValueError, match="keys present"):
        F.event_dates_from_metadata({"features": [{"properties": {"name": "x", "when": "y"}}]})


def test_the_grid_is_the_chips_exact_pixel_grid():
    """Pixel-by-pixel subtraction: a half-pixel offset would misplace every
    land/water edge in every chip."""
    from scripts import fetch_pre_event as F

    class Affine:
        a, b, c, d, e, f = 0.0001, 0.0, 75.5, 0.0, -0.0001, 10.2

    grid = F.ee_grid(Affine, 512, 512, "EPSG:4326")
    assert grid["dimensions"] == {"width": 512, "height": 512}
    assert grid["affineTransform"]["scaleY"] == -0.0001
    assert grid["affineTransform"]["translateX"] == 75.5
    assert grid["crsCode"] == "EPSG:4326"


def test_the_fetch_is_resumable():
    """446 chips at a few seconds each must survive being interrupted.
    Checked by behaviour in test_a_rerun_skips_finished_chips and
    test_a_half_written_file_is_never_mistaken_for_a_finished_one; this only
    confirms the manifest that records failures is still written."""
    source = (BACKEND / "scripts" / "fetch_pre_event.py").read_text(encoding="utf-8")
    assert "manifest.jsonl" in source


# ================================================== through the flood path

@pytest.fixture
def change_scenario(monkeypatch):
    """20 x 20 km. Post and pre backscatter in dB, one patch per branch:

        rows  2-9,  cols 2-9   open flood       post -24, pre -8   dark
        rows 10-13, cols 2-9   shallow flood    post -17, pre -10  darkened
        rows 15-17, cols 2-9   harvested field  post -11, pre -6   refused (ceiling)
        rows 15-17, cols 15-17 permanent water  post -25, pre -25  excluded (JRC)
        everything else        dry land         post  -8, pre -8
    """
    pytest.importorskip("ee")
    import flood_scenario as S
    from detection import sar
    from fake_ee import FakeFloat, FakeImage

    post = np.full(S.SHAPE, -8.0)
    pre = np.full(S.SHAPE, -8.0)
    post[2:10, 2:10], pre[2:10, 2:10] = -24.0, -8.0
    post[10:14, 2:10], pre[10:14, 2:10] = -17.0, -10.0
    post[15:18, 2:10], pre[15:18, 2:10] = -11.0, -6.0
    post[15:18, 15:18], pre[15:18, 15:18] = -25.0, -25.0

    region = S.install(monkeypatch)
    calls = {"baseline": 0}

    def detect(region, start_date, end_date, relative_orbit=None, scale=100,
               threshold_db=None, **_):
        values = post if start_date == S.POST[0] else pre
        composite = FakeFloat(values)
        threshold = sar.DEFAULT_DB if threshold_db is None else float(threshold_db)
        info = {
            "polarisation": "VV+VH", "scene_count": 3, "degraded": None,
            "threshold": threshold, "threshold_unit": "dB",
            "threshold_source": "fixed_validated" if threshold_db is None else "fixed",
            "threshold_raw": None,
            "validation": dict(sar.VALIDATION) if threshold_db is None else None,
        }
        return composite.lt(threshold), composite, info

    def baseline(region, start, end, relative_orbit=None, **_):
        calls["baseline"] += 1
        calls["baseline_orbit"] = relative_orbit
        return FakeFloat(pre)

    monkeypatch.setattr(sar, "detect_water", detect)
    monkeypatch.setattr(sar, "baseline_composite", baseline)
    return S, region, calls, FakeImage


def run(scenario, method):
    from pipeline import analysis
    S, region, calls, _ = scenario
    return analysis.analyse_flood(
        region, dict(S.META), *S.POST, *S.PRE, force_sensor="sentinel-1", method=method,
    )


def ev(result):
    return {item["quantity"]: item for item in result["evidence"]}


def test_threshold_finds_only_the_open_flood(change_scenario):
    assert ev(run(change_scenario, "threshold"))["flood_extent"]["value"] == 64.0


def test_change_adds_the_shallow_flood_and_refuses_the_field(change_scenario):
    """64 km2 open + 32 km2 shallow. The 24 km2 field dropped 5 dB but sits
    at -11 dB, far above the ceiling, so it stays out. Permanent water is
    excluded as ever."""
    result = run(change_scenario, "change")
    assert ev(result)["flood_extent"]["value"] == 96.0


def test_the_baseline_comes_from_the_same_orbit(change_scenario):
    run(change_scenario, "change")
    _, _, calls, _ = change_scenario
    assert calls["baseline"] == 1
    assert calls["baseline_orbit"] == 63


def test_the_change_result_quotes_its_own_score_not_the_darkness_rules(change_scenario):
    """0.489 describes the darkness rule. This result quotes what change
    detection itself measured, with the caveat about how it was measured."""
    from detection import sar
    result = run(change_scenario, "change")
    extent = ev(result)["flood_extent"]
    notes = " ".join(result["unobserved"]["notes"])

    assert extent["confidence"] == sar.CHANGE_VALIDATION["precision"]
    assert f"IoU {sar.CHANGE_VALIDATION['iou']}" in notes
    assert "0.489" not in notes
    assert "tuned and scored on the same chips" in notes
    assert "not been scored" not in notes


def test_without_a_measurement_the_change_result_claims_none(change_scenario, monkeypatch):
    from detection import sar
    monkeypatch.setattr(sar, "CHANGE_VALIDATION", None)
    result = run(change_scenario, "change")
    assert "confidence" not in ev(result)["flood_extent"]
    assert "not been scored" in " ".join(result["unobserved"]["notes"])


def test_the_method_string_says_change_detection_and_names_the_baseline(change_scenario):
    method = ev(run(change_scenario, "change"))["flood_extent"]["method"]
    assert "change detection" in method
    assert "2018-05-01 to 2018-05-31" in method


def test_a_validated_change_result_is_drawn_like_the_darkness_rule(change_scenario):
    assert run(change_scenario, "change")["map"]["overlay_style"] == "translucent"


def test_an_unvalidated_change_result_is_drawn_as_one(change_scenario, monkeypatch):
    from detection import sar
    monkeypatch.setattr(sar, "CHANGE_VALIDATION", None)
    assert run(change_scenario, "change")["map"]["overlay_style"] == "hatched"


def test_the_threshold_path_is_untouched_by_the_method_parameter(change_scenario):
    """Default behaviour must be byte-for-byte what it was."""
    from pipeline import analysis
    S, region, _, _ = change_scenario
    default = analysis.analyse_flood(region, dict(S.META), *S.POST, *S.PRE,
                                     force_sensor="sentinel-1")
    explicit = run(change_scenario, "threshold")
    strip = lambda r: {k: v for k, v in r.items() if k not in ("generated_at", "_internal")}
    assert strip(default) == strip(explicit)


# --------------------------------------------------------- refusals

@pytest.mark.parametrize("kwargs,message", [
    ({"method": "magic"}, "Unknown method"),
    ({"method": "change", "force_sensor": "sentinel-2"}, "radar method"),
    ({"method": "change", "force_sensor": None}, "radar method"),
])
def test_change_detection_is_refused_where_it_cannot_run(change_scenario, kwargs, message):
    from pipeline import analysis
    S, region, _, _ = change_scenario
    args = {"force_sensor": "sentinel-1", **kwargs}
    with pytest.raises(ValueError, match=message):
        analysis.analyse_flood(region, dict(S.META), *S.POST, *S.PRE, **args)


def test_change_detection_without_a_baseline_window_is_refused(change_scenario):
    from pipeline import analysis
    S, region, _, _ = change_scenario
    with pytest.raises(ValueError, match="pre-event baseline"):
        analysis.analyse_flood(region, dict(S.META), *S.POST,
                               force_sensor="sentinel-1", method="change")


def test_no_baseline_imagery_is_not_silently_answered_by_the_darkness_rule(change_scenario,
                                                                           monkeypatch):
    """Asked for change detection, handed a different method's answer without
    being told - the failure the project is built against. It raises."""
    from detection import sar
    from pipeline import analysis
    S, region, _, _ = change_scenario

    def empty(*_, **__):
        raise sar.NoBaselineImagery(*S.PRE, 63)

    monkeypatch.setattr(sar, "baseline_composite", empty)
    with pytest.raises(sar.NoBaselineImagery, match="use method 'threshold'"):
        run(change_scenario, "change")


# --------------------------------------------------------- the endpoint

MAIN = (BACKEND / "main.py").read_text(encoding="utf-8")


def test_a_missing_baseline_is_a_422_that_says_what_would_work():
    body = MAIN.split("def analyze(payload: FloodRequest):")[1].split("@app.")[0]
    assert "except sar.NoBaselineImagery" in body
    assert "status_code=422" in body


def test_adding_method_did_not_rekey_the_cache():
    pytest.importorskip("fastapi")
    import main
    from core import cache

    old = {"region": "kerala", "bbox": None, "point": None, "radius_km": None,
           "polygon": None, "post_start": "2018-08-01", "post_end": "2018-08-31",
           "pre_start": None, "pre_end": None, "sensor": "sentinel-1", "scale": 100,
           "generate_report": True, "use_llm": True}
    assert cache.key_for(main.FloodRequest(**old).cache_key()) == cache.key_for(old)
    assert (main.FloodRequest(**old, method="change").cache_key()
            != main.FloodRequest(**old).cache_key())


# --------------------------------------------------------- the notebook

NOTEBOOK = BACKEND / "notebooks" / "06_change_detection.ipynb"


CREDENTIAL_PATTERNS = [
    r"4/[0-9A-Za-z_-]{20,}",          # Google OAuth verification code
    r"ya29\.[0-9A-Za-z_-]{20,}",      # Google access token
    r"1//[0-9A-Za-z_-]{20,}",         # Google refresh token
    r"gsk_[0-9A-Za-z]{20,}",          # Groq API key
    r"hf_[0-9A-Za-z]{20,}",           # Hugging Face token
    r"\"refresh_token\"\s*:",
]


@pytest.mark.parametrize("notebook", sorted((BACKEND / "notebooks").glob("*.ipynb")),
                         ids=lambda p: p.name)
def test_no_notebook_output_carries_a_credential(notebook):
    """An OAuth verification code was once committed in a notebook's output.

    That is the risk, so that is what is checked. Outputs as such are fine -
    pip logs, printed tables and scores are the point of running a notebook,
    and a test that failed on any output would be red every time one was
    used, and so would stop being read.
    """
    import re

    nb = json.loads(notebook.read_text(encoding="utf-8"))
    for index, cell in enumerate(nb.get("cells", [])):
        for output in cell.get("outputs") or []:
            # Text only. Plots are stored as base64 PNG, which is random
            # letters, digits and '/' - it contains "4/..." by chance, and
            # scanning it flagged 02_landcover_rf.ipynb's charts as OAuth codes.
            parts = [output.get("text", ""), output.get("traceback", ""),
                     output.get("evalue", "")]
            for mime, value in (output.get("data") or {}).items():
                if mime.startswith("text/") or mime == "application/json":
                    parts.append(value)
            text = json.dumps(parts)
            for pattern in CREDENTIAL_PATTERNS:
                assert not re.search(pattern, text), (
                    f"{notebook.name} cell {index} output looks like it contains a "
                    f"credential ({pattern}). Clear the outputs before committing."
                )


def test_the_notebook_exists():
    assert NOTEBOOK.exists()


def test_the_notebook_scores_on_the_same_pixels_and_reports_the_price():
    text = NOTEBOOK.read_text(encoding="utf-8")
    assert "common" in text
    assert "added_false_positives" in text
    assert "recovery" in text
    assert "validation_block_for_backend" in text


# ------------------------------------------ the dates in the real metadata

@pytest.mark.parametrize("value,expected", [
    ("2018-02-15", "2018-02-15"),
    ("2018-02-15T00:00:00", "2018-02-15"),
    ("2018/2/15", "2018-02-15"),
    ("2018-02-15 10:22:01", "2018-02-15"),
    ("15 Feb 2018", "2018-02-15"),
    ("February 15, 2018", "2018-02-15"),
    ("20180215", "2018-02-15"),
    (1518652800000, "2018-02-15"),     # epoch milliseconds
    (1518652800, "2018-02-15"),        # epoch seconds
])
def test_dates_are_read_whatever_the_format(value, expected):
    """The first version assumed ISO and failed on the real metadata."""
    from scripts import fetch_pre_event as F
    assert F.parse_dates([value])[F._as_key(value)].isoformat() == expected


def test_day_first_is_settled_by_the_column_not_guessed_per_value():
    from scripts import fetch_pre_event as F
    out = F.parse_dates(["03/04/2018", "25/04/2018"])
    assert out["03/04/2018"].isoformat() == "2018-04-03"      # day-first, from row 2


def test_month_first_is_settled_the_same_way():
    from scripts import fetch_pre_event as F
    out = F.parse_dates(["03/04/2018", "04/25/2018"])
    assert out["03/04/2018"].isoformat() == "2018-03-04"


def test_a_column_that_could_be_either_is_refused_not_guessed():
    """A guessed month puts every baseline in the wrong season, silently."""
    from scripts import fetch_pre_event as F
    with pytest.raises(ValueError, match="ambiguous"):
        F.parse_dates(["03/04/2018", "05/06/2018"])


def test_an_unreadable_date_names_itself_and_the_way_out():
    from scripts import fetch_pre_event as F
    with pytest.raises(ValueError, match=r"'sometime in spring'.*--dates"):
        F.parse_dates(["sometime in spring"])


def test_the_metadata_parser_uses_the_tolerant_dates():
    from scripts import fetch_pre_event as F
    geojson = {"features": [
        {"properties": {"location": "Bolivia", "s1_date": "2018/2/15"}},
        {"properties": {"location": "Ghana", "s1_date": "2018/9/18"}},
    ]}
    dates = F.event_dates_from_metadata(geojson)
    assert dates["bolivia"].isoformat() == "2018-02-15"
    assert dates["ghana"].isoformat() == "2018-09-18"


def test_a_csv_of_dates_can_replace_the_metadata(tmp_path):
    from scripts import fetch_pre_event as F
    csv = tmp_path / "dates.csv"
    csv.write_text("event,date\nSri Lanka,2017-05-28\nIndia,2016-08-12\n", encoding="utf-8")
    dates = F.dates_from_csv(csv)
    assert dates == {"sri-lanka": __import__("datetime").date(2017, 5, 28),
                     "india": __import__("datetime").date(2016, 8, 12)}


def test_show_dates_stops_before_touching_earth_engine(tmp_path, capsys):
    """The dry run must be safe to run anywhere, including without EE."""
    from scripts import fetch_pre_event as F
    meta = tmp_path / "Sen1Floods11_Metadata.geojson"
    meta.write_text(json.dumps({"features": [
        {"properties": {"location": "Bolivia", "s1_date": "2018/2/15"}}]}), encoding="utf-8")
    F.main(["--sen1floods11", str(tmp_path), "--show-dates"])
    out = capsys.readouterr().out
    assert "bolivia" in out and "2018-02-15" in out
    assert not (tmp_path / "S1Pre").exists(), "a dry run must not create the output folder"


# ------------------------------------ chips whose event name has no date

CAMBODIA = {"type": "Polygon", "coordinates": [[[102.0, 10.0], [108.0, 10.0],
                                                [108.0, 15.0], [102.0, 15.0], [102.0, 10.0]]]}
COLOMBIA = {"type": "MultiPolygon", "coordinates": [[[[-77.0, 3.0], [-72.0, 3.0],
                                                      [-72.0, 8.0], [-77.0, 3.0]]]]}


def test_footprints_are_read_from_any_geometry_type():
    from scripts import fetch_pre_event as F
    boxes = F.event_footprints({"features": [
        {"properties": {"location": "Cambodia"}, "geometry": CAMBODIA},
        {"properties": {"location": "Colombia"}, "geometry": COLOMBIA},
    ]})
    assert boxes["cambodia"] == (102.0, 10.0, 108.0, 15.0)
    assert boxes["colombia"] == (-77.0, 3.0, -72.0, 8.0)


def test_mekong_chips_take_the_date_of_the_event_they_lie_inside():
    """Decided by where the chips are, not by assuming what 'Mekong' means."""
    from scripts import fetch_pre_event as F
    boxes = {"cambodia": (102.0, 10.0, 108.0, 15.0), "colombia": (-77.0, 3.0, -72.0, 8.0)}
    centres = [(104.9, 11.5), (105.2, 12.1)]
    assert F.match_by_location(centres, boxes, ["cambodia", "colombia"]) == "cambodia"


def test_no_match_when_a_chip_lies_outside_every_footprint():
    from scripts import fetch_pre_event as F
    boxes = {"cambodia": (102.0, 10.0, 108.0, 15.0)}
    assert F.match_by_location([(104.9, 11.5), (120.0, 11.5)], boxes, ["cambodia"]) is None


def test_no_match_when_two_footprints_both_fit():
    """Two candidates is a guess. It refuses rather than picking one."""
    from scripts import fetch_pre_event as F
    boxes = {"a": (100.0, 10.0, 110.0, 15.0), "b": (104.0, 11.0, 106.0, 13.0)}
    assert F.match_by_location([(105.0, 12.0)], boxes, ["a", "b"]) is None


def test_an_event_that_already_has_chips_is_never_borrowed(tmp_path, capsys):
    """Only events with no chips of their own are candidates, so Mekong can
    take Cambodia's date but could never take, say, India's."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from scripts import fetch_pre_event as F

    def chip(name, lon, lat):
        with rasterio.open(tmp_path / f"{name}_S1Hand.tif", "w", driver="GTiff",
                           height=4, width=4, count=2, dtype="float32", crs="EPSG:4326",
                           transform=from_origin(lon, lat, 0.001, 0.001)) as dst:
            dst.write(np.zeros((2, 4, 4), "float32"))

    chip("Mekong_1", 105.0, 12.0)
    chip("Mekong_2", 105.5, 12.5)
    chip("India_1", 105.2, 12.2)          # India chip placed inside Cambodia's box

    (tmp_path / "Sen1Floods11_Metadata.geojson").write_text(json.dumps({"features": [
        {"properties": {"location": "Cambodia", "s1_date": "2018-08-05"}, "geometry": CAMBODIA},
        {"properties": {"location": "India", "s1_date": "2016-08-12"}, "geometry": CAMBODIA},
    ]}), encoding="utf-8")

    F.main(["--sen1floods11", str(tmp_path), "--show-dates"])
    out = capsys.readouterr().out
    assert "mekong" in out and "2018-08-05" in out and "cambodia footprint" in out
    assert "NO DATE" not in out


# ------------------------------------------------ parallel, resumable fetch

def test_transient_errors_are_retried_and_real_ones_are_not():
    from scripts import fetch_pre_event as F
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Too many concurrent aggregations")
        return "ok"

    assert F.with_retries(flaky, sleep=lambda s: None) == "ok"
    assert calls["n"] == 3

    def broken():
        calls["n"] += 1
        raise ValueError("Band 'VV' not found")

    calls["n"] = 0
    with pytest.raises(ValueError):
        F.with_retries(broken, sleep=lambda s: None)
    assert calls["n"] == 1, "a real error must not be retried"


@pytest.fixture
def fetch_env(tmp_path, monkeypatch):
    """Twelve chips, metadata, and Earth Engine replaced by fakes."""
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin

    from core import earth_engine
    from scripts import fetch_pre_event as F

    for i in range(12):
        with rasterio.open(tmp_path / f"Bolivia_{i}_S1Hand.tif", "w", driver="GTiff",
                           height=4, width=4, count=2, dtype="float32", crs="EPSG:4326",
                           transform=from_origin(-65 + i * 0.01, -14, 0.001, 0.001)) as dst:
            dst.write(np.zeros((2, 4, 4), "float32"))
    (tmp_path / "Sen1Floods11_Metadata.geojson").write_text(json.dumps({"features": [
        {"properties": {"location": "Bolivia", "s1_date": "2018-02-15"}}]}), encoding="utf-8")

    calls = {"fetch": 0, "rate_limited": 0}
    lock = __import__("threading").Lock()

    def fake_fetch(ee, region, grid, start, end, orbit):
        with lock:
            calls["fetch"] += 1
            if calls["rate_limited"] < 2:
                calls["rate_limited"] += 1
                raise RuntimeError("Too many concurrent aggregations")
        return np.full((2, 4, 4), -9.0, "float32"), 5

    monkeypatch.setattr(earth_engine, "initialize", lambda: None)
    monkeypatch.setattr(F, "flood_orbit", lambda ee, region, day: 156)
    monkeypatch.setattr(F, "fetch_baseline", fake_fetch)
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    import ee
    monkeypatch.setattr(ee.Geometry, "Rectangle", lambda *a, **k: object())
    return tmp_path, F, calls


def test_parallel_fetch_gets_every_chip_and_survives_rate_limits(fetch_env):
    root, F, calls = fetch_env
    F.main(["--sen1floods11", str(root), "--workers", "4"])

    out = root / "S1Pre"
    assert len(list(out.glob("*_S1Pre.tif"))) == 12
    assert not list(out.glob("*.part.tif"))
    records = [json.loads(l) for l in (out / "manifest.jsonl").read_text().splitlines()]
    assert len(records) == 12
    assert all(r["status"] == "ok" for r in records), "rate limits must be retried, not failed"


def test_a_rerun_skips_finished_chips(fetch_env):
    root, F, calls = fetch_env
    F.main(["--sen1floods11", str(root), "--workers", "4"])
    before = calls["fetch"]
    F.main(["--sen1floods11", str(root), "--workers", "4"])
    assert calls["fetch"] == before, "finished chips were fetched again"


def test_a_half_written_file_is_never_mistaken_for_a_finished_one(fetch_env):
    """A Ctrl+C mid-write used to leave a truncated .tif that every later run
    skipped as 'already present'. Now writes go to .part.tif and are renamed
    only when complete, and leftover .part files are discarded on start."""
    root, F, calls = fetch_env
    out = root / "S1Pre"
    out.mkdir()
    (out / "Bolivia_0_S1Pre.part.tif").write_bytes(b"truncated")

    F.main(["--sen1floods11", str(root), "--workers", "2"])

    assert not list(out.glob("*.part.tif"))
    import rasterio
    with rasterio.open(out / "Bolivia_0_S1Pre.tif") as src:
        assert src.read(1).shape == (4, 4)


def test_the_recovery_quoted_in_sar_py_is_the_shipped_runs():
    """The comment first quoted the -3 dB run's recovery (40% / 12%) against
    the -5 dB settings. Prose that restates a measurement goes stale; this
    ties it to the file."""
    if not RESULTS.exists():
        pytest.skip("evaluation/change_results.json not present")
    measured = json.loads(RESULTS.read_text(encoding="utf-8"))
    source = (BACKEND / "detection" / "sar.py").read_text(encoding="utf-8")
    for bin_name in ("marginal", "moderate"):
        b = measured["recovery_by_bin"][bin_name]
        share = f"{100 * b['recovered'] / b['missed']:.1f}%"
        assert share in source, f"sar.py does not quote the measured {bin_name} share {share}"
