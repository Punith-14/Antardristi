"""
Notebook 10 draws every report chart from the saved results and saves PNGs.

Run end to end here, on the real result files and a small synthetic
Sen1Floods11 folder, so a broken cell is found before it is run for the
report - and the output folder is a temporary one, never the real document.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
EVALUATION = BACKEND.parent / "evaluation"
NOTEBOOK = BACKEND / "notebooks" / "10_report_figures.ipynb"

EXPECTED = {
    "fig_01_threshold_sweep", "fig_01_fixed_vs_otsu", "fig_04_polarisation", "fig_04_missed_water",
    "fig_05_optical", "fig_06_change_detection", "fig_07_scale", "fig_08_events_india",
    "fig_09_terrain", "fig_flood_methods_summary", "fig_surface_accuracy", "fig_surface_districts",
    "fig_classifier_vs_index", "fig_routing_accuracy", "fig_dataset_events", "fig_dataset_chips",
}


def _synthetic_sen1floods11(root):
    import rasterio
    from rasterio.transform import from_origin
    for sub in ("S1Hand", "LabelHand"):
        (root / sub).mkdir(parents=True)
    rng = np.random.default_rng(0)
    for i, event in enumerate(["India", "Bolivia", "Spain", "Mekong", "India"]):
        key = f"{event}_{1000 + i}"
        vv = rng.normal(-10, 2, (64, 64)).astype("float32")
        vh = vv - 7
        label = np.zeros((64, 64), "int16")
        label[10:40, 10:30] = 1
        vv[10:40, 10:30] = -24
        vh[10:40, 10:30] = -30
        label[60:, :] = -1
        profile = dict(driver="GTiff", height=64, width=64, crs="EPSG:4326", transform=from_origin(0, 0, 1, 1))
        with rasterio.open(root / "S1Hand" / f"{key}_S1Hand.tif", "w", count=2, dtype="float32", **profile) as dst:
            dst.write(np.stack([vv, vh]))
        with rasterio.open(root / "LabelHand" / f"{key}_LabelHand.tif", "w", count=1, dtype="int16", **profile) as dst:
            dst.write(label[None])


def test_notebook_10_draws_and_saves_every_figure(tmp_path):
    pytest.importorskip("matplotlib")
    pytest.importorskip("rasterio")
    pytest.importorskip("IPython")
    data, out = tmp_path / "sen1floods11", tmp_path / "charts"
    _synthetic_sen1floods11(data)

    cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
    namespace, saved = {}, list(sys.path)
    try:
        for cell in cells:
            source = "".join(cell["source"])
            if cell["cell_type"] != "code" or source.startswith("!"):
                continue
            source = source.replace('LOCAL = r"C:\\sen1floods11"', f'LOCAL = r"{data}"')
            source = source.replace("OUT = None", f'OUT = r"{out}"')
            source = source.replace('EVALUATION = Path.cwd().resolve().parent.parent / "evaluation"',
                                    f'EVALUATION = Path(r"{EVALUATION}")')
            exec(compile(source, str(NOTEBOOK), "exec"), namespace)
    finally:
        sys.path[:] = saved
        sys.modules.pop("figures", None)

    made = {p.stem for p in out.glob("*.png")}
    assert EXPECTED <= made, sorted(EXPECTED - made)


def test_the_figures_read_the_shipped_numbers():
    """The scale chart and the summary must show the 200 m threshold that
    ships (IoU 0.609), not the -20 dB rule it replaced."""
    sys.path.insert(0, str(EVALUATION))
    try:
        import figures as F
        spa = F.load("spatial_results.json")
        assert abs(spa["step1_best_threshold_at_scale"]["200 m"]["test"]["pooled"]["iou"] - 0.609) < 0.001
        text = (EVALUATION / "figures.py").read_text(encoding="utf-8")
        assert 'step1_best_threshold_at_scale"]["200 m"]["test"]["pooled"]["iou"]' in text
    finally:
        sys.path.remove(str(EVALUATION))
        sys.modules.pop("figures", None)
