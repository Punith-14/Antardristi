"""
The sklearn-to-Earth-Engine tree serialiser.

Earth Engine's parser gives one error line and no context, so the format is
checked here against a tree small enough to verify by hand. The first
implementation wrote each node's own split on its own line instead of the
condition that led to it, and the only symptom was "Error parsing line 2".
"""

import re

import pytest

from detection import classifier

sklearn = pytest.importorskip("sklearn", reason="scikit-learn not installed")
from sklearn.tree import DecisionTreeClassifier  # noqa: E402
import numpy as np  # noqa: E402


FEATURES = ["ndvi", "VH"]


def tiny_tree():
    """A tree whose structure is obvious: two features, four clear groups."""
    x = np.array([
        [0.8, -20.0], [0.9, -21.0],      # vegetation, dark radar
        [0.7, -8.0], [0.75, -7.0],       # vegetation, bright radar
        [0.1, -22.0], [0.05, -23.0],     # bare, dark radar
        [0.1, -6.0], [0.05, -5.0],       # built-up, bright radar
    ])
    y = np.array([2, 2, 2, 2, 4, 4, 3, 3])

    tree = DecisionTreeClassifier(max_depth=2, random_state=0)
    tree.fit(x, y)
    return tree


def lines_of(text):
    return [line.strip() for line in text.split("\n") if line.strip()]


def test_root_line_is_well_formed():
    text = classifier._tree_to_string(tiny_tree().tree_, FEATURES,
                                      tiny_tree().classes_)
    assert lines_of(text)[0].startswith("1) root ")


def test_every_line_matches_the_expected_grammar():
    """node_id) condition samples deviance prediction [*]"""
    model = tiny_tree()
    text = classifier._tree_to_string(model.tree_, FEATURES, model.classes_)

    pattern = re.compile(
        r"^\d+\) (root|\S+(<=|>)-?\d+\.\d+) \d+ 9999 (\d+|9999)( \*)?$"
    )
    for line in lines_of(text):
        assert pattern.match(line), f"bad line: {line!r}"


def test_only_leaves_carry_a_prediction():
    model = tiny_tree()
    text = classifier._tree_to_string(model.tree_, FEATURES, model.classes_)

    for line in lines_of(text):
        is_leaf = line.endswith(" *")
        prediction = line.split()[-2] if is_leaf else line.split()[-1]
        if is_leaf:
            assert prediction != "9999", f"leaf without a prediction: {line}"
        else:
            assert prediction == "9999", f"internal node with a prediction: {line}"


def test_predictions_are_real_class_ids():
    model = tiny_tree()
    text = classifier._tree_to_string(model.tree_, FEATURES, model.classes_)

    valid = set(classifier.CLASS_NAMES)
    for line in lines_of(text):
        if line.endswith(" *"):
            assert int(line.split()[-2]) in valid


def test_node_ids_follow_the_binary_heap_numbering():
    """rpart numbering: children of n are 2n and 2n+1. Earth Engine relies on
    it to rebuild the structure, so a stray id silently reshapes the tree."""
    model = tiny_tree()
    text = classifier._tree_to_string(model.tree_, FEATURES, model.classes_)

    ids = [int(line.split(")")[0]) for line in lines_of(text)]
    assert ids[0] == 1
    for node_id in ids[1:]:
        assert node_id // 2 in ids, f"node {node_id} has no parent"


def test_conditions_are_the_path_not_the_split():
    """The bug that produced 'Error parsing line 2': each line must carry the
    condition that led to it, so a node id never appears twice."""
    model = tiny_tree()
    text = classifier._tree_to_string(model.tree_, FEATURES, model.classes_)

    ids = [int(line.split(")")[0]) for line in lines_of(text)]
    assert len(ids) == len(set(ids)), "a node id appears more than once"


def test_siblings_split_on_the_same_feature_in_both_directions():
    model = tiny_tree()
    text = classifier._tree_to_string(model.tree_, FEATURES, model.classes_)

    by_id = {int(l.split(")")[0]): l for l in lines_of(text)}
    for node_id, line in by_id.items():
        sibling = node_id + 1 if node_id % 2 == 0 else node_id - 1
        if node_id == 1 or sibling not in by_id:
            continue
        condition = line.split()[1]
        other = by_id[sibling].split()[1]
        assert condition.split("<=")[0].split(">")[0] == \
            other.split("<=")[0].split(">")[0], "siblings split on different features"


def test_only_known_feature_names_appear():
    model = tiny_tree()
    text = classifier._tree_to_string(model.tree_, FEATURES, model.classes_)

    for line in lines_of(text):
        condition = line.split()[1]
        if condition == "root":
            continue
        name = re.split(r"<=|>", condition)[0]
        assert name in FEATURES, f"unknown feature {name!r}"


def test_a_single_leaf_tree_is_still_valid():
    """One class in the training data: the root is the only node."""
    x = np.array([[0.5, -10.0], [0.6, -11.0]])
    y = np.array([2, 2])
    stump = DecisionTreeClassifier(max_depth=2).fit(x, y)

    text = classifier._tree_to_string(stump.tree_, FEATURES, stump.classes_)
    assert text.startswith("1) root")
    assert text.rstrip().endswith("*")


def test_serialised_size_is_recorded_for_the_deployment_guard():
    """Earth Engine uploads the whole forest as text, so size is a hard
    constraint: an unbounded forest reached 37.8 MB and was rejected."""
    assert classifier.MAX_SERIALISED_KB > 0
