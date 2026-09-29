"""
Where the missing water is, in backscatter terms.

Sentinel-1 VV at -17 dB scores recall 0.511 on Sen1Floods11: half of the water
in the labels is not detected. The threshold sweep already shows that lowering
the threshold does not fix it - precision collapses faster than recall rises.
So the missed pixels are probably not "just below the line". This module finds
out what they actually are.

Every missed water pixel falls into one of three cases, and they call for
completely different responses:

    marginal   backscatter sits just above the threshold. Calibration. A small
               threshold move catches these, at a precision cost the sweep has
               already measured.

    moderate   darker than land but well above the threshold. Mixed pixels,
               shallow or sediment-laden water, wind-roughened surfaces.

    bright     brighter than -10 dB. Far too bright to be open water at all.
               If these also show a wide VV-VH gap, the return is strongly
               co-polarised, which is the signature of DOUBLE BOUNCE: the pulse
               hitting water, reflecting off trunks and stems, and coming back
               to the sensor. That is flooded vegetation, and no darkness
               threshold in any polarisation will ever find it.

The share landing in each bin is the answer to "how much of the missing recall
is flooded vegetation under canopy?" - measured rather than assumed.

Pure numpy. Counts are pooled as histograms rather than kept as pixel arrays,
so memory stays constant across all 446 chips instead of growing to a hundred
million values.
"""

import numpy as np

NODATA = -1

# 0.5 dB resolution is finer than the differences we are looking for, and keeps
# the pooled histogram small enough to carry around in a JSON file.
VV_BINS = np.arange(-45.0, 5.5, 0.5)
GAP_BINS = np.arange(-10.0, 25.5, 0.5)

# Above this, a pixel is too bright to be open water under any threshold anyone
# would defend. Open water in Sentinel-1 VV sits near -20 dB.
BRIGHT_DB = -10.0

# How far above the threshold still counts as "nearly caught".
MARGINAL_WIDTH_DB = 3.0

# A VV-VH gap wider than this means the return kept its polarisation, which
# points at double bounce rather than the volume scattering of a dry canopy.
DOUBLE_BOUNCE_GAP_DB = 9.0


def _percentile_from_histogram(counts, edges, fraction):
    """Approximate percentile from pooled bin counts.

    Exact to the bin width (0.5 dB), which is well inside the precision anyone
    should claim for a backscatter statistic.
    """
    total = counts.sum()
    if total == 0:
        return None

    cumulative = np.cumsum(counts)
    index = int(np.searchsorted(cumulative, total * fraction))
    index = min(index, len(counts) - 1)
    return float((edges[index] + edges[index + 1]) / 2.0)


class ErrorProfile:
    """Accumulates backscatter statistics for correct and incorrect pixels.

    Call update() once per chip, then summary() at the end.
    """

    CATEGORIES = ("true_positive", "false_negative", "false_positive")

    def __init__(self, threshold=-17.0, bright_db=BRIGHT_DB,
                 marginal_width=MARGINAL_WIDTH_DB,
                 double_bounce_gap=DOUBLE_BOUNCE_GAP_DB):
        self.threshold = float(threshold)
        self.bright_db = float(bright_db)
        self.marginal_width = float(marginal_width)
        self.double_bounce_gap = float(double_bounce_gap)

        self.vv_hist = {c: np.zeros(len(VV_BINS) - 1, dtype=np.int64)
                        for c in self.CATEGORIES}
        self.gap_hist = {c: np.zeros(len(GAP_BINS) - 1, dtype=np.int64)
                         for c in self.CATEGORIES}
        self.counts = {c: 0 for c in self.CATEGORIES}

        # False negatives split by how bright they are.
        self.fn_bins = {"marginal": 0, "moderate": 0, "bright": 0}
        # Of the bright ones, how many look like double bounce.
        self.fn_bright_double_bounce = 0
        self.chips = 0

    def update(self, vv, vh, truth, pred):
        """One chip. vv/vh in dB, truth in {-1,0,1}, pred boolean."""
        vv = np.asarray(vv, dtype=np.float64)
        vh = np.asarray(vh, dtype=np.float64)
        truth = np.asarray(truth)
        pred = np.asarray(pred).astype(bool)

        labelled = (truth != NODATA) & np.isfinite(vv) & np.isfinite(vh)
        if not labelled.any():
            return

        water = labelled & (truth == 1)
        land = labelled & (truth == 0)

        masks = {
            "true_positive": water & pred,
            "false_negative": water & ~pred,
            "false_positive": land & pred,
        }

        with np.errstate(invalid="ignore"):
            gap = vv - vh

        for name, mask in masks.items():
            if not mask.any():
                continue
            self.counts[name] += int(mask.sum())
            self.vv_hist[name] += np.histogram(vv[mask], bins=VV_BINS)[0]
            self.gap_hist[name] += np.histogram(gap[mask], bins=GAP_BINS)[0]

        missed = masks["false_negative"]
        if missed.any():
            missed_vv = vv[missed]
            missed_gap = gap[missed]

            marginal = missed_vv < self.threshold + self.marginal_width
            bright = missed_vv >= self.bright_db
            moderate = ~marginal & ~bright

            self.fn_bins["marginal"] += int(marginal.sum())
            self.fn_bins["moderate"] += int(moderate.sum())
            self.fn_bins["bright"] += int(bright.sum())
            self.fn_bright_double_bounce += int(
                (bright & (missed_gap > self.double_bounce_gap)).sum()
            )

        self.chips += 1

    def summary(self):
        """Numbers for the report. Shares are of the false negatives only."""
        missed = max(sum(self.fn_bins.values()), 1)

        def stats(category):
            return {
                "pixels": self.counts[category],
                "vv_median_db": _percentile_from_histogram(
                    self.vv_hist[category], VV_BINS, 0.5),
                "vv_p90_db": _percentile_from_histogram(
                    self.vv_hist[category], VV_BINS, 0.9),
                "vv_vh_gap_median_db": _percentile_from_histogram(
                    self.gap_hist[category], GAP_BINS, 0.5),
            }

        return {
            "chips": self.chips,
            "threshold_db": self.threshold,
            "by_outcome": {c: stats(c) for c in self.CATEGORIES},
            "missed_water": {
                "total_pixels": sum(self.fn_bins.values()),
                "bins": dict(self.fn_bins),
                "shares": {
                    name: round(count / missed, 4)
                    for name, count in self.fn_bins.items()
                },
                "bright_and_double_bounce_pixels": self.fn_bright_double_bounce,
                "bright_and_double_bounce_share": round(
                    self.fn_bright_double_bounce / missed, 4
                ),
            },
            "definitions": {
                "marginal": f"vv < {self.threshold + self.marginal_width:.1f} dB "
                            "- a threshold move would catch these",
                "moderate": f"{self.threshold + self.marginal_width:.1f} dB "
                            f"<= vv < {self.bright_db:.1f} dB",
                "bright": f"vv >= {self.bright_db:.1f} dB - too bright to be "
                          "open water",
                "double_bounce": f"vv - vh > {self.double_bounce_gap:.1f} dB "
                                 "- return kept its polarisation",
            },
        }


def interpret(summary):
    """Plain-language reading of the summary, so the notebook states a
    conclusion rather than leaving a table to be squinted at.

    Deliberately conservative: it reports what the shares say and names the
    confound, rather than declaring the hypothesis proved.
    """
    missed = summary["missed_water"]
    shares = missed["shares"]
    lines = []

    if missed["total_pixels"] == 0:
        return ["No missed water pixels to analyse."]

    threshold = summary["threshold_db"]
    lines.append(
        f"{shares['marginal'] * 100:.1f}% of missed water sits between "
        f"{threshold:.0f} and {threshold + MARGINAL_WIDTH_DB:.0f} dB, within "
        f"{MARGINAL_WIDTH_DB:.0f} dB of the threshold - calibration, "
        "recoverable by moving the threshold at a known precision cost."
    )
    lines.append(
        f"{shares['bright'] * 100:.1f}% is brighter than -10 dB, which no "
        "darkness threshold can recover in any polarisation."
    )

    double_bounce = missed["bright_and_double_bounce_share"]
    lines.append(
        f"{double_bounce * 100:.1f}% is both bright AND strongly co-polarised, "
        "the signature of double bounce - consistent with flooded vegetation."
    )

    if double_bounce < 0.05:
        lines.append(
            "That share is small. Flooded vegetation is NOT the main cause of "
            "the missing recall, so a VV/VH fusion rule will not fix it. Worth "
            "reporting as a negative result rather than pursuing further."
        )
    elif double_bounce < 0.20:
        lines.append(
            "A real but minority contribution. A fusion rule might recover some "
            "of it; check whether the precision cost is acceptable before "
            "changing what ships."
        )
    else:
        lines.append(
            "A large share. A double-bounce arm is worth adding to the "
            "detector - but built-up areas scatter the same way, so measure "
            "precision on dry urban chips before trusting it."
        )

    lines.append(
        "Caveat that applies whatever the number: Sen1Floods11 labels were "
        "drawn from Sentinel-2 indices and hand corrected using optical, DEM "
        "and land cover. If the labellers could not see water under a canopy, "
        "flooded vegetation is absent from the labels, and detecting it would "
        "score as a false positive rather than as recovered recall."
    )
    return lines
