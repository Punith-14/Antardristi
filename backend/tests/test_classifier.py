"""
The trained classifier: does it load, and does what the system claims about it
match what was actually measured?

These are the tests that stop the model quietly becoming a lie - a stale pickle,
a reordered feature list, or a reliability figure someone updated in one file
and not the other.
"""

import json
import pickle
from pathlib import Path

import pytest

import classifier
from surface import ANALYSES

MODELS = Path(__file__).resolve().parent.parent.parent / "models"
METADATA = MODELS / f"{classifier.MODEL_NAME}.json"
PICKLE = MODELS / f"{classifier.MODEL_NAME}.pkl"

needs_model = pytest.mark.skipif(
    not METADATA.exists(), reason="model not trained yet"
)

try:
    import sklearn

    HAS_SKLEARN = True
except ImportError:                       # pragma: no cover
    sklearn = None
    HAS_SKLEARN = False

# Loading the pickle needs scikit-learn. Everything that only reads metadata
# does not, and stays useful in an environment without it.
needs_sklearn = pytest.mark.skipif(
    not HAS_SKLEARN, reason="scikit-learn not installed"
)


# ----------------------------------------------------------------- metadata

@needs_model
def test_metadata_records_what_is_needed_to_reproduce():
    meta = classifier.metadata()
    for key in ("sklearn_version", "python_features_in_order", "classes",
                "training_districts", "holdout_districts", "split"):
        assert key in meta, f"metadata is missing {key}"


@needs_model
def test_validation_was_a_spatial_split():
    """A random split scores the model on near-duplicate pixels it has
    effectively seen. If this ever says otherwise, the numbers are inflated."""
    meta = classifier.metadata()
    assert "district" in meta["split"].lower()

    train = set(meta["training_districts"])
    holdout = set(meta["holdout_districts"])
    assert holdout, "nothing was held out"
    assert not (train & holdout), "a district appears in both train and test"


@needs_model
def test_holdout_includes_the_hard_districts():
    """NDBI scored 0.001 in Jaisalmer and 0.322 in Bangalore. Holding out the
    easy districts instead would flatter every number."""
    holdout = set(classifier.metadata()["holdout_districts"])
    assert "Jaisalmer" in holdout or "Bangalore Urban" in holdout


@needs_model
def test_feature_order_matches_what_inference_builds():
    """A model handed its features in the wrong order returns confident
    nonsense rather than an error, so the order is asserted, not assumed."""
    features = classifier.metadata()["python_features_in_order"]

    expected = (classifier.OPTICAL_BANDS + classifier.INDEX_NAMES
                + classifier.SAR_BANDS + ["vv_vh_ratio"])
    assert features == expected, (
        "feature order in the model does not match what feature_stack builds"
    )


@needs_model
def test_every_index_feature_exists_in_indices_module():
    import indices

    for name in classifier.INDEX_NAMES:
        assert name in indices.INDEX_FUNCTIONS, f"{name} is not computable"


# -------------------------------------------------------------------- model

@needs_model
@needs_sklearn
@pytest.mark.skipif(not PICKLE.exists(), reason="pickle not present")
def test_model_loads_and_expects_the_right_feature_count():
    model, meta = classifier.load()
    assert model.n_features_in_ == len(meta["python_features_in_order"])


@needs_model
@needs_sklearn
@pytest.mark.skipif(not PICKLE.exists(), reason="pickle not present")
def test_model_predicts_the_four_classes_we_use():
    model, _ = classifier.load()
    assert set(model.classes_) <= set(classifier.CLASS_NAMES)


@needs_model
@needs_sklearn
@pytest.mark.skipif(not PICKLE.exists(), reason="pickle not present")
def test_pinned_sklearn_matches_the_training_version():
    """Pickles are version-fragile: a mismatch can load and then behave
    differently, which is worse than failing outright."""
    trained_with = classifier.metadata()["sklearn_version"]
    if trained_with != sklearn.__version__:
        pytest.fail(
            f"model trained with scikit-learn {trained_with}, environment has "
            f"{sklearn.__version__}. Pin it in requirements.txt or retrain."
        )


@needs_model
def test_requirements_pins_the_training_version():
    requirements = (Path(__file__).resolve().parent.parent / "requirements.txt")
    trained_with = classifier.metadata()["sklearn_version"]
    assert f"scikit-learn=={trained_with}" in requirements.read_text(encoding="utf-8"), (
        "requirements.txt must pin the exact scikit-learn the model was "
        "trained with"
    )


# -------------------------------------------------------- claims vs measured

@needs_model
def test_surface_claims_match_the_measured_scores():
    """surface.py tells users how accurate built-up detection is. That number
    must be the one the model actually scored."""
    measured = classifier.scores()
    config = ANALYSES["built_up"]

    if not config.get("classifier_class"):
        pytest.skip("built_up is not wired to the classifier")

    claimed = config["validation"]
    actual = measured[config["classifier_class"]]

    assert claimed["iou"] == pytest.approx(actual["iou"], abs=0.005)
    assert claimed["precision"] == pytest.approx(actual["precision"], abs=0.005)
    assert claimed["recall"] == pytest.approx(actual["recall"], abs=0.005)


@needs_model
def test_the_classifier_actually_beats_the_rule_it_replaced():
    """If it stops beating the threshold, it should not be the default."""
    for name, config in ANALYSES.items():
        target = config.get("classifier_class")
        if not target:
            continue
        assert classifier.scores()[target]["beats_index_rule"], (
            f"{name} prefers the classifier, but it scores worse than the index"
        )


@needs_model
def test_a_classifier_backed_analysis_documents_its_fallback():
    """When radar is missing the system silently drops to a much weaker method.
    The response must be able to say so."""
    for name, config in ANALYSES.items():
        if not config.get("classifier_class"):
            continue
        fallback = config["validation"].get("fallback")
        assert fallback, f"{name} has no documented fallback"
        assert fallback["iou"] < config["validation"]["iou"], (
            "the fallback should be the weaker method"
        )


@needs_model
def test_known_confusions_name_the_real_weaknesses():
    text = " ".join(classifier.KNOWN_CONFUSIONS).lower()
    assert "bare" in text          # precision 0.461, over-predicted
    assert "radar" in text         # the hard dependency
    assert "district" in text      # limited training geography


# ------------------------------------------------------------- unavailable

def test_missing_model_raises_rather_than_returning_nothing(monkeypatch, tmp_path):
    """A missing model must be an explicit failure the caller can catch and
    fall back from, never a silent empty result."""
    monkeypatch.setattr(classifier, "MODELS", tmp_path)
    classifier._cache.clear()

    with pytest.raises(classifier.ModelUnavailable):
        classifier.metadata()

    assert classifier.is_available() is False
    classifier._cache.clear()


def test_class_mask_rejects_an_unknown_class():
    with pytest.raises(ValueError, match="Unknown class"):
        classifier.class_mask(None, "volcano")
