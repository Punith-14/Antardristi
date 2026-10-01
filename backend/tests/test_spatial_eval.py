"""
Comparable scoring, the rule as it runs, and region growing (evaluation/spatial.py).

Each function here decides what a reported number means, so each is tested
on small arrays whose right answer can be worked out by hand.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
EVALUATION = BACKEND.parent / "evaluation"
for path in (BACKEND, EVALUATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

pytest.importorskip("scipy")

import spatial as S  # noqa: E402  (evaluation/spatial.py)


# ------------------------------------------------------------------ splits

def write_split(folder, name, keys):
    folder.mkdir(parents=True, exist_ok=True)
    rows = "".join(f"{k}_S1Hand.tif,{k}_LabelHand.tif\n" for k in keys)
    (folder / f"flood_{name}_data.csv").write_text(rows, encoding="utf-8")


def test_the_official_splits_are_read_from_the_dataset(tmp_path):
    hand = tmp_path / "v1.1" / "splits" / "flood_handlabeled"
    write_split(hand, "train", ["Ghana_1", "Sri-Lanka_2"])
    write_split(hand, "valid", ["India_3"])
    write_split(hand, "test", ["USA_4"])
    write_split(hand, "bolivia", ["Bolivia_5"])

    splits = S.load_splits(tmp_path)
    assert splits["train"] == {"Ghana_1", "Sri-Lanka_2"}
    assert splits["bolivia"] == {"Bolivia_5"}


def test_weak_label_splits_with_the_same_names_are_ignored(tmp_path):
    """The weakly labelled data reuses the file names. Mixing them in would
    score against labels that were never hand checked."""
    hand = tmp_path / "splits" / "flood_handlabeled"
    for i, name in enumerate(("train", "valid", "test", "bolivia")):
        write_split(hand, name, [f"India_{100 + i}"])
    write_split(tmp_path / "splits" / "flood_s1weak", "train", ["India_999"])

    assert "India_999" not in S.load_splits(tmp_path)["train"]


def test_a_missing_split_file_fails_loudly(tmp_path):
    write_split(tmp_path / "splits" / "flood_handlabeled", "train", ["Ghana_1"])
    with pytest.raises(FileNotFoundError, match="test"):
        S.load_splits(tmp_path)


# ----------------------------------------------------------------- metrics

def test_pooled_and_chip_mean_differ_as_they_should():
    """One big easy chip and one small hard chip. Pooling is dominated by the
    big one; the chip mean weighs them equally."""
    big_easy = (900, 50, 50)      # IoU 0.9
    small_hard = (10, 10, 30)     # IoU 0.2
    assert S.pooled([big_easy, small_hard])["iou"] == pytest.approx(910 / 1050)
    assert S.chip_mean_iou([big_easy, small_hard]) == pytest.approx(0.55)


def test_a_dry_chip_with_no_prediction_is_skipped_not_scored():
    """0/0 is not 0 and not 1. Counting it either way would move the chip
    mean on chips that contain nothing to find."""
    assert S.chip_mean_iou([(10, 0, 0), (0, 0, 0)]) == pytest.approx(1.0)


def test_nodata_labels_are_never_scored():
    pred = np.array([True, True, False])
    truth = np.array([1, -1, 0])
    assert S.counts(pred, truth) == (1, 0, 0)


# ---------------------------------------------------------- speckle, scale

def test_speckle_filter_removes_isolated_noise():
    band = np.full((21, 21), -10.0, dtype=np.float32)
    band[10, 10] = -30.0          # one speckle pixel
    assert S.speckle_median(band)[10, 10] == pytest.approx(-10.0)


def test_speckle_filter_keeps_holes_as_holes():
    band = np.full((21, 21), -10.0, dtype=np.float32)
    band[3, 3] = np.nan
    out = S.speckle_median(band)
    assert np.isnan(out[3, 3])
    assert np.isfinite(out[3, 4]), "a hole must not spread"


def test_block_mean_is_the_pyramid_average_and_ignores_nan():
    band = np.array([[-10, -20, -30, -30],
                     [-10, np.nan, -30, -30]], dtype=float)
    out = S.block_mean(band, 2)
    assert out.shape == (1, 2)
    assert out[0, 0] == pytest.approx(-40 / 3)
    assert out[0, 1] == pytest.approx(-30)


def test_block_mean_crops_an_incomplete_edge():
    assert S.block_mean(np.zeros((512, 512)), 10).shape == (51, 51)


def test_coarse_labels_follow_the_majority_and_drop_thin_blocks():
    truth = np.array([[1, 1, 0, -1],
                      [1, 0, -1, -1]])
    out = S.block_label(truth, 2)
    assert out[0, 0] == 1              # 3 of 4 water
    assert out[0, 1] == -1             # only 1 of 4 labelled


# ---------------------------------------------------------- region growing

def strip(values):
    return np.array([values], dtype=float)


def test_growth_reaches_moderately_dark_pixels_joined_to_confident_water():
    # confident water, then a shallow margin, then dry land
    fused = strip([-24, -24, -18, -18, -8])
    assert S.grow(fused, -20, -17, steps=0).tolist() == [[True, True, True, True, False]]


def test_an_isolated_moderately_dark_patch_is_not_grown_into():
    """Wet soil in the middle of a field, not touching any flood."""
    fused = strip([-24, -8, -18, -18, -8])
    assert S.grow(fused, -20, -17, steps=0).tolist() == [[True, False, False, False, False]]


def test_growth_stops_after_the_step_limit():
    fused = strip([-24, -18, -18, -18, -18])
    assert S.grow(fused, -20, -17, steps=2).tolist() == [[True, True, True, False, False]]


def test_growth_never_loses_what_the_darkness_rule_finds():
    rng = np.random.default_rng(3)
    fused = rng.uniform(-30, 0, (60, 60))
    fused[rng.random((60, 60)) < 0.05] = np.nan
    dark = np.isfinite(fused) & (np.nan_to_num(fused, nan=0) < -20)
    assert not np.any(dark & ~S.grow(fused, -20, -16, steps=5))


def test_diagonal_neighbours_count():
    fused = np.full((3, 3), -8.0)
    fused[0, 0] = -24
    fused[1, 1] = -18
    assert S.grow(fused, -20, -17, steps=0)[1, 1]


def test_missing_data_is_never_grown_into():
    fused = strip([-24, np.nan, -18])
    assert S.grow(fused, -20, -17, steps=0).tolist() == [[True, False, False]]


def test_a_weak_threshold_no_looser_than_the_strong_one_changes_nothing():
    fused = strip([-24, -19, -18])
    assert S.grow(fused, -20, -20, steps=0).tolist() == [[True, False, False]]


def test_the_share_added_by_growth_is_reported():
    fused = strip([-24, -18, -18, -8])
    assert S.grown_share(fused, -20, -17, steps=0) == pytest.approx(2 / 3)


# ----------------------------------------------------------------- scoring

def test_score_reports_both_conventions():
    chips = [{"truth": np.array([[1, 1, 0]]), "fused": strip([-24, -18, -8])}]
    result = S.score(chips, lambda c: S.grow(c["fused"], -20, -17, steps=0))
    assert result["pooled"]["iou"] == 1.0
    assert result["chip_mean_iou"] == 1.0
    assert result["chips"] == 1


def test_the_shipped_strong_threshold_is_the_backend_one():
    pytest.importorskip("ee")
    from detection import sar
    assert S.PRODUCTION_SPECKLE_RADIUS_PX * 10 == 50, "50 m on 10 m pixels"
    assert sar.DEFAULT_DB == -20.0


# ------------------------------------------ other layouts (Hugging Face copy)

def test_split_files_are_found_by_content_in_any_layout(tmp_path):
    """The Hugging Face copy did not use the flood_handlabeled/ folder the
    first version looked for. Files are now recognised by what they list."""
    hand = {"Ghana_1", "India_2", "USA_3", "Bolivia_4", "Spain_5"}
    folder = tmp_path / "v1.1" / "splits"
    folder.mkdir(parents=True)
    (folder / "train.txt").write_text("Ghana_1\nIndia_2\n", encoding="utf-8")
    (folder / "val.txt").write_text("Spain_5\n", encoding="utf-8")
    (folder / "test.txt").write_text("USA_3\n", encoding="utf-8")
    (folder / "bolivia_test.txt").write_text("Bolivia_4\n", encoding="utf-8")

    splits = S.load_splits(tmp_path, hand_keys=hand)
    assert splits == {"train": {"Ghana_1", "India_2"}, "valid": {"Spain_5"},
                      "test": {"USA_3"}, "bolivia": {"Bolivia_4"}}


def test_a_weak_label_split_is_rejected_by_what_it_lists(tmp_path):
    """Same file name, but its chips have no hand labels - so it is not the
    hand-labelled split, wherever it sits."""
    hand = {"Ghana_1", "India_2", "USA_3", "Bolivia_4", "Spain_5"}
    good = tmp_path / "a"
    weak = tmp_path / "b"
    for folder in (good, weak):
        folder.mkdir()
    (good / "flood_train_data.csv").write_text("Ghana_1_S1Hand.tif,x\n", encoding="utf-8")
    (weak / "flood_train_data.csv").write_text(
        "".join(f"Ghana_{9000 + i}_S1Weak.tif,x\n" for i in range(500)), encoding="utf-8")
    for name, key in (("valid", "Spain_5"), ("test", "USA_3"), ("bolivia", "Bolivia_4")):
        (good / f"flood_{name}_data.csv").write_text(f"{key},x\n", encoding="utf-8")

    assert S.load_splits(tmp_path, hand_keys=hand)["train"] == {"Ghana_1"}


def test_bolivia_is_not_mistaken_for_the_test_split():
    assert S._split_name("flood_bolivia_data") == "bolivia"
    assert S._split_name("bolivia_test") == "bolivia"
    assert S._split_name("flood_test_data") == "test"
    assert S._split_name("valid") == "valid"
    assert S._split_name("flood_val_data") == "valid"
    assert S._split_name("readme") is None
    assert S._split_name("intervals") is None, "'val' inside a word is not a split"


def test_the_error_lists_what_it_saw(tmp_path):
    (tmp_path / "train.txt").write_text("NotAChip\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="Candidate files seen"):
        S.load_splits(tmp_path, hand_keys={"Ghana_1"})
