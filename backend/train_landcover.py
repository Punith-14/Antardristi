"""
Train a land cover classifier and compare it against the thresholds it replaces.

The point of this model is not that it is a model. It is that NDBI scores an
IoU of 0.057 for built-up area - it calls desert sand "buildings" - and we want
to know whether a classifier over the same features does better. So every score
here is reported next to the index rule for the same class, on the same pixels.

SPATIAL SPLIT, NOT RANDOM. Two pixels twenty metres apart are nearly identical,
so a random split puts near-duplicates in both train and test and reports an
accuracy the model has not earned. Whole districts are held out instead: the
model is scored on landscapes it has never seen, which is the situation it
faces in use.

Run:  python train_landcover.py
      python train_landcover.py --holdout Jaisalmer "Bangalore Urban"
"""

import argparse
import json
import pickle
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix

SAMPLES = Path(__file__).resolve().parent.parent / "evaluation" / "landcover_samples.csv"
MODELS = Path(__file__).resolve().parent.parent / "models"

OPTICAL = ["B2", "B3", "B4", "B8", "B11", "B12",
           "ndvi", "ndwi", "mndwi", "ndbi", "ndmi", "bsi"]
RADAR = ["VV", "VH", "vv_vh_ratio"]

# Two feature sets, trained on the same split, so the question "does radar help"
# gets an answer rather than an assumption.
FEATURE_SETS = {
    "optical only": OPTICAL,
    "optical + radar": OPTICAL + RADAR,
}
FEATURES = OPTICAL + RADAR

CLASS_NAMES = {1: "water", 2: "vegetation", 3: "built-up", 4: "bare"}

# The index rules currently in surface.py, with their measured IoU from
# validate_surface.py. These are what the classifier has to beat.
INDEX_RULES = {
    1: ("mndwi", "above", -0.15, 0.466),
    2: ("ndvi", "above", 0.20, 0.888),
    3: ("ndbi", "above", -0.10, 0.057),
    4: ("bsi", "above", 0.15, 0.747),
}

# Districts held out by default: the desert and the city, because those are
# exactly where the index rules disagreed most (NDBI scored 0.001 in Jaisalmer
# and 0.322 in Bangalore). Holding out the easy ones would flatter everything.
DEFAULT_HOLDOUT = ["Jaisalmer", "Bangalore Urban"]


def iou(predicted, actual):
    """Intersection over union for one class, as a binary problem."""
    tp = int(np.sum(predicted & actual))
    fp = int(np.sum(predicted & ~actual))
    fn = int(np.sum(~predicted & actual))
    return tp / (tp + fp + fn) if (tp + fp + fn) else 0.0


def precision_recall(predicted, actual):
    tp = int(np.sum(predicted & actual))
    fp = int(np.sum(predicted & ~actual))
    fn = int(np.sum(~predicted & actual))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return precision, recall


def score_index_rule(frame, class_id):
    """How the current threshold rule does on these same pixels."""
    index, direction, threshold, _ = INDEX_RULES[class_id]
    values = frame[index].to_numpy()
    predicted = values > threshold if direction == "above" else values < threshold
    actual = frame["label"].to_numpy() == class_id
    return iou(predicted, actual), *precision_recall(predicted, actual)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", nargs="*", default=None,
                        help="districts to hold out entirely")
    parser.add_argument("--trees", type=int, default=300)
    parser.add_argument("--depth", type=int, default=None)
    args = parser.parse_args()

    if not SAMPLES.exists():
        print(f"No samples at {SAMPLES}. Run export_training_data.py first.")
        return

    frame = pd.read_csv(SAMPLES)
    holdout = args.holdout if args.holdout is not None else DEFAULT_HOLDOUT
    holdout = [d for d in holdout if d in set(frame["district"])]

    train = frame[~frame["district"].isin(holdout)]
    test = frame[frame["district"].isin(holdout)]

    if test.empty:
        print("Holdout districts not present in the samples.")
        return

    print(f"{len(frame):,} samples from {frame['district'].nunique()} districts")
    print(f"  train  {len(train):>6,}  ({', '.join(sorted(set(train['district'])))})")
    print(f"  test   {len(test):>6,}  ({', '.join(holdout)})")
    print("\nHeld out by district, not at random: adjacent pixels are near-identical,")
    print("so a random split scores the model on data it has effectively seen.\n")

    y_train = train["label"].to_numpy()
    y_test = test["label"].to_numpy()

    available = set(frame.columns)
    sets = {
        name: features for name, features in FEATURE_SETS.items()
        if set(features) <= available
    }
    if not sets:
        print("No usable feature set. Re-run export_training_data.py.")
        return

    # Ablation: same split, same model, different inputs. The optical-only
    # classifier LOST to the threshold on built-up (-0.036), with 58% of
    # built-up predicted as bare. If radar closes that gap, the missing
    # information was never spectral.
    trained = {}
    for name, features in sets.items():
        candidate = RandomForestClassifier(
            n_estimators=args.trees,
            max_depth=args.depth,
            class_weight="balanced",
            n_jobs=-1,
            random_state=42,
        )
        candidate.fit(train[features].to_numpy(), y_train)
        trained[name] = {
            "model": candidate,
            "features": features,
            "predicted": candidate.predict(test[features].to_numpy()),
        }

    if len(trained) > 1:
        print("=" * 72)
        print("DOES RADAR HELP? (IoU per class, same split, same model)")
        print("=" * 72)
        header = f"{'class':<12}{'rule':>8}"
        for name in trained:
            header += f"{name:>18}"
        print(header + f"{'radar gain':>13}")
        print("-" * 72)

        for class_id, class_name in CLASS_NAMES.items():
            actual = y_test == class_id
            if not actual.any():
                continue
            rule_iou = score_index_rule(test, class_id)[0]
            row = f"{class_name:<12}{rule_iou:>8.3f}"
            scores = []
            for name, bundle in trained.items():
                value = iou(bundle["predicted"] == class_id, actual)
                scores.append(value)
                row += f"{value:>18.3f}"
            gain = scores[-1] - scores[0] if len(scores) > 1 else 0.0
            print(row + f"{gain:>+13.3f}")
        print()

    # The full feature set is what ships.
    best_name = "optical + radar" if "optical + radar" in trained else "optical only"
    model = trained[best_name]["model"]
    active_features = trained[best_name]["features"]
    predicted = trained[best_name]["predicted"]

    print("=" * 72)
    print(f"PER CLASS: {best_name} classifier against the index rule it replaces")
    print("=" * 72)
    print(f"{'class':<12}{'RF IoU':>9}{'rule IoU':>10}{'change':>9}"
          f"{'RF prec':>9}{'RF rec':>8}")
    print("-" * 72)

    results = {}
    for class_id, name in CLASS_NAMES.items():
        actual = y_test == class_id
        if not actual.any():
            continue

        rf_mask = predicted == class_id
        rf_iou = iou(rf_mask, actual)
        rf_precision, rf_recall = precision_recall(rf_mask, actual)
        rule_iou, rule_precision, rule_recall = score_index_rule(test, class_id)

        change = rf_iou - rule_iou
        marker = "  <-- was broken" if INDEX_RULES[class_id][3] < 0.1 else ""
        print(f"{name:<12}{rf_iou:>9.3f}{rule_iou:>10.3f}{change:>+9.3f}"
              f"{rf_precision:>9.3f}{rf_recall:>8.3f}{marker}")

        results[name] = {
            "rf": {"iou": round(rf_iou, 4),
                   "precision": round(rf_precision, 4),
                   "recall": round(rf_recall, 4)},
            "index_rule": {"index": INDEX_RULES[class_id][0],
                           "threshold": INDEX_RULES[class_id][2],
                           "iou_on_this_test_set": round(rule_iou, 4),
                           "iou_reported_in_surface_py": INDEX_RULES[class_id][3]},
            "change": round(change, 4),
        }

    print("\n" + "=" * 72)
    print("CONFUSION MATRIX (rows actual, columns predicted)")
    print("=" * 72)
    labels = sorted(CLASS_NAMES)
    matrix = confusion_matrix(y_test, predicted, labels=labels)
    print(f"{'':<12}" + "".join(f"{CLASS_NAMES[l]:>12}" for l in labels))
    for row_label, row in zip(labels, matrix):
        print(f"{CLASS_NAMES[row_label]:<12}" + "".join(f"{v:>12,}" for v in row))

    print("\n" + classification_report(
        y_test, predicted,
        labels=labels,
        target_names=[CLASS_NAMES[l] for l in labels],
        digits=3,
        zero_division=0,
    ))

    print("=" * 72)
    print("WHAT THE MODEL USES")
    print("=" * 72)
    importance = sorted(zip(active_features, model.feature_importances_),
                        key=lambda kv: -kv[1])
    for name, weight in importance:
        bar = "#" * int(weight * 120)
        tag = " (radar)" if name in RADAR else ""
        print(f"  {name:<14}{weight:>7.3f}  {bar}{tag}")

    radar_share = sum(w for n, w in importance if n in RADAR)
    if radar_share:
        print(f"\n  radar accounts for {radar_share:.1%} of the model's decisions")

    # The model is evaluated inside Earth Engine, which means the whole forest
    # is uploaded as text. Size is a deployment constraint, not a detail: an
    # unbounded forest reached 610,102 nodes and 37.8 MB, which Earth Engine
    # will not accept. Report it here so the trade-off is visible while
    # choosing the parameters rather than discovered at inference.
    total_nodes = sum(e.tree_.node_count for e in model.estimators_)
    approx_kb = total_nodes * 60 / 1024

    print("\n" + "=" * 72)
    print("DEPLOYMENT COST")
    print("=" * 72)
    print(f"  trees               {args.trees:>10,}")
    print(f"  max depth           {str(args.depth or 'unlimited'):>10}")
    print(f"  total nodes         {total_nodes:>10,}")
    print(f"  approx serialised   {approx_kb:>10,.0f} KB")

    if approx_kb > 4096:
        print("\n  TOO LARGE for Earth Engine. Retrain smaller, for example:")
        print("    python train_landcover.py --trees 60 --depth 10")
    else:
        print("\n  Within the Earth Engine limit.")

    MODELS.mkdir(parents=True, exist_ok=True)
    model_path = MODELS / "landcover_rf_v1.pkl"
    with model_path.open("wb") as fh:
        pickle.dump(model, fh)

    metadata = {
        "name": "landcover_rf_v1",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "algorithm": "RandomForestClassifier",
        "n_estimators": args.trees,
        "max_depth": args.depth,
        # Pickles are version-fragile: loading under a different scikit-learn
        # can fail, or silently misbehave. Record it and pin it.
        "sklearn_version": sklearn.__version__,
        "feature_set": best_name,
        "total_nodes": int(total_nodes),
        "approx_serialised_kb": round(approx_kb, 1),
        "python_features_in_order": active_features,
        "classes": CLASS_NAMES,
        "training_samples": len(train),
        "training_districts": sorted(set(train["district"])),
        "holdout_districts": holdout,
        "holdout_samples": len(test),
        "split": "spatial: whole districts held out",
        "results": results,
        "source_data": str(SAMPLES.name),
        "note": (
            "Features and order must match backend/indices.py. Scored against "
            "the index thresholds in surface.py on the same held-out pixels."
        ),
    }
    (MODELS / "landcover_rf_v1.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )

    print("=" * 72)
    print(f"Saved {model_path.name} and its metadata to models/")
    print(f"scikit-learn {sklearn.__version__} - pin this, pickles are version-fragile")

    built_up = results.get("built-up")
    if built_up:
        print()
        if built_up["change"] > 0:
            print(f"Built-up: {built_up['index_rule']['iou_on_this_test_set']:.3f} "
                  f"-> {built_up['rf']['iou']:.3f} "
                  f"({built_up['change']:+.3f}). The classifier is worth having.")
        else:
            print(f"Built-up: {built_up['change']:+.3f}. The classifier did NOT beat "
                  "the threshold here. Report that rather than hiding it - a "
                  "negative result on a held-out split is still a result.")


if __name__ == "__main__":
    main()
