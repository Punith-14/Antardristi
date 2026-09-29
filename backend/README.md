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

  geo/                Where an analysis runs, and how it is drawn.
    regions.py          names, through FAO GAUL 2015
    footprint.py        areas the user draws instead
    indices.py          spectral indices on Sentinel-2
    mapping.py          display hints
    zones.py            contiguous regions, vectorised and ranked

  detection/          Imagery into masks. The measurement itself.
    sar.py              Sentinel-1 flood, mean(VV,VH) at -20 dB
    surface.py          six optical analyses
    classifier.py       land-cover Random Forest, run inside Earth Engine

  pipeline/           Question in, evidence-bound answer out.
    routing.py          plain language into a structured query
    analysis.py         runs it, builds the evidence record
    report.py           writes the prose, which is then verified

  legacy/             The pre-evidence-record path, still serving /query
    gee_fetch.py        and the upload endpoint. Kept working, not extended.
    ndwi.py
    query_parser.py

  scripts/            Run by hand. Measurement, training, diagnosis.
  manual_checks/      End-to-end checks a person reads.
  tests/              The pytest suite. 314 tests.
  notebooks/          Colab notebooks for the measurement runs.
  results/            Generated output from scripts/.
```

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
