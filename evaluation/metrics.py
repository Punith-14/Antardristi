"""
Segmentation metrics for flood/water masks.

Deliberately pure numpy so the same code runs in Colab, in Kaggle, and in the
backend. No torch, no Earth Engine.

Convention, matching Sen1Floods11 labels:
    -1  no data / not labelled  -> excluded from every metric
     0  not water
     1  water
"""

import numpy as np

NODATA = -1


def confusion(pred, truth, ignore_value=NODATA):
    """Counts of tp, fp, fn, tn over labelled pixels only.

    Excluding nodata matters: Sen1Floods11 chips contain large unlabelled
    regions, and counting them as 'not water' inflates every metric that has
    true negatives in it.
    """
    pred = np.asarray(pred)
    truth = np.asarray(truth)

    valid = truth != ignore_value
    p = pred[valid].astype(bool)
    t = truth[valid].astype(bool)

    tp = int(np.sum(p & t))
    fp = int(np.sum(p & ~t))
    fn = int(np.sum(~p & t))
    tn = int(np.sum(~p & ~t))
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "valid_pixels": int(valid.sum())}


def scores_from_counts(counts):
    tp, fp, fn, tn = counts["tp"], counts["fp"], counts["fn"], counts["tn"]

    def safe(numerator, denominator):
        return float(numerator / denominator) if denominator else 0.0

    precision = safe(tp, tp + fp)
    recall = safe(tp, tp + fn)

    return {
        "iou": safe(tp, tp + fp + fn),
        "precision": precision,
        "recall": recall,
        "f1": safe(2 * precision * recall, precision + recall),
        "accuracy": safe(tp + tn, tp + fp + fn + tn),
        # How much water there actually is. Water is usually a small minority of
        # pixels, so accuracy looks excellent for a model that predicts nothing.
        "water_prevalence": safe(tp + fn, tp + fp + fn + tn),
        **counts,
    }


def evaluate(pred, truth, ignore_value=NODATA):
    return scores_from_counts(confusion(pred, truth, ignore_value))


def aggregate(per_chip):
    """Two ways of averaging, and they disagree for good reasons.

    micro : pool all pixels, then score. Dominated by large chips and by chips
            with a lot of water. This is the honest headline number.
    macro : score each chip, then average. Every chip counts equally, so a
            handful of tiny dry chips can drag it down.

    Report both. A wide gap between them means performance depends strongly on
    how much water is present, which is itself a finding.
    """
    if not per_chip:
        return {}

    pooled = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "valid_pixels": 0}
    for chip in per_chip:
        for key in pooled:
            pooled[key] += chip.get(key, 0)

    micro = scores_from_counts(
        {k: pooled[k] for k in ("tp", "fp", "fn", "tn")}
    )
    micro["valid_pixels"] = pooled["valid_pixels"]

    macro = {}
    for key in ("iou", "precision", "recall", "f1", "accuracy"):
        values = [c[key] for c in per_chip if c.get("valid_pixels", 0) > 0]
        macro[key] = float(np.mean(values)) if values else 0.0

    return {"micro": micro, "macro": macro, "chips": len(per_chip)}


def format_table(results, title="Results"):
    """results: {method_name: aggregate(...)}"""
    header = (
        f"{'method':<28}{'IoU':>8}{'F1':>8}{'prec':>8}{'recall':>8}{'chips':>8}"
    )
    lines = [title, "=" * len(header), header, "-" * len(header)]

    for name, agg in results.items():
        if not agg:
            continue
        m = agg["micro"]
        lines.append(
            f"{name:<28}{m['iou']:>8.3f}{m['f1']:>8.3f}"
            f"{m['precision']:>8.3f}{m['recall']:>8.3f}{agg['chips']:>8}"
        )

    lines.append("")
    lines.append("(micro-averaged: all pixels pooled)")
    return "\n".join(lines)
