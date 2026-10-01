"""
Accuracy per flood event, and for India with an uncertainty range.

Every figure so far is an average over eleven flood events on six continents.
An Indian disaster official reading "IoU 0.609" deserves to know what the rule
scores on Indian floods - and how sure that number is, because Sen1Floods11
has only one Indian event and the held-out share of it is small.

Two rules keep the India figure honest:

1. **Only chips the threshold never saw.** The 200 m threshold was chosen on
   the train split, which includes Indian chips. Scoring the rule on those
   would flatter it. The India figure uses valid + test chips only; the
   all-chip figure is reported beside it, labelled.

2. **A range, not just a number.** With a few dozen chips, one unusual chip
   moves the score. `bootstrap_iou` resamples whole chips (not pixels -
   pixels within a chip are not independent) and reports the 2.5th and 97.5th
   percentiles of pooled IoU.
"""

import numpy as np

HELD_OUT = ("valid", "test")


def event_of(key):
    """'Sri-Lanka_249079' -> 'Sri-Lanka'. Sen1Floods11 names chips EVENT_ID."""
    return key.rsplit("_", 1)[0]


def _pooled(tp, fp, fn):
    total = tp + fp + fn
    return tp / total if total else 0.0


def pooled_scores(chip_counts):
    """IoU, precision, recall from a list of (tp, fp, fn) per chip."""
    tp = sum(c[0] for c in chip_counts)
    fp = sum(c[1] for c in chip_counts)
    fn = sum(c[2] for c in chip_counts)
    return {
        "iou": _pooled(tp, fp, fn),
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "chips": len(chip_counts),
    }


def bootstrap_iou(chip_counts, n=2000, seed=0, level=0.95):
    """(low, high) range for pooled IoU by resampling chips with replacement.

    Chips, not pixels, are the unit: neighbouring pixels share a flood, a
    scene and a labeller, so treating 260,000 pixels per chip as independent
    would make the range absurdly narrow. Returns (nan, nan) with fewer than
    two chips - one chip has no spread to resample.
    """
    counts = np.asarray(chip_counts, dtype=np.float64)
    if counts.ndim != 2 or len(counts) < 2:
        return float("nan"), float("nan")

    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(counts), size=(n, len(counts)))
    sums = counts[picks].sum(axis=1)                  # (n, 3): tp, fp, fn
    denom = sums.sum(axis=1)
    iou = np.divide(sums[:, 0], denom, out=np.zeros(n), where=denom > 0)

    tail = (1 - level) / 2
    return float(np.quantile(iou, tail)), float(np.quantile(iou, 1 - tail))


def by_event(chips, counts_of):
    """{event: pooled scores} over the chips given. `counts_of(chip)` -> (tp, fp, fn)."""
    grouped = {}
    for chip in chips:
        grouped.setdefault(event_of(chip["key"]), []).append(counts_of(chip))
    return {event: pooled_scores(c) for event, c in sorted(grouped.items())}


def india_summary(chips, counts_of, event="India", n=2000, seed=0):
    """The figure an Indian user is shown, and the one beside it.

    Returns {"held_out": {...scores, "ci95": [lo, hi]}, "all": {...scores}}.
    """
    india = [c for c in chips if event_of(c["key"]) == event]
    held = [c for c in india if c.get("split") in HELD_OUT]

    held_counts = [counts_of(c) for c in held]
    all_counts = [counts_of(c) for c in india]

    held_scores = pooled_scores(held_counts)
    held_scores["ci95"] = list(bootstrap_iou(held_counts, n=n, seed=seed))
    return {"held_out": held_scores, "all": pooled_scores(all_counts)}
