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
    scenes.py           which satellite scenes a result came from, by ID
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
    export_gis.py       GeoJSON, KML, CSV; GeoTIFF of codes (1 flood, 0 dry,
                        2 permanent water, 255 not observed)

  legacy/             The pre-evidence-record path, still serving /query.
    gee_fetch.py        Kept working, not extended.
    ndwi.py             superseded by detection/rgb_upload.py; unused
    query_parser.py

  scripts/            Run by hand. Measurement, training, diagnosis.
    fetch_pre_event.py  baselines for notebook 06 (resumable, slow)
    fetch_terrain.py    HAND + slope per chip for notebook 09 (resumable)
  manual_checks/      End-to-end checks a person reads.
  tests/              The pytest suite (800+ at last count). fake_ee.py and
                      flood_scenario.py run the whole flood path on numpy grids.
  notebooks/          Measurement runs. 05 = optical flood, 06 = change detection,
                      07 = scale, 08 = India, 09 = terrain check.
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

## Added with Group B (October 2026)

| endpoint / field | what it does |
|---|---|
| `scenes` in every flood result | each Sentinel-1/2 scene used, post and baseline apart, with an Earth Engine snippet that loads them again (`core/scenes.py`) |
| `GET /analyses` -> `flood.methods` | the three sensor/method choices, each with its measured accuracy read from the detectors' constants |
| `GET /analyze/{id}/export/zones.geojson` | zone outlines with people and attribution |
| `GET /analyze/{id}/export/zones.kml` | the same for Google Earth, coloured by severity |
| `GET /analyze/{id}/export/zones.csv`, `districts.csv` | the tables |
| `GET /analyze/{id}/export/flood-plan` | the scale the GeoTIFF will be delivered at |
| `GET /analyze/{id}/export/flood.zip` | the flood map as a GeoTIFF of codes, with a GDAL `.aux.xml` and README; rebuilt from the stored request, refused (409) if the result predates the current detection rule |

`latest: true` now refuses `sensor: "sentinel-2"` and `method: "change"` (400),
and `cloud_limit` must be 1-100.

## Added with Group C (October 2026): the terrain check

Dark radar pixels high above the nearest drainage (HAND) or on steep slopes
(radar shadow) cannot be flood. Whether removing them helps is MEASURED first:

```bash
python -m scripts.fetch_terrain --sen1floods11 C:\sen1floods11
# then run notebooks/09_terrain_check.ipynb
```

Notebook 09 chooses the thresholds on the train split, scores test/valid/
Bolivia/India, and applies a decision rule written before the run (test IoU at
200 m up with a bootstrap range above zero; Indian recall down by at most
0.02). Only if it prints SHIP does `sar.TERRAIN_RULE` get filled; until then
the flood path is unchanged. Once filled:

| field | what it does |
|---|---|
| `water_excluded_by_terrain` evidence | the dark area not counted, and why |
| `terrain` in the result | applied or not, thresholds, excluded km2, or the reason it did not run |
| `terrain_check: false` in `/analyze` | switches it off (the old map) |
| `terrain_excluded_tiles` | map layer, magenta; GeoTIFF code 3 |
| `flood.terrain` in `/analyses` | the rule and its measured accuracy |

Cache keys gain `terrain_rule` the day a rule ships, so every result is
recomputed under it; GeoTIFFs of older results are refused (409).

**Result (notebook 09): measured, not shipped.** HAND <= 10 m (MERIT), chosen
on train, lifted test IoU at 200 m from 0.609 to 0.621 and precision from
0.807 to 0.841, but the gain's bootstrap range (-0.010 to +0.051) includes
zero and held-out Indian recall fell 0.024 (limit 0.02) - it removed real
Brahmaputra floodplain water. `TERRAIN_RULE` stays None; a test keeps it so
while `terrain_results.json` says do not ship.

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
