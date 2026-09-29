"""
Land cover classification with the trained Random Forest.

Two things make this awkward, and both are handled here rather than left to the
caller.

The model runs INSIDE Earth Engine, not in Python. Pulling a state's pixels down
to run scikit-learn locally would be gigabytes per query. Instead each tree is
serialised into Earth Engine's own decision-tree format and evaluated
server-side, so only the summary statistics come back - the same pattern the
rest of the pipeline uses.

And it needs RADAR. The optical-only model scored 0.238 IoU for built-up, below
the threshold rule it was meant to replace, because concrete and sand are
spectrally almost identical. Adding Sentinel-1 raised it to 0.434, with VH
becoming the most important of fifteen features. So inference must fetch two
satellite collections, not one.
"""

import json
import pickle
from pathlib import Path

import ee

from geo import indices

from core import paths

MODELS = paths.MODELS
MODEL_NAME = "landcover_rf_v1"

CLASS_IDS = {"water": 1, "vegetation": 2, "built-up": 3, "bare": 4}
CLASS_NAMES = {v: k for k, v in CLASS_IDS.items()}

OPTICAL_BANDS = ["B2", "B3", "B4", "B8", "B11", "B12"]
INDEX_NAMES = ["ndvi", "ndwi", "mndwi", "ndbi", "ndmi", "bsi"]
SAR_BANDS = ["VV", "VH"]

_cache = {}

# Earth Engine evaluates the forest as text, so the whole thing is uploaded with
# the request. A model trained with max_depth=None grew to 610,102 nodes and
# 37.8 MB, which Earth Engine will not accept. Refuse early with an instruction
# rather than hanging on an upload that cannot succeed.
MAX_SERIALISED_KB = 4096


class ModelUnavailable(RuntimeError):
    """The classifier cannot be used. Callers fall back to the index rule."""


# ------------------------------------------------------------------ loading

def metadata():
    path = MODELS / f"{MODEL_NAME}.json"
    if not path.exists():
        raise ModelUnavailable(
            f"{path.name} not found. Run train_landcover.py first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load():
    """The trained model, with the checks that stop it failing silently."""
    if "model" in _cache:
        return _cache["model"], _cache["meta"]

    meta = metadata()
    path = MODELS / f"{MODEL_NAME}.pkl"
    if not path.exists():
        raise ModelUnavailable(f"{path.name} not found.")

    try:
        import sklearn
    except ImportError as exc:
        raise ModelUnavailable("scikit-learn is not installed.") from exc

    trained_with = meta.get("sklearn_version")
    if trained_with and trained_with != sklearn.__version__:
        # Not fatal, but it must be visible: pickles can load under a different
        # version and then behave differently, which is worse than failing.
        print(
            f"  ! {MODEL_NAME} was trained with scikit-learn {trained_with}, "
            f"running {sklearn.__version__}. Pin the version."
        )

    with path.open("rb") as fh:
        model = pickle.load(fh)

    features = meta["python_features_in_order"]
    if getattr(model, "n_features_in_", len(features)) != len(features):
        raise ModelUnavailable(
            f"Model expects {model.n_features_in_} features but metadata lists "
            f"{len(features)}. Retrain rather than guessing the order."
        )

    _cache["model"] = model
    _cache["meta"] = meta
    return model, meta


def is_available():
    try:
        load()
        return True
    except ModelUnavailable:
        return False


def scores():
    """Measured IoU per class, for the evidence record.

    train_landcover.py writes a dict keyed by class; the notebook writes a list
    of rows. Accept both rather than depending on which produced the file.
    """
    results = metadata().get("results")
    out = {}

    if isinstance(results, dict):
        for name, entry in results.items():
            rf = entry.get("rf", {})
            out[name] = {
                "iou": rf.get("iou"),
                "precision": rf.get("precision"),
                "recall": rf.get("recall"),
                "beats_index_rule": entry.get("change", 0) > 0,
            }
    elif isinstance(results, list):
        for row in results:
            name = row.get("class")
            if name:
                out[name] = {
                    "iou": row.get("RF IoU"),
                    "precision": row.get("RF prec"),
                    "recall": row.get("RF rec"),
                    "beats_index_rule": row.get("change", 0) > 0,
                }

    return out


# ---------------------------------------------------------------- features

def sar_composite(region, start_date, end_date):
    """Sentinel-1 VV/VH, matching how the training samples were built."""
    collection = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .select(SAR_BANDS)
    )

    if collection.size().getInfo() == 0:
        raise ModelUnavailable(
            f"No Sentinel-1 coverage for {start_date} to {end_date}. The "
            "classifier needs radar; the index rule does not."
        )

    smoothed = (
        collection.median().clip(region)
        .focal_median(30, "circle", "meters")
        .rename(SAR_BANDS)
    )
    ratio = smoothed.select("VV").subtract(smoothed.select("VH")).rename("vv_vh_ratio")
    return smoothed.addBands(ratio)


def feature_stack(optical_composite, region, start_date, end_date):
    """Every feature the model was trained on, in the recorded order."""
    _, meta = load()
    expected = meta["python_features_in_order"]

    stack = optical_composite.select(OPTICAL_BANDS)
    for name in INDEX_NAMES:
        stack = stack.addBands(indices.compute(optical_composite, name))

    if any(f in expected for f in SAR_BANDS):
        stack = stack.addBands(sar_composite(region, start_date, end_date))

    # Order matters more than presence: a model handed its features in the
    # wrong order returns confident nonsense rather than an error.
    return stack.select(expected)


# --------------------------------------------------------------- inference

def _to_earth_engine_classifier(model, features):
    """Serialise the forest into Earth Engine's decision-tree format.

    Earth Engine cannot run scikit-learn, but it can evaluate trees expressed in
    its own string format. Converting lets the model run where the pixels
    already are, instead of moving a state's worth of imagery to Python.

    Cached: the model never changes between requests, and rebuilding 300 trees
    of text per query was pure waste - it dominated the cost of the whole
    analysis.
    """
    if "ee_classifier" in _cache:
        return _cache["ee_classifier"]

    trees = [_tree_to_string(estimator.tree_, features, model.classes_)
             for estimator in model.estimators_]

    total_nodes = sum(e.tree_.node_count for e in model.estimators_)
    size_kb = round(sum(len(t) for t in trees) / 1024, 1)
    _cache["tree_stats"] = {
        "trees": len(trees),
        "total_nodes": total_nodes,
        "serialised_kb": size_kb,
    }

    if size_kb > MAX_SERIALISED_KB:
        raise ModelUnavailable(
            f"The forest serialises to {size_kb:,.0f} KB across {total_nodes:,} "
            f"nodes, above the {MAX_SERIALISED_KB:,} KB Earth Engine will "
            "accept. Retrain with fewer and shallower trees:\n"
            "    python -m scripts.train_landcover --trees 60 --depth 10"
        )

    classifier_object = ee.Classifier.decisionTreeEnsemble(trees)
    _cache["ee_classifier"] = classifier_object
    return classifier_object


def tree_stats():
    """How big the serialised forest is. Large numbers here mean slow queries."""
    if "tree_stats" not in _cache:
        model, meta = load()
        _to_earth_engine_classifier(model, meta["python_features_in_order"])
    return _cache.get("tree_stats", {})


def _tree_to_string(tree, features, classes):
    """One sklearn tree in Earth Engine's rpart-style text format.

        1) root 8400 9999 9999
          2) ndvi<=0.310000 4200 9999 9999
            4) VH<=-18.200000 2100 9999 1 *
            5) VH>-18.200000 2100 9999 3 *
          3) ndvi>0.310000 4200 9999 2 *

    Each line carries the condition that LED TO that node, not the split the
    node itself makes - a subtlety that cost a round of "Error parsing line 2".
    Node ids follow the binary-heap numbering rpart uses: children of n are 2n
    and 2n+1. A trailing * marks a leaf, and only leaves carry a prediction;
    internal nodes use 9999 in that position.
    """
    def prediction(node):
        return int(classes[int(tree.value[node][0].argmax())])

    root_is_leaf = tree.children_left[0] == -1
    if root_is_leaf:
        return f"1) root {int(tree.n_node_samples[0])} 9999 {prediction(0)} *"

    lines = [f"1) root {int(tree.n_node_samples[0])} 9999 9999"]

    def walk(node, node_id, depth):
        left = tree.children_left[node]
        right = tree.children_right[node]

        name = features[tree.feature[node]]
        threshold = tree.threshold[node]
        indent = "  " * depth

        for child, condition, child_id in (
            (left, f"{name}<={threshold:.6f}", node_id * 2),
            (right, f"{name}>{threshold:.6f}", node_id * 2 + 1),
        ):
            samples = int(tree.n_node_samples[child])

            if tree.children_left[child] == -1:          # child is a leaf
                lines.append(
                    f"{indent}{child_id}) {condition} {samples} 9999 "
                    f"{prediction(child)} *"
                )
            else:
                lines.append(
                    f"{indent}{child_id}) {condition} {samples} 9999 9999"
                )
                walk(child, child_id, depth + 1)

    walk(0, 1, 1)
    return "\n".join(lines)


def classify(optical_composite, region, start_date, end_date):
    """Per-pixel land cover. Returns (classified image, info dict)."""
    model, meta = load()
    features = meta["python_features_in_order"]

    stack = feature_stack(optical_composite, region, start_date, end_date)
    classifier = _to_earth_engine_classifier(model, features)

    classified = stack.classify(classifier).rename("land_cover")

    return classified, {
        "model": MODEL_NAME,
        "algorithm": meta.get("algorithm"),
        "trained_on": meta.get("training_districts"),
        "validated_on": meta.get("holdout_districts"),
        "split": meta.get("split"),
        "features": features,
        "sklearn_version": meta.get("sklearn_version"),
        "scores": scores(),
    }


def class_mask(classified, class_name):
    """Binary mask for one class, so it drops into the existing pipeline."""
    if class_name not in CLASS_IDS:
        raise ValueError(f"Unknown class {class_name!r}. {sorted(CLASS_IDS)}")
    return classified.eq(CLASS_IDS[class_name]).rename(f"{class_name}_mask")


KNOWN_CONFUSIONS = [
    "Bare ground is over-predicted: precision 0.461 against recall 0.898, so "
    "vegetation and built-up pixels are sometimes swept into it.",
    "Trained on nine Indian districts. Landscapes unlike those - the Northeast, "
    "high Himalaya, the Deccan plateau - are outside what the model has seen.",
    "Built-up detection depends on radar. Without Sentinel-1 coverage the "
    "classifier cannot run and the weaker index rule is used instead.",
]
