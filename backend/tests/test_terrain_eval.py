"""
C1: the terrain-check measurement (evaluation/terrain.py, notebook 09).

The rule, the train-only choice, the paired bootstrap and the shipping
decision - and notebook 09 run end to end on synthetic chips, so a broken
cell is found here rather than after a long terrain fetch.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
EVALUATION = BACKEND.parent / "evaluation"
NOTEBOOK = BACKEND / "notebooks" / "09_terrain_check.ipynb"
if str(EVALUATION) not in sys.path:
    sys.path.insert(0, str(EVALUATION))

import terrain as T  # noqa: E402

INF = math.inf


def test_high_or_steep_is_implausible_and_missing_terrain_is_not():
    hand = np.array([5.0, 40.0, 5.0, np.nan])
    slope = np.array([2.0, 2.0, 30.0, np.nan])
    assert T.implausible(hand, slope, 15, 20).tolist() == [False, True, True, False]
    assert not T.implausible(hand, slope, INF, INF).any(), "inf switches a test off"


def test_the_check_only_ever_removes_water():
    pred = np.array([True, False, True])
    out = T.apply(pred, [100, 100, 1], [0, 0, 0], 15, INF)
    assert out.tolist() == [False, False, True]


def chip(pred, truth, hand, slope):
    return {"pred": np.array(pred, bool), "truth": np.array(truth),
            "hand": np.array(hand, float), "slope": np.array(slope, float)}


# 4 pixels: true water low and flat, a dark hill (high, steep), a dark dry
# flat, and dry land not predicted.
CHIPS = [chip([1, 1, 1, 0], [1, 0, 0, 0], [2, 60, 2, 2], [1, 30, 1, 1]) for _ in range(5)]


def test_no_check_reproduces_the_darkness_rule_exactly():
    base = [T._counts(c["pred"], c["truth"]) for c in CHIPS]
    assert T.chip_counts(CHIPS, "pred", INF, INF) == base


def test_the_sweep_finds_the_rule_that_removes_the_hill_and_prefers_the_looser_on_ties():
    table = T.sweep(CHIPS, "pred", hand_grid=(15.0, 50.0, INF), slope_grid=(20.0, INF))
    best = table[0]
    assert best["iou"] == pytest.approx(0.5)            # hill gone, dry flat stays
    # HAND 15, HAND 50 and slope 20 all remove the hill; the loosest wins.
    assert (best["max_hand_m"], best["max_slope_deg"]) == (INF, 20.0)
    off = next(r for r in table if r["max_hand_m"] == INF and r["max_slope_deg"] == INF)
    assert off["iou"] == pytest.approx(1 / 3)


def test_the_removed_breakdown_separates_dry_from_water():
    hurt = [chip([1, 1], [1, 0], [40, 40], [0, 0])]
    assert T.removed_breakdown(hurt, "pred", 15, INF) == {
        "removed_dry_px": 1, "removed_water_px": 1, "share_dry": 0.5}


def test_the_paired_gain_range():
    same = [(10, 5, 5)] * 10
    assert T.paired_bootstrap_gain(same, same, n=200) == (0.0, 0.0)
    better = [(10, 1, 5)] * 10
    low, high = T.paired_bootstrap_gain(same, better, n=200)
    assert low > 0 and high >= low
    lo, hi = T.paired_bootstrap_gain(same[:1], better[:1])
    assert math.isnan(lo) and math.isnan(hi)


BASE = {"iou": 0.60, "precision": 0.80, "recall": 0.70}
UP = {"iou": 0.64, "precision": 0.88, "recall": 0.69}


def test_decide_ships_a_real_gain_that_keeps_indian_recall():
    d = T.decide(BASE, UP, (0.01, 0.07), {"recall": 0.79}, {"recall": 0.78})
    assert d["ship"] is True


@pytest.mark.parametrize("new,ci,india_new,why", [
    ({**UP, "iou": 0.59}, (0.01, 0.07), 0.79, "did not rise"),
    (UP, (-0.01, 0.07), 0.79, "does not exclude zero"),
    (UP, (float("nan"), float("nan")), 0.79, "does not exclude zero"),
    (UP, (0.01, 0.07), 0.75, "Indian recall fell"),
])
def test_decide_refuses_each_failure(new, ci, india_new, why):
    d = T.decide(BASE, new, ci, {"recall": 0.79}, {"recall": india_new})
    assert d["ship"] is False
    assert any(why in r for r in d["reasons"])


def test_no_block_unless_it_ships_and_inf_is_written_as_off():
    no = {"ship": False, "reasons": []}
    assert T.backend_block({"max_hand_m": 15, "max_slope_deg": INF}, UP | {"chips": 88}, {}, "x", no) is None
    block = T.backend_block({"max_hand_m": 15.0, "max_slope_deg": INF}, UP | {"chips": 88},
                            {"iou": 0.7}, "MERIT/Hydro/v1_0_1", {"ship": True})
    assert block["max_hand_m"] == 15.0 and block["max_slope_deg"] is None
    assert block["validation"]["iou"] == 0.64
    json.dumps(block, allow_nan=False)


def test_plain_makes_strict_json():
    out = T.plain({"a": INF, "b": [np.float32(0.5), float("nan")], "c": np.int64(3)})
    assert out == {"a": None, "b": [0.5, None], "c": 3}
    json.dumps(out, allow_nan=False)


# ------------------------------------------------------------- notebook 09

def test_the_decision_rule_is_written_before_any_result():
    cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
    intro = "".join(cells[0]["source"])
    assert "fixed before running" in intro
    assert "0.02" in intro and "above zero" in intro
    # Outputs are kept once run, as for notebooks 05-08: they are the record
    # of the measurement. test_change_detection scans them for credentials.


def _synthetic_dataset(root, rng):
    import rasterio
    from rasterio.transform import from_origin

    n = 60
    for sub in ("S1Hand", "LabelHand", "Terrain", "splits"):
        (root / sub).mkdir(parents=True)
    splits = {"train": [], "valid": [], "test": [], "bolivia": []}
    index = 1000
    plan = [("train", 6), ("valid", 2), ("test", 4)]
    for split, count in plan:
        for _ in range(count):
            for event in ("India", "Spain"):
                index += 1
                splits[split].append(f"{event}_{index}")
    for _ in range(2):
        index += 1
        splits["bolivia"].append(f"Bolivia_{index}")

    for split, keys in splits.items():
        for key in keys:
            vv = rng.normal(-10, 1, (n, n)).astype("float32")
            truth = np.zeros((n, n), "int8")
            hand = np.full((n, n), 3, "float32")
            slope = np.full((n, n), 1, "float32")
            truth[0:40, 0:20] = 1
            vv[0:40, 0:20] = -24
            vv[0:40, 40:60] = -23            # radar shadow on a hill
            hand[0:40, 40:60] = 60
            slope[0:40, 40:60] = 28
            profile = dict(driver="GTiff", height=n, width=n, crs="EPSG:32646",
                           transform=from_origin(5e5, 2.9e6, 10, 10))
            with rasterio.open(root / "S1Hand" / f"{key}_S1Hand.tif", "w", count=2,
                               dtype="float32", **profile) as dst:
                dst.write(np.stack([vv, vv - 6]))
            with rasterio.open(root / "LabelHand" / f"{key}_LabelHand.tif", "w", count=1,
                               dtype="int8", **profile) as dst:
                dst.write(truth[None])
            with rasterio.open(root / "Terrain" / f"{key}_Terrain.tif", "w", count=3,
                               dtype="float32", **profile) as dst:
                dst.write(np.stack([hand, hand, slope]))
                for i, name in enumerate(("hand_merit", "hand_30m", "slope_deg"), 1):
                    dst.set_band_description(i, name)
        (root / "splits" / f"flood_{split}_data.txt").write_text(
            "\n".join(f"{k}_S1Hand.tif,{k}_LabelHand.tif" for k in keys))


def test_notebook_09_runs_end_to_end_on_synthetic_chips(tmp_path):
    pytest.importorskip("rasterio")
    pytest.importorskip("scipy")
    import shutil

    data = tmp_path / "sen1floods11"
    _synthetic_dataset(data, np.random.default_rng(0))
    evaluation = tmp_path / "evaluation"
    evaluation.mkdir()
    for name in ("spatial.py", "change.py", "events.py", "terrain.py", "spatial_results.json"):
        shutil.copy(EVALUATION / name, evaluation / name)

    cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
    namespace = {}
    saved_path = list(sys.path)
    try:
        for cell in cells:
            if cell["cell_type"] != "code":
                continue
            source = "".join(cell["source"])
            if source.startswith("!"):
                continue
            source = source.replace('LOCAL = r"C:\\sen1floods11"', f'LOCAL = r"{data}"')
            source = source.replace(
                'EVALUATION = Path.cwd().resolve().parent.parent / "evaluation"',
                f'EVALUATION = Path(r"{evaluation}")')
            exec(compile(source, str(NOTEBOOK), "exec"), namespace)
    finally:
        sys.path[:] = saved_path
        for module in ("spatial", "change", "events", "terrain"):
            sys.modules.pop(module, None)        # the copies must not shadow the real ones

    result = json.loads((evaluation / "terrain_results.json").read_text(encoding="utf-8"))
    assert result["decision"]["ship"] is True, result["decision"]
    assert result["source"] == "hand_merit", "the catalogue source wins ties"
    assert result["terrain_rule_for_backend"]["hand_source"] == "MERIT/Hydro/v1_0_1"
    after = result["results"]["test 200 m"]["after"]
    before = result["results"]["test 200 m"]["before"]
    assert after["iou"] > before["iou"]
    assert result["results"]["test 200 m"]["removed"]["removed_water_px"] == 0


# ------------------------------------------------------------- the fetch

def test_the_fetch_writes_the_bands_the_notebook_reads():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from scripts import fetch_terrain as F

    assert F.BANDS == ("hand_merit", "hand_30m", "slope_deg")
    source = "".join("".join(c["source"]) for c in
                     json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"])
    for band in F.BANDS:
        assert f'"{band}"' in source
    assert F.chip_key(Path("x/India_1000_S1Hand.tif")) == "India_1000"
    assert F.output_path("out", "India_1000") == Path("out") / "India_1000_Terrain.tif"


def test_the_fetch_samples_terrain_bilinearly_on_the_chips_grid():
    """Nearest-neighbour from 90 m would stamp 9 x 9 blocks on every chip."""
    text = (BACKEND / "scripts" / "fetch_terrain.py").read_text(encoding="utf-8")
    assert '.resample("bilinear")' in text
    assert "ee_grid(src.transform, src.width, src.height, src.crs)" in text
