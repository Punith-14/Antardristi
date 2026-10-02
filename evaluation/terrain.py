"""
Does a terrain check remove false flood water? Measured, not assumed.

Radar calls dark pixels water. Hill slopes facing away from the satellite
(radar shadow) and smooth dry ground high above any river are dark too. A
flood cannot sit 40 m above the stream it would drain into, or on a 25 degree
slope - so operational services mask such pixels out. TU Wien's Sentinel-1
algorithm in the Copernicus Global Flood Monitoring service uses a HAND
(Height Above Nearest Drainage) exclusion mask; published tests found about
15 m most suitable, and the gain largest where look-alikes and relief are
both large.

The rule scored here, on top of the shipped darkness rule:

    water = dark AND NOT (hand > max_hand_m) AND NOT (slope > max_slope_deg)

Missing terrain is not evidence against water: where HAND or slope is NaN the
pixel keeps its darkness verdict.

How it is chosen and judged (notebook 09):

    1. max_hand_m and max_slope_deg are chosen on the TRAIN split only, at
       the 200 m scale the service runs at.
    2. They are scored on test, valid and Bolivia, at 10, 100 and 200 m.
    3. `decide` applies a rule written down BEFORE the run:
         - test-split IoU at 200 m must rise, and the paired bootstrap range
           of the gain must lie above zero;
         - recall on the held-out Indian chips must not fall by more than
           INDIA_RECALL_TOLERANCE - a check that deletes real Indian flood
           water is not worth its precision.
       Fail either and nothing ships; the measurement is reported as a
       negative result.

Pure numpy, so tests run it on synthetic chips without the dataset.
"""

import math

import numpy as np

# Candidate thresholds. inf = "this test switched off", so the grid includes
# HAND alone, slope alone, both, and neither (which must reproduce the
# baseline exactly - a test holds it to that).
HAND_GRID_M = (5.0, 10.0, 15.0, 20.0, 30.0, 50.0, math.inf)
SLOPE_GRID_DEG = (5.0, 10.0, 15.0, 20.0, 30.0, math.inf)

INDIA_RECALL_TOLERANCE = 0.02
BOOTSTRAP_N = 2000


def implausible(hand, slope, max_hand_m, max_slope_deg):
    """True where terrain says a flood cannot be. NaN terrain -> False."""
    hand = np.asarray(hand, dtype=np.float64)
    slope = np.asarray(slope, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        high = np.isfinite(hand) & (hand > max_hand_m)
        steep = np.isfinite(slope) & (slope > max_slope_deg)
    return high | steep


def apply(pred, hand, slope, max_hand_m, max_slope_deg):
    """The darkness prediction with terrain-implausible pixels removed."""
    return np.asarray(pred, dtype=bool) & ~implausible(hand, slope, max_hand_m, max_slope_deg)


def _counts(pred, truth):
    pred = np.asarray(pred, dtype=bool)
    truth = np.asarray(truth)
    labelled = truth != -1
    water = truth == 1
    return (int((pred & water & labelled).sum()),
            int((pred & ~water & labelled).sum()),
            int((~pred & water & labelled).sum()))


def chip_counts(chips, pred_key, max_hand_m=math.inf, max_slope_deg=math.inf,
                hand_key="hand", slope_key="slope", truth_key="truth"):
    """(tp, fp, fn) per chip for the rule at these thresholds."""
    return [_counts(apply(c[pred_key], c[hand_key], c[slope_key], max_hand_m, max_slope_deg),
                    c[truth_key]) for c in chips]


def pooled(counts):
    tp = sum(c[0] for c in counts)
    fp = sum(c[1] for c in counts)
    fn = sum(c[2] for c in counts)
    safe = lambda n, d: n / d if d else 0.0
    return {"iou": safe(tp, tp + fp + fn), "precision": safe(tp, tp + fp),
            "recall": safe(tp, tp + fn), "chips": len(counts)}


def sweep(chips, pred_key, hand_grid=HAND_GRID_M, slope_grid=SLOPE_GRID_DEG, **keys):
    """Pooled scores for every (max_hand, max_slope) pair, best IoU first."""
    rows = []
    for h in hand_grid:
        for s in slope_grid:
            scores = pooled(chip_counts(chips, pred_key, h, s, **keys))
            rows.append({"max_hand_m": h, "max_slope_deg": s, **scores})
    # Ties go to the LOOSER rule: if removing less scores the same, remove less.
    rows.sort(key=lambda r: (-round(r["iou"], 6), -r["max_hand_m"], -r["max_slope_deg"]))
    return rows


def removed_breakdown(chips, pred_key, max_hand_m, max_slope_deg, **keys):
    """What the check removed: how much was dry (good) and how much was water.

    The number to read beside the IoU: a check that removes 1,000 false
    pixels and 400 true ones is buying precision with recall.
    """
    hand_key = keys.get("hand_key", "hand")
    slope_key = keys.get("slope_key", "slope")
    truth_key = keys.get("truth_key", "truth")
    removed_dry = removed_water = 0
    for c in chips:
        pred = np.asarray(c[pred_key], dtype=bool)
        gone = pred & implausible(c[hand_key], c[slope_key], max_hand_m, max_slope_deg)
        truth = np.asarray(c[truth_key])
        removed_water += int((gone & (truth == 1)).sum())
        removed_dry += int((gone & (truth == 0)).sum())
    total = removed_dry + removed_water
    return {"removed_dry_px": removed_dry, "removed_water_px": removed_water,
            "share_dry": removed_dry / total if total else None}


def paired_bootstrap_gain(base_counts, new_counts, n=BOOTSTRAP_N, seed=0, level=0.95):
    """(low, high) range for IoU(new) - IoU(base), resampling whole chips.

    Paired: each resample draws the same chips for both rules, so the range
    describes the GAIN, not two independent uncertainties stacked together.
    """
    base = np.asarray(base_counts, dtype=np.float64)
    new = np.asarray(new_counts, dtype=np.float64)
    if base.shape != new.shape or base.ndim != 2 or len(base) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(base), size=(n, len(base)))

    def iou(stack):
        sums = stack[picks].sum(axis=1)
        denom = sums.sum(axis=1)
        return np.divide(sums[:, 0], denom, out=np.zeros(n), where=denom > 0)

    gain = iou(new) - iou(base)
    tail = (1 - level) / 2
    return float(np.quantile(gain, tail)), float(np.quantile(gain, 1 - tail))


def decide(test_base, test_new, gain_ci, india_base=None, india_new=None,
           tolerance=INDIA_RECALL_TOLERANCE):
    """The pre-stated shipping rule. Returns {"ship": bool, "reasons": [...]}."""
    reasons = []
    ok = True
    gain = test_new["iou"] - test_base["iou"]
    if gain <= 0:
        ok = False
        reasons.append(f"test IoU did not rise ({test_base['iou']:.3f} -> {test_new['iou']:.3f})")
    else:
        reasons.append(f"test IoU rose {test_base['iou']:.3f} -> {test_new['iou']:.3f} (+{gain:.3f})")
    low, high = gain_ci
    if not (isinstance(low, float) and low > 0):
        ok = False
        reasons.append(f"the gain's 95% range ({low:.3f} to {high:.3f}) does not exclude zero")
    else:
        reasons.append(f"the gain's 95% range is {low:.3f} to {high:.3f}, above zero")
    if india_base is not None and india_new is not None:
        drop = india_base["recall"] - india_new["recall"]
        if drop > tolerance:
            ok = False
            reasons.append(f"Indian recall fell by {drop:.3f}, more than the {tolerance} allowed")
        else:
            reasons.append(f"Indian recall changed by {-drop:+.3f} (allowed fall {tolerance})")
    else:
        reasons.append("no held-out Indian chips with terrain: India check not possible")
    return {"ship": ok, "reasons": reasons}


def backend_block(choice, scores_200, india_200, source, decision):
    """What sar.TERRAIN_RULE should hold, or None if the check does not ship."""
    if not decision["ship"]:
        return None
    # inf means "that test is off"; written as None so the block is plain JSON.
    finite = lambda v: None if v is None or math.isinf(v) else v
    return {
        "hand_source": source,
        "max_hand_m": finite(choice["max_hand_m"]),
        "max_slope_deg": finite(choice["max_slope_deg"]),
        "validation": {
            "dataset": (f"Sen1Floods11 HandLabeled test split ({scores_200['chips']} chips) "
                        "at 200 m with the terrain check, thresholds chosen on the train split"),
            "iou": round(scores_200["iou"], 3),
            "precision": round(scores_200["precision"], 3),
            "recall": round(scores_200["recall"], 3),
        },
        "india": india_200,
    }


def plain(obj):
    """obj with inf/NaN as None and numpy scalars as Python ones - strict JSON."""
    if isinstance(obj, dict):
        return {str(k): plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj
