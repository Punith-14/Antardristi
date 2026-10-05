"""
Crop stress: does the satellite see the droughts India declared? (notebook 11)

Today's crop-stress rule thresholds NDMI and has never been checked against
anything: there is no pixel-level "stress" map to score it on. But there is a
district-level record. In the 2015 kharif season, after a monsoon 14% below
normal, 266 districts in 11 states were declared drought-affected. A method
that measures agricultural drought should flag those districts, and should
NOT flag districts in states that declared nothing.

The method is the one India's Manual for Drought Management (2016) names:
Vegetation Condition Index (Kogan 1995) on NDVI and on NDWI, the worse of the
two, read as 60-100 normal, 40-60 moderate, 0-40 severe. Computed here on
MODIS over MODIS cropland, August-October, each month against the same month
in twenty other years.

Everything the decision uses is in this file and fixed before the run.
"""

import math

import numpy as np

YEAR = 2015                   # the declared drought
NORMAL_YEAR = 2019            # monsoon 10% above normal: few districts should flag

SEVERE = 40                   # Drought Manual 2016: 0-40 severe
MODERATE = 60                 # 40-60 moderate, 60-100 normal

# ------------------------------------------------------------ the decision rule

MIN_POD = 0.70                # share of declared districts flagged
MAX_FAR = 0.30                # share of flags that fall on undeclared districts
MAX_NORMAL_YEAR_FLAGGED = 0.20
MIN_CROP_SHARE = 0.10         # less cropland than this and a district is not scored
MAX_UNRESOLVED = 0.20         # more names than this unmatched and the run stops

DECISION_RULE = (
    f"A district is flagged when the worse of its season (Aug-Oct) cropland VCI on "
    f"NDVI and on NDWI is below {SEVERE}. Ship only if, in {YEAR}: at least "
    f"{MIN_POD:.0%} of declared districts are flagged (POD) and at most {MAX_FAR:.0%} "
    f"of flags fall on districts in states that declared nothing (FAR); and in "
    f"{NORMAL_YEAR} at most {MAX_NORMAL_YEAR_FLAGGED:.0%} of the same districts are "
    f"flagged. Districts with under {MIN_CROP_SHARE:.0%} cropland are left out of "
    f"both lists."
)

# ------------------------------------------------------------ reference lists

# Declared in kharif 2015. Only districts declared WHOLE (or nearly) are listed;
# a district with a few drought mandals is left out of both lists, since a
# district average cannot be expected to call it either way.
DECLARED_2015 = [
    # Maharashtra: drought-like conditions declared in every Marathwada village.
    *({"state": "Maharashtra", "district": d, "aliases": a,
       "source": "Maharashtra, Oct 2015: all 8,522 Marathwada villages"}
      for d, a in [("Aurangabad", []), ("Jalna", []), ("Beed", ["Bid", "Bhir"]),
                   ("Latur", []), ("Osmanabad", ["Dharashiv"]), ("Nanded", []),
                   ("Parbhani", []), ("Hingoli", [])]),
    # Karnataka: 27 of 30 districts declared; the 12 of North Interior Karnataka
    # were the worst hit and are named in reporting.
    *({"state": "Karnataka", "district": d, "aliases": a,
       "source": "Karnataka, 2015: 27 of 30 districts; North Interior Karnataka worst"}
      for d, a in [("Bagalkot", ["Bagalkote"]), ("Vijayapura", ["Bijapur"]),
                   ("Raichur", []), ("Bidar", []), ("Yadgir", ["Yadgiri"]),
                   ("Gadag", []), ("Ballari", ["Bellary"]), ("Koppal", []),
                   ("Belagavi", ["Belgaum"]), ("Haveri", []), ("Dharwad", []),
                   ("Kalaburagi", ["Gulbarga"])]),
    # Uttar Pradesh: 50 of 75 districts declared, Bundelkhand among them.
    *({"state": "Uttar Pradesh", "district": d, "aliases": a,
       "source": "Uttar Pradesh, 2015: 50 of 75 districts incl. Bundelkhand"}
      for d, a in [("Jhansi", []), ("Jalaun", []), ("Lalitpur", []),
                   ("Chitrakoot", ["Chitrakut"]), ("Banda", []), ("Hamirpur", []),
                   ("Mahoba", [])]),
    # Telangana, 24 Nov 2015: 231 mandals in 7 districts. These four had all
    # or nearly all their mandals declared.
    *({"state": "Telangana", "district": d, "aliases": a,
       "source": "Telangana, 24 Nov 2015: 231 mandals"}
      for d, a in [("Mahbubnagar", ["Mahabubnagar"]), ("Medak", []),
                   ("Nizamabad", []), ("Ranga Reddy", ["Rangareddy", "Rangareddi"])]),
]

# Cropland districts in states that declared no drought in 2015-16. Cotton
# districts hit by the 2015 whitefly attack (Bathinda, Mansa, Sirsa, Hisar) are
# avoided: that was real crop damage, but not drought.
NOT_DECLARED_2015 = [
    {"state": "Punjab", "district": "Ludhiana"},
    {"state": "Punjab", "district": "Sangrur"},
    {"state": "Punjab", "district": "Patiala"},
    {"state": "Haryana", "district": "Karnal"},
    {"state": "Haryana", "district": "Kurukshetra"},
    {"state": "Haryana", "district": "Kaithal"},
    {"state": "West Bengal", "district": "Barddhaman", "aliases": ["Bardhaman", "Burdwan"]},
    {"state": "West Bengal", "district": "Nadia"},
    {"state": "West Bengal", "district": "Murshidabad"},
    {"state": "West Bengal", "district": "Birbhum"},
    {"state": "Assam", "district": "Nagaon", "aliases": ["Nowgong"]},
    {"state": "Assam", "district": "Barpeta"},
    {"state": "Assam", "district": "Sonitpur"},
    {"state": "Kerala", "district": "Palakkad", "aliases": ["Palghat"]},
]

# Share of each state's districts declared in 2015-16 (Factly, from Ministry of
# Agriculture replies). Used for a secondary, state-level check only.
DECLARED_SHARE_2015 = {
    "Chhattisgarh": 0.93, "Karnataka": 0.93, "Jharkhand": 0.92, "Odisha": 0.90,
    "Madhya Pradesh": 0.90, "Maharashtra": 0.78, "Andhra Pradesh": 0.77,
    "Telangana": 0.70, "Uttar Pradesh": 0.67, "Rajasthan": 0.58, "Gujarat": 0.15,
    "Punjab": 0.0, "Haryana": 0.0, "West Bengal": 0.0, "Assam": 0.0, "Kerala": 0.0,
    "Bihar": 0.0, "Tamil Nadu": 0.0,
}


# ------------------------------------------------------------ the method

def worse_of_two(vci_ndvi, vci_ndwi):
    """The lower VCI, as the manual combines its indicators; one may be missing."""
    a = np.asarray(vci_ndvi, float)
    b = np.asarray(vci_ndwi, float)
    return np.where(np.isnan(a), b, np.where(np.isnan(b), a, np.minimum(a, b)))


def category(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "unknown"
    return "severe" if v < SEVERE else "moderate" if v < MODERATE else "normal"


def flagged(combined):
    return np.asarray(combined, float) < SEVERE


def contingency(flags_positive, flags_negative):
    """Hits, misses and false alarms; POD and FAR as the drought literature uses them."""
    hits = int(np.sum(flags_positive))
    misses = int(len(flags_positive) - hits)
    false_alarms = int(np.sum(flags_negative))
    correct_negatives = int(len(flags_negative) - false_alarms)
    pod = hits / (hits + misses) if hits + misses else 0.0
    far = false_alarms / (hits + false_alarms) if hits + false_alarms else 0.0
    total = hits + misses + false_alarms + correct_negatives
    return {"hits": hits, "misses": misses, "false_alarms": false_alarms,
            "correct_negatives": correct_negatives, "pod": pod, "far": far,
            "accuracy": (hits + correct_negatives) / total if total else 0.0}


def _ranks(values):
    values = np.asarray(values, float)
    order = values.argsort(kind="mergesort")
    ranks = np.empty(len(values))
    ranks[order] = np.arange(len(values), dtype=float)
    for v in np.unique(values):                 # average ties
        tie = values == v
        ranks[tie] = ranks[tie].mean()
    return ranks


def spearman(x, y):
    if len(x) < 3:
        return float("nan")
    rx, ry = _ranks(x), _ranks(y)
    rx, ry = rx - rx.mean(), ry - ry.mean()
    denom = math.sqrt(float(np.sum(rx * rx) * np.sum(ry * ry)))
    return float(np.sum(rx * ry) / denom) if denom else float("nan")


def state_shares(rows, min_crop_share=MIN_CROP_SHARE):
    """{state: share of its (cropland) districts flagged}; rows: dicts with
    state, crop_share, combined."""
    tally = {}
    for r in rows:
        if r["crop_share"] is None or r["crop_share"] < min_crop_share or r["combined"] is None:
            continue
        if isinstance(r["combined"], float) and math.isnan(r["combined"]):
            continue
        n, k = tally.get(r["state"], (0, 0))
        tally[r["state"]] = (n + 1, k + int(r["combined"] < SEVERE))
    return {s: k / n for s, (n, k) in tally.items() if n}


def state_pairs(shares, declared=None, gaul_names=None):
    """(state, declared share, flagged share) for states present in both.

    shares: {GAUL state name: flagged share}. gaul_names: {listed state: [GAUL
    names]} for renamed states. Andhra Pradesh and Telangana are left out:
    GAUL 2015 keeps them as one state, but they declared separately.
    """
    declared = DECLARED_SHARE_2015 if declared is None else declared
    gaul_names = gaul_names or {}
    out = []
    for state, share in sorted(declared.items()):
        if state in ("Andhra Pradesh", "Telangana"):
            continue
        for name in [state, *gaul_names.get(state, [])]:
            if name in shares:
                out.append((state, share, shares[name]))
                break
    return out


def decide(score_year, normal_flagged_share, unresolved_share):
    reasons, ship = [], True

    def check(ok, text):
        nonlocal ship
        ship = ship and ok
        reasons.append(("PASS " if ok else "FAIL ") + text)

    check(unresolved_share <= MAX_UNRESOLVED,
          f"{unresolved_share:.0%} of listed districts unmatched in GAUL (allowed {MAX_UNRESOLVED:.0%})")
    check(score_year["pod"] >= MIN_POD,
          f"{YEAR}: POD {score_year['pod']:.2f} (needs >= {MIN_POD})")
    check(score_year["far"] <= MAX_FAR,
          f"{YEAR}: FAR {score_year['far']:.2f} (needs <= {MAX_FAR})")
    check(normal_flagged_share <= MAX_NORMAL_YEAR_FLAGGED,
          f"{NORMAL_YEAR}: {normal_flagged_share:.0%} of districts flagged "
          f"(allowed {MAX_NORMAL_YEAR_FLAGGED:.0%})")
    return {"ship": ship, "reasons": reasons}


def validation_block(score_year, normal_flagged_share, decision):
    """The `validation` entry for surface.ANALYSES['crop_stress'] if it ships."""
    return {
        "reliability": "moderate" if decision["ship"] else "unvalidated",
        "method": "Vegetation Condition Index on MODIS NDVI and NDWI, worse of the two "
                  "(Manual for Drought Management 2016)",
        "pod": round(score_year["pod"], 3),
        "far": round(score_year["far"], 3),
        "accuracy": round(score_year["accuracy"], 3),
        "normal_year_flagged": round(normal_flagged_share, 3),
        "caveat": (
            f"Checked at district level against the 2015 kharif drought declarations "
            f"(flagged {score_year['hits']} of {score_year['hits'] + score_year['misses']} "
            f"declared districts) and against {NORMAL_YEAR}, a wet year. It compares a "
            f"season with the same months in twenty other years, so it says 'worse "
            f"than usual here', not how much yield was lost. MODIS pixels are 250-500 m: "
            f"read district and block patterns, not single fields."
        ),
        "shipped": bool(decision["ship"]),
    }


def plain(value):
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, np.bool_):
        return bool(value)
    return value
