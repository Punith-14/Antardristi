# Backend layout

Every module used to sit flat in this directory — thirty-odd files with no
indication of which were the service, which were run by hand, and which were
dead. They are now grouped by what they do.

```
backend/
  main.py             FastAPI app. The only entry point.
  pytest.ini
  requirements.txt

  core/               Infrastructure. Imports nothing of ours.
    paths.py            where everything lives, computed once
    earth_engine.py     one initialiser, idempotent
    cache.py            analyses keyed by request
    evidence.py         the evidence record
    verification.py     checks a report against that record
    alignment.py        checks the route against the QUESTION (May-vs-May)

  geo/                Where an analysis runs, and how it is drawn.
    regions.py          names, through FAO GAUL 2015
    footprint.py        areas the user draws instead (box, circle, polygon)
    indices.py          spectral indices on Sentinel-2
    mapping.py          display hints; "solid" only for validated methods
    zones.py            contiguous regions, vectorised and ranked

  detection/          Imagery into masks. The measurement itself.
    sar.py              Sentinel-1 flood, mean(VV,VH); threshold per scale
                        (-20 dB at 10 m, -19 at 100 m, -18.5 at 200 m,
                        notebook 07); change detection opt-in (notebook 06)
    optical.py          Sentinel-2 flood, same evidence shape as sar.py;
                        MNDWI > 0.15, IoU 0.743 on L1C (see its caveat)
    surface.py          six optical analyses
    classifier.py       land-cover Random Forest, run inside Earth Engine
    rgb_upload.py       uploaded photos: pixel fractions, never km2

  pipeline/           Question in, evidence-bound answer out.
    routing.py          plain language into a structured query
    analysis.py         runs it (either sensor), builds the evidence record
    report.py           writes the prose, which is then verified
    timeseries.py       one full analysis per month, gaps kept as gaps
    export_pdf.py       the stored response as a PDF; nothing recomputed

  legacy/             The pre-evidence-record path, still serving /query.
    gee_fetch.py        Kept working, not extended.
    ndwi.py             superseded by detection/rgb_upload.py; unused
    query_parser.py

  scripts/            Run by hand. Measurement, training, diagnosis.
    fetch_pre_event.py  baselines for notebook 06 (resumable, slow)
  manual_checks/      End-to-end checks a person reads.
  tests/              The pytest suite (534 at last count). fake_ee.py and
                      flood_scenario.py run the whole flood path on numpy grids.
  notebooks/          Measurement runs. 05 = optical flood, 06 = change detection.
  results/            Generated output from scripts/.
```

## Endpoints added in September 2026

| endpoint | what it does |
|---|---|
| `POST /analyze` with `"sensor": "sentinel-2"` | optical flood, same evidence quantities as radar |
| `POST /analyze` with `"method": "change"` | radar change detection against the pre window |
| `POST /analyze/series` | flood extent month by month, up to 12 months |
| `GET /analyze/{id}/report.pdf[?ask_id=]` | the stored analysis as a PDF |
| `POST /analyze-upload` | now returns an evidence record and a `request_id` |

Both measured numbers are filled: `VALIDATION` in `detection/optical.py`
(notebook 05) and `CHANGE_VALIDATION` in `detection/sar.py` (notebook 06), on
2026-09-30. Tests tie each to the results file its notebook wrote, so they
cannot be edited by hand or left behind when a threshold changes.

## Running things

The app, from this directory:

```bash
uvicorn main:app --reload
```

Tests — fast by default, no credentials, no network, no Earth Engine quota:

```bash
pytest
pytest -m earthengine      # the slow ones, explicitly
```

Scripts and manual checks run **as modules**, so that `backend/` is on the
import path and `from detection import sar` resolves:

```bash
python -m scripts.measure_boundary_coverage
python -m scripts.train_landcover --trees 60 --depth 10
python -m manual_checks.api_smoke          # needs uvicorn running
```

Running `python scripts/train_landcover.py` directly will fail — that puts
`scripts/` on the path rather than `backend/`.

## Two conventions worth keeping

**Paths come from `core.paths`, never from `Path(__file__).parent.parent`.**
Nine modules used to compute their own, which encoded how deep in the tree each
file happened to sit. Moving one changed what `parent.parent` meant, silently,
because a wrong path does not raise until something reads it.

**`core` imports nothing of ours.** If something in `core` needs `geo` or
`detection`, it belongs in that layer instead. Everything else may import
downwards.

## Why `manual_checks` is not `tests`

Those four files were named `test_*.py` and sat at the backend root, which made
them look like the suite. They never were: `pytest.ini` sets
`testpaths = tests`, so they had never once been collected. They need a running
server, real Earth Engine quota, or a Groq key — which is exactly why they are
not automatic. Renamed so the distinction is visible.
