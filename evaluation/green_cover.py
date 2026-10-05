"""
Green cover: can a year of imagery tell trees from crops? (notebook 10)

Today's rule is NDVI > 0.4 on a dry-season median. It scores IoU 0.405 against
WorldCover's tree class: Kerala 0.905, Punjab 0.047, because a green wheat
field in February is as green as a forest. This module holds everything the
measurement decides with, so the rule is fixed - and tested - before any
number is read.

Design
------
* Districts, not pixels, are split. Train and test districts come from
  DIFFERENT STATES, so neighbouring near-identical pixels can never sit on
  both sides.
* Training pixels are stratified by WorldCover class (every kind of non-tree
  is seen). Test pixels are drawn uniformly at random, the same number per
  district, so test scores reflect real tree prevalence and each district
  counts equally.
* Today's rule is scored on the same test pixels.
* WorldCover is the reference, Dynamic World a second opinion: a gain that
  appears only against WorldCover is learning WorldCover's habits.
"""

import math

import numpy as np

# ------------------------------------------------------------ the districts

TRAIN = "train"
TEST = "test"

DISTRICTS = [
    # train: seven states
    {"state": "Punjab", "district": "Ludhiana", "role": TRAIN, "landscape": "cropland"},
    {"state": "Kerala", "district": "Thrissur", "role": TRAIN, "landscape": "forest and plantation"},
    {"state": "Kerala", "district": "Wayanad", "role": TRAIN, "landscape": "forest"},
    {"state": "Karnataka", "district": "Bangalore Urban", "role": TRAIN, "landscape": "urban",
     "aliases": ["Bengaluru Urban"]},
    {"state": "Karnataka", "district": "Shimoga", "role": TRAIN, "landscape": "forest and cropland",
     "aliases": ["Shivamogga"]},
    {"state": "Rajasthan", "district": "Jaisalmer", "role": TRAIN, "landscape": "arid"},
    {"state": "Uttarakhand", "district": "Dehradun", "role": TRAIN, "landscape": "forest",
     "aliases": ["Dehra Dun"]},
    {"state": "Maharashtra", "district": "Nashik", "role": TRAIN, "landscape": "cropland",
     "aliases": ["Nasik"]},
    {"state": "Odisha", "district": "Cuttack", "role": TRAIN, "landscape": "rice and forest"},
    # test: seven other states, never seen in training
    {"state": "Haryana", "district": "Karnal", "role": TEST, "landscape": "cropland"},
    {"state": "West Bengal", "district": "Barddhaman", "role": TEST, "landscape": "cropland",
     "aliases": ["Bardhaman", "Burdwan"]},
    {"state": "Uttar Pradesh", "district": "Meerut", "role": TEST, "landscape": "cropland"},
    {"state": "Chhattisgarh", "district": "Bastar", "role": TEST, "landscape": "forest"},
    {"state": "Tamil Nadu", "district": "Nilgiris", "role": TEST, "landscape": "forest",
     "aliases": ["The Nilgiris"]},
    {"state": "Telangana", "district": "Hyderabad", "role": TEST, "landscape": "urban"},
    {"state": "Gujarat", "district": "Kachchh", "role": TEST, "landscape": "arid",
     "aliases": ["Kutch"]},
]

# Must equal GREEN_FEATURES_A/B in backend/detection/seasonal.py (a test checks).
FEATURES_A = [
    "ndvi_p10", "ndvi_p50", "ndvi_p90", "ndvi_std",
    "ndvi_q1", "ndvi_q2", "ndvi_q3", "ndvi_q4",
    "ndvi_amp", "ndvi_phase",
    "ndmi_p50", "swir1_p50", "swir2_p50",
    "vv_p50", "vh_p50", "vv_std", "vh_std", "vv_vh",
]
FEATURES_B = FEATURES_A + ["canopy_h"]

YEAR = 2021                  # WorldCover v200 is a 2021 map
SCALE = 10
TRAIN_PER_CLASS = 150        # per WorldCover class, per training district
TEST_PIXELS = 3000           # uniform random, per test district
SEED = 42

# Fixed before the run; not tuned on test.
RF_PARAMS = {"n_estimators": 150, "max_depth": 14, "min_samples_leaf": 3,
             "random_state": 0, "n_jobs": -1}
PROBABILITY_CUT = 0.5

# ------------------------------------------------------------ the decision rule

MIN_IOU = 0.55               # held-out pooled IoU against WorldCover
MIN_GAIN = 0.10              # over today's rule, same pixels
MIN_CROP_PRECISION = 0.50    # in held-out cropland districts
HEIGHT_MUST_ADD = 0.02       # canopy height is an extra dependency; it must earn it

DECISION_RULE = (
    f"Ship a variant only if, on the held-out districts: pooled IoU >= {MIN_IOU}; "
    f"IoU beats NDVI > 0.4 by >= {MIN_GAIN} with the district-bootstrap 95% range of "
    f"the gain above zero; in cropland districts IoU rises and precision >= "
    f"{MIN_CROP_PRECISION}; and the gain also shows against Dynamic World. "
    f"Variant B (with canopy height) is preferred only if it passes and beats A by "
    f">= {HEIGHT_MUST_ADD}."
)


def by_role(role):
    return [d for d in DISTRICTS if d["role"] == role]


def states_are_disjoint(districts=DISTRICTS):
    train = {d["state"] for d in districts if d["role"] == TRAIN}
    test = {d["state"] for d in districts if d["role"] == TEST}
    return not (train & test)


# ------------------------------------------------------------ scoring

def counts(pred, truth):
    pred = np.asarray(pred, bool)
    truth = np.asarray(truth, bool)
    return {
        "tp": int(np.sum(pred & truth)),
        "fp": int(np.sum(pred & ~truth)),
        "fn": int(np.sum(~pred & truth)),
        "tn": int(np.sum(~pred & ~truth)),
    }


def scores(c):
    def safe(a, b):
        return a / b if b else 0.0
    precision = safe(c["tp"], c["tp"] + c["fp"])
    recall = safe(c["tp"], c["tp"] + c["fn"])
    return {
        "iou": safe(c["tp"], c["tp"] + c["fp"] + c["fn"]),
        "precision": precision,
        "recall": recall,
        "f1": safe(2 * precision * recall, precision + recall),
        **c,
    }


def pooled(count_list):
    total = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for c in count_list:
        for k in total:
            total[k] += c[k]
    return scores(total)


def district_counts(frame, pred_col, truth_col, names=None):
    """{district: counts} over the rows of a table with a 'district' column."""
    out = {}
    for name, rows in frame.groupby("district", sort=True):
        if names is not None and name not in names:
            continue
        out[name] = counts(rows[pred_col].to_numpy(), rows[truth_col].to_numpy())
    return out


def baseline_prediction(frame, band="ndvi_dry", threshold=0.4):
    return frame[band].to_numpy() > threshold


def paired_bootstrap_gain(base, new, n=2000, seed=0):
    """95% range of the pooled-IoU gain, resampling whole districts.

    base, new: {district: counts}, same keys.
    """
    names = sorted(base)
    if not names:
        return (0.0, 0.0)
    rng = np.random.default_rng(seed)
    gains = []
    for _ in range(n):
        pick = rng.integers(0, len(names), len(names))
        chosen = [names[i] for i in pick]
        gains.append(pooled([new[k] for k in chosen])["iou"] - pooled([base[k] for k in chosen])["iou"])
    lo, hi = np.percentile(gains, [2.5, 97.5])
    return (float(lo), float(hi))


# ------------------------------------------------------------ the decision

def decide(base, new, gain_ci, crop_base, crop_new, dw_base, dw_new):
    """Apply DECISION_RULE. All arguments except gain_ci are score dicts."""
    reasons, ship = [], True

    def check(ok, text):
        nonlocal ship
        ship = ship and ok
        reasons.append(("PASS " if ok else "FAIL ") + text)

    check(new["iou"] >= MIN_IOU, f"held-out IoU {new['iou']:.3f} (needs >= {MIN_IOU})")
    gain = new["iou"] - base["iou"]
    check(gain >= MIN_GAIN, f"gain over NDVI > 0.4: {gain:+.3f} (needs >= {MIN_GAIN})")
    check(gain_ci[0] > 0, f"gain 95% range {gain_ci[0]:+.3f} to {gain_ci[1]:+.3f} (must sit above zero)")
    check(crop_new["iou"] > crop_base["iou"],
          f"cropland IoU {crop_base['iou']:.3f} -> {crop_new['iou']:.3f} (must rise)")
    check(crop_new["precision"] >= MIN_CROP_PRECISION,
          f"cropland precision {crop_new['precision']:.3f} (needs >= {MIN_CROP_PRECISION})")
    dw_gain = dw_new["iou"] - dw_base["iou"]
    check(dw_gain > 0, f"gain against Dynamic World {dw_gain:+.3f} (must be above zero)")
    return {"ship": ship, "reasons": reasons}


def choose(decision_a, iou_a, decision_b=None, iou_b=None):
    """Which variant ships: 'B', 'A' or None."""
    if decision_b and decision_b["ship"] and (not decision_a["ship"] or iou_b >= iou_a + HEIGHT_MUST_ADD):
        return "B"
    if decision_a["ship"]:
        return "A"
    return None


def validation_block(new, per_district, landscapes, method, decision):
    """The `validation` entry for surface.ANALYSES['green_cover'] if it ships."""
    worst = min(per_district.items(), key=lambda kv: kv[1]["iou"]) if per_district else None
    grade = "good" if new["iou"] >= 0.6 else "moderate" if new["iou"] >= 0.4 else "poor"
    return {
        "iou": round(new["iou"], 3),
        "precision": round(new["precision"], 3),
        "recall": round(new["recall"], 3),
        "reliability": grade,
        "method": method,
        "per_district": {k: round(v["iou"], 3) for k, v in sorted(per_district.items())},
        "caveat": (
            "Trained on nine districts in seven states and scored on seven districts in "
            "seven other states, against ESA WorldCover 2021 trees. "
            + (f"Weakest held-out district: {worst[0]} ({landscapes.get(worst[0], '')}, "
               f"IoU {worst[1]['iou']:.2f}). " if worst else "")
            + "Needs a year of imagery, so it answers for a calendar year, not a week."
        ),
        "shipped": bool(decision["ship"]),
    }


def plain(value):
    """Strict JSON: numpy scalars to Python, NaN and inf to None."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, np.bool_):
        return bool(value)
    return value
