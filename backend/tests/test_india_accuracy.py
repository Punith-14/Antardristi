"""
Accuracy per event and for India (evaluation/events.py, notebook 08), and the
clause that carries the India figure into every flood result.
"""

import json
import math
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
EVALUATION = BACKEND.parent / "evaluation"
for path in (BACKEND, EVALUATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import events as EV  # noqa: E402

RESULTS = EVALUATION / "events_results.json"


def chip(key, split, counts):
    return {"key": key, "split": split, "counts": counts}


def counts_of(c):
    return c["counts"]


def test_events_are_named_by_everything_before_the_last_underscore():
    assert EV.event_of("Sri-Lanka_249079") == "Sri-Lanka"
    assert EV.event_of("India_1000") == "India"


def test_the_india_figure_excludes_chips_the_threshold_was_tuned_on():
    """Train chips helped choose the threshold. A perfect train chip must not
    lift the held-out India figure."""
    chips = [
        chip("India_1", "train", (1000, 0, 0)),     # perfect, but tuned on
        chip("India_2", "test", (50, 25, 25)),      # IoU 0.5
        chip("India_3", "valid", (50, 25, 25)),     # IoU 0.5
        chip("Ghana_4", "test", (100, 0, 0)),       # another event
    ]
    summary = EV.india_summary(chips, counts_of, n=200)
    assert summary["held_out"]["chips"] == 2
    assert summary["held_out"]["iou"] == pytest.approx(0.5)
    assert summary["all"]["chips"] == 3
    assert summary["all"]["iou"] > summary["held_out"]["iou"]


def test_the_range_brackets_the_score_and_widens_with_disagreement():
    agree = [(50, 25, 25)] * 10
    disagree = [(90, 5, 5)] * 5 + [(10, 45, 45)] * 5
    lo_a, hi_a = EV.bootstrap_iou(agree, n=500)
    lo_d, hi_d = EV.bootstrap_iou(disagree, n=500)

    assert lo_a == pytest.approx(0.5) and hi_a == pytest.approx(0.5)
    assert lo_d < EV.pooled_scores(disagree)["iou"] < hi_d
    assert hi_d - lo_d > 0.1


def test_one_chip_has_no_range():
    lo, hi = EV.bootstrap_iou([(50, 25, 25)])
    assert math.isnan(lo) and math.isnan(hi)


def test_the_range_is_reproducible():
    data = [(90, 5, 5), (10, 45, 45), (50, 25, 25), (70, 20, 10)]
    assert EV.bootstrap_iou(data, seed=3) == EV.bootstrap_iou(data, seed=3)


def test_per_event_scores_group_by_event():
    chips = [chip("India_1", "test", (50, 25, 25)), chip("India_2", "test", (50, 25, 25)),
             chip("Spain_3", "test", (100, 0, 0))]
    scores = EV.by_event(chips, counts_of)
    assert set(scores) == {"India", "Spain"}
    assert scores["India"]["chips"] == 2
    assert scores["Spain"]["iou"] == 1.0


# --------------------------------------------- carried into every flood result

def test_no_india_figure_means_no_clause():
    pytest.importorskip("ee")
    from pipeline.analysis import india_clause
    assert india_clause(None) == ""
    assert india_clause({}) == ""


def test_the_india_clause_states_the_score_and_its_range():
    pytest.importorskip("ee")
    from pipeline.analysis import india_clause
    text = india_clause({"event": "Sen1Floods11 India event (2016)", "chips": 21,
                         "iou": 0.58, "ci95": [0.47, 0.67]})
    assert "21 chips" in text
    assert "IoU 0.58" in text
    assert "95% range 0.47 to 0.67" in text
    assert text.startswith(" On held-out Indian chips")


def test_the_india_clause_joins_the_accuracy_note_not_a_new_note(monkeypatch):
    """Every note must survive into the report; a clause in the existing note
    adds no new caveat for the model to drop."""
    pytest.importorskip("ee")
    sys.path.insert(0, str(BACKEND / "tests"))
    import flood_scenario as S
    from detection import sar

    india = {"event": "Sen1Floods11 India event (2016)", "chips": 21,
             "iou": 0.58, "ci95": [0.47, 0.67]}
    with_india = dict(sar.VALIDATION, india=india)
    real = S.fake_sar_detect

    def detect(*args, **kwargs):
        mask, composite, info = real(*args, **kwargs)
        info["validation"] = dict(with_india)
        return mask, composite, info

    from pipeline import analysis

    base = S.run(monkeypatch, "sentinel-1", baseline=False)
    region = S.install(monkeypatch)            # S.run re-installs its own fakes,
    monkeypatch.setattr(sar, "detect_water", detect)   # so patch after it
    result = analysis.analyse_flood(region, dict(S.META), *S.POST,
                                    force_sensor="sentinel-1")

    notes = result["unobserved"]["notes"]
    assert len(notes) == len(base["unobserved"]["notes"])
    assert any("held-out Indian chips" in n and "IoU 0.58" in n for n in notes)


def test_the_shipped_india_figure_is_notebook_08s_measurement():
    """Once filled, the figure is tied to the file the notebook wrote."""
    pytest.importorskip("ee")
    from detection import sar

    india = sar.SCALE_RULES[200]["validation"].get("india")
    if india is None:
        pytest.skip("India figure not filled yet - run notebook 08")
    if not RESULTS.exists():
        pytest.fail("sar.py carries an India figure but events_results.json is missing")
    measured = json.loads(RESULTS.read_text(encoding="utf-8"))["india_validation_block_for_backend"]
    for key in ("chips", "iou", "precision", "recall", "ci95"):
        assert india[key] == measured[key], key


def test_the_dry_ground_warning_quotes_the_measured_precision():
    """Somalia and Pakistan scored precision near 0.2-0.3: dry ground reads as
    water. The caveat quotes notebook 08's figures, so it cannot drift."""
    pytest.importorskip("ee")
    from detection import sar
    if not RESULTS.exists():
        pytest.skip("events_results.json not present")
    per = json.loads(RESULTS.read_text(encoding="utf-8"))["per_event"]
    text = " ".join(sar.KNOWN_CONFUSIONS)
    assert f"{per['Somalia']['precision']:.2f} in arid Somalia" in text
    assert f"{per['Pakistan']['precision']:.2f} in Pakistan" in text
