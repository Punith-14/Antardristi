"""
Every chart and dataset picture the report uses, drawn from the results the
notebooks already saved - so a figure can never disagree with the measurement
it illustrates. Run through notebook 10 (backend/notebooks/10_report_figures).

Each fig_* function takes the loaded result dict(s), draws one figure, saves it
as a PNG in the output folder and returns the path. Nothing here calls Earth
Engine; the dataset views read a local Sen1Floods11 copy if there is one.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")                     # files, not windows; the notebook shows them inline
import matplotlib.pyplot as plt           # noqa: E402
import numpy as np                        # noqa: E402

EVALUATION = Path(__file__).resolve().parent
PROJECT = EVALUATION.parent
# antardrishti -> application -> Group_project / document / report_images / charts
DEFAULT_OUT = PROJECT.parent.parent / "document" / "report_images" / "charts"
RESULTS = PROJECT / "backend" / "results"
MODELS = PROJECT / "models"

NAVY, TEAL, ORANGE, RED, GREY, GREEN = "#0B2545", "#0F8B8D", "#E07A1F", "#C0392B", "#8A99A8", "#2E8B57"
GRADE = {"good": GREEN, "moderate": ORANGE, "poor": RED, "unvalidated": GREY}

_out = {"dir": DEFAULT_OUT}


def set_output(folder):
    _out["dir"] = Path(folder)


def style():
    plt.rcParams.update({
        "figure.dpi": 110, "savefig.dpi": 220, "font.size": 10.5,
        "font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False,
        "axes.titleweight": "bold", "axes.titlesize": 12, "axes.grid": True, "grid.alpha": 0.25,
        "legend.frameon": False,
    })


def save(fig, name):
    folder = _out["dir"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.png"
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    print("saved", path)
    return path


def load(name, folder=EVALUATION):
    path = Path(folder) / name
    if not path.exists():
        print(f"(skipped: {path.name} not found)")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _bar_labels(ax, bars, fmt="{:.3f}"):
    for b in bars:
        ax.annotate(fmt.format(b.get_height()), (b.get_x() + b.get_width() / 2, b.get_height()),
                    ha="center", va="bottom", fontsize=9, xytext=(0, 2), textcoords="offset points")


# ------------------------------------------------------------- notebook 01

def fig_threshold_sweep(s1f):
    """IoU, precision and recall against the VV threshold (notebook 01)."""
    sweep = s1f["threshold_sweep"]
    xs = sorted(sweep, key=float)
    x = [float(t) for t in xs]
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    for key, colour, label in (("iou", NAVY, "IoU"), ("precision", TEAL, "Precision"), ("recall", ORANGE, "Recall")):
        ax.plot(x, [sweep[t][key] for t in xs], marker="o", ms=3.5, color=colour, label=label, lw=2)
    best = max(xs, key=lambda t: sweep[t]["iou"])
    ax.axvline(float(best), color=GREY, ls="--", lw=1)
    ax.annotate(f"best IoU {sweep[best]['iou']:.3f}\nat {float(best):g} dB", (float(best), sweep[best]["iou"]),
                xytext=(10, 25), textcoords="offset points", fontsize=9)
    ax.set_xlabel("Sentinel-1 VV threshold (dB): darker than this is called water")
    ax.set_ylabel("Score")
    ax.set_ylim(0, 1)
    ax.set_title(f"Threshold sweep on Sen1Floods11 ({s1f.get('chips_scored', '?')} hand-labelled chips)")
    ax.legend(loc="center right")
    return save(fig, "fig_01_threshold_sweep")


def fig_otsu_methods(s1f):
    """Fixed thresholds against Otsu, pooled IoU (notebook 01)."""
    methods = s1f["methods"]
    names = list(methods)
    iou = [methods[n]["micro"]["iou"] for n in names]
    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    bars = ax.bar(range(len(names)), iou, color=[TEAL if "otsu" in n else NAVY for n in names])
    _bar_labels(ax, bars)
    ax.set_xticks(range(len(names)), [n.replace(" (", "\n(").replace(" [", "\n[") for n in names], fontsize=8.5)
    ax.set_ylabel("IoU (pooled over all pixels)")
    ax.set_ylim(0, max(iou) * 1.25)
    ax.set_title("Fixed thresholds vs automatic (Otsu) thresholds")
    return save(fig, "fig_01_fixed_vs_otsu")


# ------------------------------------------------------------- notebook 04

def fig_polarisation(pol):
    """VV, VH and their fusion (notebook 04)."""
    methods = pol["methods"]
    names = list(methods)
    keys = ("iou", "precision", "recall")
    fig, ax = plt.subplots(figsize=(8, 4))
    width = 0.26
    for i, (k, c) in enumerate(zip(keys, (NAVY, TEAL, ORANGE))):
        bars = ax.bar(np.arange(len(names)) + (i - 1) * width, [methods[n]["micro"][k] for n in names],
                      width, color=c, label=k.capitalize())
        if k == "iou":
            _bar_labels(ax, bars, "{:.2f}")
    ax.set_xticks(range(len(names)), [n.replace(" (", "\n(") for n in names], fontsize=8.5)
    ax.set_ylim(0, 1)
    ax.set_title("Polarisation: VV, VH and VV+VH rules on Sen1Floods11")
    ax.legend(ncols=3, loc="upper left")
    return save(fig, "fig_04_polarisation")


def fig_missed_water(pol):
    """How bright the missed water is, in VV (notebook 04)."""
    bins = pol["missing_recall_diagnostic"]["missed_water"]["bins"]
    names = list(bins)
    total = sum(bins.values())
    fig, ax = plt.subplots(figsize=(6, 3.6))
    bars = ax.bar(names, [bins[n] / total * 100 for n in names], color=[TEAL, ORANGE, RED][:len(names)])
    _bar_labels(ax, bars, "{:.0f}%")
    ax.set_ylabel("Share of missed water pixels (%)")
    ax.set_title("Where the missed flood water sits (radar brightness)")
    return save(fig, "fig_04_missed_water")


# ------------------------------------------------------------- notebook 05

def fig_optical(opt):
    """MNDWI sweep and radar vs optical agreement (notebook 05)."""
    sweep = opt["sweep"]
    xs = sorted(sweep, key=float)
    fig, (a, b) = plt.subplots(1, 2, figsize=(10.5, 4))
    for key, colour in (("iou", NAVY), ("precision", TEAL), ("recall", ORANGE)):
        a.plot([float(t) for t in xs], [sweep[t][key] for t in xs], marker="o", ms=3, color=colour, label=key.capitalize(), lw=2)
    a.axvline(opt["shipped_optical_threshold"], color=GREY, ls="--", lw=1)
    a.set_xlabel("MNDWI threshold (Sentinel-2)")
    a.set_ylim(0, 1)
    a.set_title("Optical water rule: threshold sweep")
    a.legend()
    water = opt["agreement"]["water_pixels"]
    labels = list(water)
    total = sum(water.values())
    bars = b.bar(labels, [water[k] / total * 100 for k in labels], color=[GREEN, NAVY, TEAL, RED][:len(labels)])
    _bar_labels(b, bars, "{:.1f}%")
    b.set_ylabel("% of labelled water pixels")
    b.set_title("Radar vs optical on true water")
    b.tick_params(axis="x", labelsize=8.5)
    return save(fig, "fig_05_optical")


# ------------------------------------------------------------- notebook 06

def fig_change(chg):
    """Change detection against darkness alone (notebook 06)."""
    h = chg["head_to_head"]
    names = list(h)
    fig, (a, b) = plt.subplots(1, 2, figsize=(10.5, 4))
    bars = a.bar(range(len(names)), [h[n]["micro"]["iou"] for n in names], color=[NAVY, TEAL, ORANGE][:len(names)])
    _bar_labels(a, bars)
    a.set_xticks(range(len(names)), [n.replace(" (", "\n(").replace(", drop", "\ndrop").replace(" / ", "\n") for n in names], fontsize=7.5)
    a.set_ylabel("IoU")
    a.set_title("Darkness rule vs change detection")
    rec = chg["recovery_by_bin"]
    bins = list(rec)
    x = np.arange(len(bins))
    b.bar(x - 0.2, [rec[k]["missed"] for k in bins], 0.4, color=GREY, label="missed by darkness")
    b.bar(x + 0.2, [rec[k]["recovered"] for k in bins], 0.4, color=TEAL, label="recovered by change")
    b.set_xticks(x, bins)
    b.set_ylabel("pixels")
    b.set_title("Missed water recovered, by brightness")
    b.legend()
    return save(fig, "fig_06_change_detection")


# ------------------------------------------------------------- notebook 07

def fig_scale(spa):
    """Accuracy at 10 m, 100 m and 200 m on the test split (notebook 07).

    Two bars per scale: the -20 dB rule as it first ran, and the threshold
    re-chosen on the TRAIN split for that scale (what ships: -18.5 dB at 200 m).
    """
    ran = spa["step1_as_it_runs_on_test"]
    best = spa["step1_best_threshold_at_scale"]
    names = ["speckled_10m", "100 m", "200 m"]
    first = [ran[n]["pooled"]["iou"] for n in names]
    tuned = [ran["speckled_10m"]["pooled"]["iou"]] + [best[n]["test"]["pooled"]["iou"] for n in names[1:]]
    labels = ["10 m (raw pixels)", f"100 m (re-chosen {best['100 m']['db']} dB)", f"200 m (re-chosen {best['200 m']['db']} dB, ships)"]
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(7.4, 4))
    b1 = ax.bar(x - 0.2, first, 0.4, color=GREY, label="-20 dB, as first run")
    b2 = ax.bar(x + 0.2, tuned, 0.4, color=TEAL, label="threshold chosen on train for that scale")
    _bar_labels(ax, b1)
    _bar_labels(ax, b2)
    ax.set_xticks(x, labels, fontsize=8.5)
    ax.set_ylim(0, 0.8)
    ax.set_ylabel("IoU, test split (88 chips)")
    ax.set_title("Analysis scale: averaging radar speckle away")
    ax.legend(loc="upper left")
    return save(fig, "fig_07_scale")


# ------------------------------------------------------------- notebook 08

def fig_events(evt):
    """IoU per flood event, India highlighted (notebook 08)."""
    per = evt["per_event"]
    names = sorted(per, key=lambda e: per[e]["iou"])
    fig, ax = plt.subplots(figsize=(8, 4.4))
    bars = ax.barh(names, [per[e]["iou"] for e in names],
                   color=[ORANGE if e == "India" else NAVY for e in names])
    for b, e in zip(bars, names):
        ax.text(b.get_width() + 0.01, b.get_y() + b.get_height() / 2,
                f"{per[e]['iou']:.2f}  ({per[e]['chips']} chips)", va="center", fontsize=8.5)
    held = evt["india"]["held_out"]
    ax.axvline(held["iou"], color=ORANGE, ls="--", lw=1)
    ax.text(held["iou"] + 0.01, -0.9, f"India held-out {held['iou']:.3f}\n95% range {held['ci95'][0]:.2f}-{held['ci95'][1]:.2f}",
            color=ORANGE, fontsize=8.5)
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("IoU at 200 m, shipped rule")
    ax.set_title("Accuracy per flood event (Sen1Floods11)")
    ax.grid(axis="y", alpha=0)
    return save(fig, "fig_08_events_india")


# ------------------------------------------------------------- notebook 09

def fig_terrain(ter):
    """With and without the terrain (HAND) check (notebook 09)."""
    res = ter["results"]
    names = [n for n in res if "after" in res[n]]
    x = np.arange(len(names))
    fig, (a, b) = plt.subplots(1, 2, figsize=(10.5, 4), gridspec_kw={"width_ratios": [2.2, 1]})
    a.bar(x - 0.2, [res[n]["before"]["iou"] for n in names], 0.4, color=NAVY, label="without check")
    a.bar(x + 0.2, [res[n]["after"]["iou"] for n in names], 0.4, color=TEAL, label="with HAND check")
    a.set_xticks(x, names, rotation=25, fontsize=8.5)
    a.set_ylim(0, 1)
    a.set_ylabel("IoU")
    a.set_title("Terrain check: measured, not shipped")
    a.legend()
    removed = res.get("test 200 m", {}).get("removed") or {}
    if removed:
        bars = b.bar(["dry ground\nremoved", "real water\nremoved"],
                     [removed.get("removed_dry_px", 0), removed.get("removed_water_px", 0)], color=[GREEN, RED])
        _bar_labels(b, bars, "{:.0f}")
        b.set_title("What it removed (test, 200 m)")
        b.set_ylabel("pixels")
    reasons = (ter.get("decision") or {}).get("reasons") or []
    if reasons:
        fig.text(0.01, -0.04, "Decision: " + "; ".join(reasons)[:220], fontsize=8, color=GREY)
    return save(fig, "fig_09_terrain")


# ------------------------------------------------------------- surface + classifier

def fig_surface(surf, claims=None):
    """IoU of each surface analysis against ESA WorldCover."""
    ana = {k: v for k, v in surf["analyses"].items() if "measured_scores" in v}
    names = list(ana)
    iou = [ana[n]["measured_scores"]["iou"] for n in names]
    grade = [(claims or {}).get(n) or ("good" if v >= 0.6 else "moderate" if v >= 0.4 else "poor") for n, v in zip(names, iou)]
    fig, ax = plt.subplots(figsize=(7.5, 3.9))
    labels = [n.replace("_", " ") + ("\n(NDBI index; RF 0.433)" if n == "built_up" else "") for n in names]
    bars = ax.bar(labels, iou, color=[GRADE[g] for g in grade])
    _bar_labels(ax, bars)
    ax.set_ylim(0, 1)
    ax.set_ylabel(f"IoU vs ESA WorldCover {surf.get('year', '')}")
    ax.set_title("Surface analyses: measured accuracy (crop stress has no reference)")
    handles = [plt.Rectangle((0, 0), 1, 1, color=GRADE[g]) for g in ("good", "moderate", "poor")]
    ax.set_ylim(0, 1.12)
    ax.legend(handles, ["good", "moderate", "poor"], loc="upper center", ncols=3)
    return save(fig, "fig_surface_accuracy")


def fig_surface_districts(surf):
    """Per-district IoU heatmap at each analysis' measured threshold."""
    ana = {k: v for k, v in surf["analyses"].items() if "per_district" in v and "measured_threshold" in v}
    names = list(ana)
    districts = sorted({d for v in ana.values() for d in v["per_district"]})
    grid = np.full((len(names), len(districts)), np.nan)
    for i, n in enumerate(names):
        t = str(ana[n]["measured_threshold"])
        for j, d in enumerate(districts):
            cell = ana[n]["per_district"].get(d, {}).get("thresholds", {}).get(t)
            if cell:
                grid[i, j] = cell["iou"]
    fig, ax = plt.subplots(figsize=(8.2, 3.6))
    im = ax.imshow(grid, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(districts)), districts, rotation=25, ha="right")
    ax.set_yticks(range(len(names)), [n.replace("_", " ") for n in names])
    for i in range(len(names)):
        for j in range(len(districts)):
            if not np.isnan(grid[i, j]):
                ax.text(j, i, f"{grid[i, j]:.2f}", ha="center", va="center", fontsize=8.5)
    ax.grid(False)
    fig.colorbar(im, ax=ax, label="IoU")
    ax.set_title("Same rule, different landscapes: IoU per district")
    return save(fig, "fig_surface_districts")


def fig_classifier(model):
    """Random Forest against the index rules, per class, same pixels."""
    res = model["results"]
    names = list(res)
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(7.2, 3.9))
    b1 = ax.bar(x - 0.2, [res[n]["index_rule"]["iou_on_this_test_set"] for n in names], 0.4, color=GREY, label="index rule")
    b2 = ax.bar(x + 0.2, [res[n]["rf"]["iou"] for n in names], 0.4, color=TEAL, label="Random Forest (S2 + S1)")
    _bar_labels(ax, b1, "{:.2f}")
    _bar_labels(ax, b2, "{:.2f}")
    ax.set_xticks(x, names)
    ax.set_ylim(0, 1)
    ax.set_ylabel("IoU on held-out districts")
    ax.set_title(f"Land-cover classifier vs index rules (held out: {', '.join(model.get('holdout_districts', []))})")
    ax.legend(loc="upper right")
    return save(fig, "fig_classifier_vs_index")


def fig_routing(llm, rules=None, heldout=None):
    """How well questions are understood: rules, the model, unseen phrasings."""
    fields = list(llm["accuracy"])
    runs = [("Rules only", rules, GREY), (f"LLM ({llm.get('mode', '')})", llm, TEAL), ("LLM, held-out phrasings", heldout, ORANGE)]
    runs = [r for r in runs if r[1]]
    width = 0.8 / len(runs)
    x = np.arange(len(fields))
    fig, ax = plt.subplots(figsize=(8, 4))
    for i, (label, data, colour) in enumerate(runs):
        ax.bar(x + (i - (len(runs) - 1) / 2) * width, [data["accuracy"].get(f, 0) for f in fields], width,
               color=colour, label=f"{label} - {data.get('cases_scored', '?')} questions")
    ax.set_xticks(x, fields)
    ax.set_ylim(0, 1.1)
    ax.set_ylabel("share correct")
    ax.set_title("Ask: what the question was understood as")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncols=1, fontsize=8.5)
    return save(fig, "fig_routing_accuracy")


def fig_flood_methods_summary(s1f, pol, spa, chg, opt, evt):
    """Every flood rule measured, side by side. Mixed protocols, stated."""
    rows = []
    if s1f:
        best = max(s1f["threshold_sweep"].values(), key=lambda r: r["iou"])
        rows.append(("VV threshold\n(nb 01)", best["iou"]))
    if pol:
        rows.append(("VH threshold\n(nb 04)", max(m["micro"]["iou"] for m in pol["methods"].values())))
    if chg:
        rows.append(("change detection\n(nb 06)", chg["best"]["iou"]))
    if spa:
        rows.append(("VV+VH at 200 m,\ntest split, ships (nb 07)", spa["step1_best_threshold_at_scale"]["200 m"]["test"]["pooled"]["iou"]))
    if evt:
        rows.append(("India held-out\n(nb 08)", evt["india"]["held_out"]["iou"]))
    if opt:
        rows.append(("optical MNDWI,\nbest (nb 05)", max(r["iou"] for r in opt["sweep"].values())))
    fig, ax = plt.subplots(figsize=(8.5, 4))
    bars = ax.bar([r[0] for r in rows], [r[1] for r in rows],
                  color=[ORANGE if "India" in r[0] else NAVY for r in rows])
    _bar_labels(ax, bars)
    ax.set_ylim(0, 1)
    ax.set_ylabel("IoU")
    ax.tick_params(axis="x", labelsize=8.5)
    ax.set_title("Flood detection: how each step changed the score")
    fig.text(0.01, -0.03, "Pooled IoU on Sen1Floods11 hand labels. Splits differ between notebooks (all chips, test split, "
             "India held-out); read as the direction of improvement, not one leaderboard.", fontsize=8, color=GREY)
    return save(fig, "fig_flood_methods_summary")


# ------------------------------------------------------------- the dataset

def chip_pairs(root):
    """{key: (s1_path, label_path)} for every hand-labelled chip under root."""
    root = Path(root)
    s1 = {p.name.replace("_S1Hand.tif", ""): p for p in root.rglob("*_S1Hand.tif")}
    lab = {p.name.replace("_LabelHand.tif", ""): p for p in root.rglob("*_LabelHand.tif")}
    return {k: (s1[k], lab[k]) for k in sorted(s1) if k in lab}


def fig_dataset_overview(pairs):
    """How many hand-labelled chips each flood event contributes."""
    from collections import Counter
    counts = Counter(k.split("_")[0] for k in pairs)
    names = sorted(counts, key=counts.get)
    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.barh(names, [counts[n] for n in names], color=[ORANGE if n == "India" else NAVY for n in names])
    for b in bars:
        ax.text(b.get_width() + 0.5, b.get_y() + b.get_height() / 2, f"{int(b.get_width())}", va="center", fontsize=8.5)
    ax.set_xlabel("hand-labelled 512 x 512 chips")
    ax.set_title(f"Sen1Floods11: {len(pairs)} chips from {len(counts)} flood events")
    ax.grid(axis="y", alpha=0)
    return save(fig, "fig_dataset_events")


def fig_dataset_chips(pairs, keys=None, threshold_db=-18.5, n=4):
    """A few chips: VV, VH, the hand label, and the rule against the label."""
    import rasterio
    keys = keys or _pick_chips(pairs, n)
    fig, axes = plt.subplots(len(keys), 4, figsize=(11, 2.8 * len(keys)))
    axes = np.atleast_2d(axes)
    for row, key in zip(axes, keys):
        s1_path, label_path = pairs[key]
        with rasterio.open(s1_path) as src:
            vv, vh = src.read(1).astype("float32"), src.read(2).astype("float32")
        with rasterio.open(label_path) as src:
            label = src.read(1)
        vv[vv < -50] = np.nan
        vh[vh < -50] = np.nan
        fused = (vv + vh) / 2
        valid = label != -1
        pred = np.isfinite(fused) & (fused < threshold_db)
        truth = label == 1
        comp = np.full(label.shape + (3,), 0.92)
        comp[valid & pred & truth] = (0.06, 0.55, 0.55)      # caught
        comp[valid & ~pred & truth] = (0.75, 0.22, 0.17)     # missed
        comp[valid & pred & ~truth] = (0.88, 0.48, 0.12)     # false water
        comp[~valid] = (1, 1, 1)
        row[0].imshow(vv, cmap="gray", vmin=-25, vmax=0)
        row[1].imshow(vh, cmap="gray", vmin=-30, vmax=-5)
        row[2].imshow(np.where(valid, truth, np.nan), cmap="Blues", vmin=0, vmax=1)
        row[3].imshow(comp)
        row[0].set_ylabel(key, fontsize=8.5)
        for ax, title in zip(row, ("VV (dB)", "VH (dB)", "hand label: water", "rule vs label")):
            ax.set_title(title, fontsize=9.5)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.grid(False)
    fig.text(0.5, -0.01, f"Rule: mean(VV,VH) < {threshold_db} dB.  Teal = water caught, red = water missed, "
             "orange = false water, grey = dry, white = no label.", ha="center", fontsize=9)
    return save(fig, "fig_dataset_chips")


def _pick_chips(pairs, n):
    """India first, then a spread of other events - the same every run."""
    by_event = {}
    for k in pairs:
        by_event.setdefault(k.split("_")[0], []).append(k)
    order = ["India"] + [e for e in ("Bolivia", "Spain", "Mekong", "Pakistan", "USA") if e != "India"]
    picks = [by_event[e][len(by_event[e]) // 2] for e in order if e in by_event]
    return picks[:n]
