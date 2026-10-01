"""
Evaluation that can be compared with the literature, and the two cheapest
ways to add spatial context to the darkness rule.

Three things live here, in the order notebook 07 uses them.

1. **The official split.** Every number so far pooled all 441 labelled chips.
   Published Sen1Floods11 results score a held-out test split (and a held-out
   Bolivia set), usually averaging IoU per chip. Our 0.489 therefore could not
   be put next to a published 0.359 or 0.672. `load_splits` reads the split
   files that ship with the dataset, and `chip_mean_iou` gives the averaged
   figure next to the pooled one.

2. **The rule as it actually runs.** Validation used raw 10 m chips. The live
   app applies a 50 m focal-median speckle filter, and then reduces at 100 to
   200 m, where Earth Engine serves pixels averaged from the 10 m data. The
   threshold is therefore applied to much smoother values than the ones it
   was tuned on. `speckle_median` and `block_mean` reproduce both steps so the
   shipped rule can be scored the way it runs.

3. **Region growing from confident water.** Matgen et al. (2011) and
   Giustarini et al. (2013) threshold only the clearest water, then grow into
   neighbouring pixels that pass a looser test; DLR's algorithm in the
   Copernicus Global Flood Monitoring ensemble does the same. Our missed water
   is 40% "marginal" (within 3 dB above -20) and 57% "moderate", so growing
   from confident water into near-threshold neighbours targets exactly it.

   `grow` is geodesic dilation: start from pixels below `strong_db`, and add
   pixels below `weak_db` that touch the growing region, for at most `steps`
   pixels. It is written that way because Earth Engine can do exactly the
   same thing - repeated one-pixel focal max, masked by the weak test - so
   what is scored here is what can ship.
"""

import csv
import re
import warnings
from pathlib import Path

import numpy as np

LABEL_NODATA = -1
KEY = re.compile(r"([A-Za-z]+(?:-[A-Za-z]+)*_\d+)")

# The production speckle filter: focal median, 50 m radius, on 10 m pixels.
PRODUCTION_SPECKLE_RADIUS_PX = 5


# ------------------------------------------------------------------ splits

SPLIT_WORDS = (
    # Checked in this order: "bolivia" before "test", because the Bolivia
    # hold-out file is itself a test set and may be named like one.
    ("bolivia", ("bolivia",)),
    ("test", ("test",)),
    ("valid", ("valid", "val")),
    ("train", ("train",)),
)


def _split_name(filename):
    lowered = filename.lower()
    for split, words in SPLIT_WORDS:
        if any(re.search(rf"(^|[^a-z]){w}([^a-z]|$)", lowered) for w in words):
            return split
    return None


def _keys_in(path):
    """Chip keys listed in a .csv or .txt, whatever the column layout."""
    keys = set()
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            match = KEY.search(line)
            if match:
                keys.add(match.group(1))
    return keys


def load_splits(root, hand_keys=None, min_overlap=0.9):
    """{split: set(chip keys)} from the dataset's own split files.

    The first version looked only for flood_{split}_data.csv in a folder
    named for hand labels, and found nothing in the Hugging Face copy of the
    dataset. So files are now found by what they are rather than where they
    sit:

    - any .csv or .txt whose name says train, valid/val, test or bolivia;
    - kept only if at least `min_overlap` of the chips it lists are chips
      that HAVE hand labels on disk (`hand_keys`). The weakly labelled splits
      share the file names but list thousands of other chips, so this
      separates them by content, not by folder.

    Where several files qualify for one split, the one covering the most
    hand-labelled chips wins. Raises, listing every candidate it saw, if a
    split is missing - scoring on an improvised split and calling it the
    official one would make every comparison wrong.
    """
    hand_keys = set(hand_keys or ())
    candidates = []
    for path in Path(root).rglob("*"):
        if path.suffix.lower() not in (".csv", ".txt") or not path.is_file():
            continue
        split = _split_name(path.stem)
        if split:
            candidates.append((split, path))

    best = {}
    for split, path in candidates:
        keys = _keys_in(path)
        if not keys:
            continue
        if hand_keys:
            covered = keys & hand_keys
            if len(covered) < min_overlap * len(keys):
                continue          # a weak-label split, or something else
            keys = covered
        elif "hand" not in str(path).lower():
            continue              # without the chip list, fall back to the folder name
        if split not in best or len(keys) > len(best[split][1]):
            best[split] = (path, keys)

    missing = {"train", "valid", "test", "bolivia"} - set(best)
    if missing:
        seen = "\n  ".join(f"{s:<8} {p}" for s, p in candidates[:25]) or "(none)"
        raise FileNotFoundError(
            f"split files not found for {sorted(missing)} under {root}.\n"
            f"Candidate files seen (name mentions a split):\n  {seen}"
        )
    return {split: keys for split, (path, keys) in best.items()}


def split_sources(root, hand_keys=None):
    """Which file each split was read from - printed so a wrong pick is visible."""
    hand_keys = set(hand_keys or ())
    chosen = {}
    for path in Path(root).rglob("*"):
        if path.suffix.lower() not in (".csv", ".txt") or not path.is_file():
            continue
        split = _split_name(path.stem)
        if not split:
            continue
        keys = _keys_in(path)
        covered = keys & hand_keys if hand_keys else keys
        if keys and (not hand_keys or len(covered) >= 0.9 * len(keys)):
            if split not in chosen or len(covered) > chosen[split][1]:
                chosen[split] = (path, len(covered))
    return {s: str(p) for s, (p, n) in chosen.items()}


# ----------------------------------------------------------------- metrics

def counts(pred, truth):
    """tp, fp, fn over labelled pixels only."""
    pred = np.asarray(pred, dtype=bool)
    truth = np.asarray(truth)
    valid = truth != LABEL_NODATA
    t = truth == 1
    return (
        int((pred & t & valid).sum()),
        int((pred & ~t & valid).sum()),
        int((~pred & t & valid).sum()),
    )


def pooled(chip_counts):
    """IoU, precision and recall with every pixel pooled - our usual figure."""
    tp = sum(c[0] for c in chip_counts)
    fp = sum(c[1] for c in chip_counts)
    fn = sum(c[2] for c in chip_counts)
    safe = lambda n, d: n / d if d else 0.0
    return {"iou": safe(tp, tp + fp + fn), "precision": safe(tp, tp + fp),
            "recall": safe(tp, tp + fn)}


def chip_mean_iou(chip_counts):
    """IoU averaged over chips - closer to how papers report Sen1Floods11.

    Chips with no water and no prediction have no IoU (0/0) and are skipped
    rather than counted as 0 or 1; conventions differ on this, which is one
    reason published figures are only roughly comparable even on one split.
    """
    values = [tp / (tp + fp + fn) for tp, fp, fn in chip_counts if tp + fp + fn]
    return float(np.mean(values)) if values else 0.0


def score(chips, predict):
    """{"pooled": {...}, "chip_mean_iou": x, "chips": n} for a rule."""
    per_chip = [counts(predict(chip), chip["truth"]) for chip in chips]
    return {"pooled": pooled(per_chip), "chip_mean_iou": chip_mean_iou(per_chip),
            "chips": len(per_chip)}


# --------------------------------------------------- the rule as it runs

def _disk(radius):
    y, x = np.ogrid[-radius:radius + 1, -radius:radius + 1]
    return x * x + y * y <= radius * radius


def speckle_median(band, radius_px=PRODUCTION_SPECKLE_RADIUS_PX):
    """Focal median over a disk, as sar.speckle_filter does, NaN kept NaN.

    NaN is filled with the chip median before filtering and restored after,
    so a hole neither spreads nor pulls its neighbours towards an arbitrary
    fill value.
    """
    from scipy.ndimage import median_filter

    band = np.asarray(band, dtype=np.float32)
    missing = ~np.isfinite(band)
    if missing.all():
        return band.copy()
    filled = np.where(missing, np.nanmedian(band), band)
    out = median_filter(filled, footprint=_disk(radius_px), mode="nearest")
    out[missing] = np.nan
    return out


def block_mean(band, factor):
    """Mean over factor x factor blocks, ignoring NaN - Earth Engine's pyramid.

    At a 100 m analysis scale Earth Engine does not threshold 10 m pixels; it
    serves the mean of each 10 x 10 block. The edge that does not fill a
    whole block is cropped.
    """
    band = np.asarray(band, dtype=np.float64)
    h = band.shape[0] // factor * factor
    w = band.shape[1] // factor * factor
    blocks = band[:h, :w].reshape(h // factor, factor, w // factor, factor)
    # An all-NaN block is a legitimate "no data here", not an error worth a
    # warning per block.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(blocks, axis=(1, 3))


def block_label(truth, factor, min_labelled=0.5):
    """Labels at the coarse scale: water if most labelled pixels are water.

    A block with less than `min_labelled` of its pixels labelled becomes
    nodata, so a coarse pixel is never scored on a handful of labels.
    """
    truth = np.asarray(truth)
    h = truth.shape[0] // factor * factor
    w = truth.shape[1] // factor * factor
    t = truth[:h, :w].reshape(h // factor, factor, w // factor, factor)
    labelled = (t != LABEL_NODATA).sum(axis=(1, 3))
    water = (t == 1).sum(axis=(1, 3))
    out = np.where(water * 2 > labelled, 1, 0).astype(np.int8)
    out[labelled < min_labelled * factor * factor] = LABEL_NODATA
    return out


# --------------------------------------------------------- region growing

EIGHT = np.ones((3, 3), dtype=bool)


def grow(fused_db, strong_db=-20.0, weak_db=-17.0, steps=10):
    """Region growing from confident water, as geodesic dilation.

    Seeds: pixels below `strong_db` (the shipped darkness rule). Then, `steps`
    times: add any pixel below `weak_db` that touches the region (8-connected).
    steps=0 means grow until nothing changes.

    A pixel that is only moderately dark is accepted only if it is joined to
    confidently dark water through other moderately dark pixels - the flood
    edge and shallow margin, not an isolated patch of wet soil in the middle
    of a field. Everything the darkness rule finds, this finds too.
    """
    from scipy.ndimage import binary_dilation

    fused_db = np.asarray(fused_db, dtype=np.float64)
    finite = np.isfinite(fused_db)
    with np.errstate(invalid="ignore"):
        strong = finite & (fused_db < strong_db)
        weak = finite & (fused_db < weak_db)
    if not strong.any() or weak_db <= strong_db:
        return strong
    return binary_dilation(strong, structure=EIGHT, iterations=steps, mask=weak) | strong


def grown_share(fused_db, strong_db=-20.0, weak_db=-17.0, steps=10):
    """How much of the result was added by growing: a sanity check that a
    setting is doing something, and not everything."""
    base = np.isfinite(fused_db) & (np.nan_to_num(fused_db, nan=0.0) < strong_db)
    out = grow(fused_db, strong_db, weak_db, steps)
    total = int(out.sum())
    return (total - int(base.sum())) / total if total else 0.0
