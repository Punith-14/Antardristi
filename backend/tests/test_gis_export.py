"""
B4: results as GIS files - GeoJSON, KML, CSV and a GeoTIFF of the flood map.

Each file must be valid for the tool it is for, carry the request ID it came
from, and - for the GeoTIFF - never turn ground the satellite did not see
into "dry".
"""

import csv
import io
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from pipeline import export_gis as G  # noqa: E402

KML = "{http://www.opengis.net/kml/2.2}"


def square(lon, lat, size=0.05):
    return {"type": "Polygon", "coordinates": [[
        [lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]]}


def result():
    """A stored flood result as the API returns it, two zones and a district table."""
    zones = [
        {"id": "Z1", "rank": 1, "area_km2": 64.0, "severity": "high",
         "centroid": [76.05, 10.05], "population": {"low": 3100, "high": 4500}},
        {"id": "Z2", "rank": 2, "area_km2": 12.5, "severity": "low",
         "centroid": [76.5, 10.5], "population": None},
    ]
    features = [
        {"type": "Feature", "geometry": square(76.0, 10.0),
         "properties": {"id": "Z1", "rank": 1, "area_km2": 64.0, "severity": "high", "kind": "outline"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [76.05, 10.05]},
         "properties": {"id": "Z1", "rank": 1, "area_km2": 64.0, "severity": "high", "kind": "marker"}},
        # Z2 has no outline: its centre must still be exported.
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [76.5, 10.5]},
         "properties": {"id": "Z2", "rank": 2, "area_km2": 12.5, "severity": "low", "kind": "marker"}},
    ]
    return {
        "request_id": "abc123def4567890abcd",
        "generated_at": "2026-10-01T10:00:00Z",
        "region": {"name": "Testland", "slug": "testland"},
        "period": {"post": {"start": "2018-08-01", "end": "2018-08-31"}},
        "observation": {"sensor_used": "sentinel-1"},
        "evidence": [{"id": "E1", "quantity": "flood_extent", "value": 76.5, "unit": "km2",
                      "method": "Sentinel-1 VV+VH at -18.5 dB for 200 m analysis",
                      "source": "COPERNICUS/S1_GRD IW VV+VH"}],
        "zones": zones,
        "zones_summary": {"count": 5, "listed": 2, "truncated": True},
        "zones_geojson": {"type": "FeatureCollection", "features": features},
        "districts": {"kind": "state_breakdown", "rows": [
            {"rank": 1, "name": "Alappuzha", "state": "Testland", "flooded_km2": 40.0,
             "flooded_pct": 20.0, "observed_pct": 100.0, "low_coverage": False,
             "people": {"low": 6000, "high": 8000}},
            {"rank": 2, "name": "=cmd", "state": "Testland", "flooded_km2": 24.0,
             "flooded_pct": 15.0, "observed_pct": 80.0, "low_coverage": True, "people": None},
        ]},
        "provenance": {"stats_scale_m": 200, "pipeline_version": "0.5.0",
                       "datasets": [{"id": "COPERNICUS/S1_GRD"}]},
    }


# ---------------------------------------------------------------- GeoJSON

def test_geojson_carries_every_zone_once_with_people_and_attribution():
    fc = G.zones_geojson(result())
    ids = [f["properties"]["id"] for f in fc["features"]]
    assert ids == ["Z1", "Z2"], "outline for Z1, centre point for Z2, no duplicate"
    z1 = fc["features"][0]["properties"]
    assert z1["geometry_kind"] == "outline"
    assert (z1["people_low"], z1["people_high"]) == (3100, 4500)
    assert z1["request_id"] == "abc123def4567890abcd"
    assert fc["features"][1]["properties"]["geometry_kind"] == "centre point"
    meta = fc["antardrishti"]
    assert meta["method"].startswith("Sentinel-1")
    assert meta["zones_found"] == 5 and meta["zones_listed"] == 2
    assert "Only the listed zones" in meta["note"]
    json.dumps(fc)                       # serialisable as-is


# -------------------------------------------------------------------- KML

def test_kml_parses_and_colours_by_severity():
    root = ElementTree.fromstring(G.zones_kml(result()).encode("utf-8"))
    placemarks = root.findall(f".//{KML}Placemark")
    assert len(placemarks) == 2
    first = placemarks[0]
    assert first.find(f"{KML}name").text == "Z1"
    assert first.find(f"{KML}styleUrl").text == "#high"
    assert "3,100 to 4,500 people" in first.find(f"{KML}description").text
    assert first.find(f".//{KML}Polygon") is not None
    assert placemarks[1].find(f".//{KML}Point") is not None
    styles = {s.get("id") for s in root.findall(f".//{KML}Style")}
    assert styles == {"high", "moderate", "low"}
    data = {d.get("name"): d.find(f"{KML}value").text for d in first.findall(f".//{KML}Data")}
    assert data["request_id"] == "abc123def4567890abcd"
    assert "abc123def4567890abcd" in root.find(f"{KML}Document/{KML}description").text


def test_kml_escapes_names():
    r = result()
    r["region"]["name"] = "Tom & Jerry <district>"
    ElementTree.fromstring(G.zones_kml(r).encode("utf-8"))      # still well-formed


# -------------------------------------------------------------------- CSV

def test_zone_csv_has_a_header_and_one_row_per_zone():
    rows = list(csv.reader(io.StringIO(G.zones_csv(result()))))
    assert tuple(rows[0]) == G.ZONE_COLUMNS
    assert len(rows) == 3
    assert rows[1][:6] == ["1", "Z1", "64.0", "high", "3100", "4500"]
    assert rows[1][6:8] == ["10.05", "76.05"], "lat before lon, as the header says"


def test_district_csv_marks_partly_seen_and_defuses_formulas():
    rows = list(csv.reader(io.StringIO(G.districts_csv(result()))))
    assert tuple(rows[0]) == G.DISTRICT_COLUMNS
    assert rows[2][1] == "'=cmd", "a name a spreadsheet would execute is quoted"
    assert rows[2][6] == "yes" and rows[1][6] == "no"


def test_no_district_table_no_district_csv():
    r = result()
    r["districts"] = None
    assert G.districts_csv(r) is None


# --------------------------------------------------------- the GeoTIFF plan

KERALA = (74.85, 8.17, 77.42, 12.79)
INDIA = (68.1, 6.7, 97.4, 37.1)


def test_grid_size_counts_square_degree_pixels_as_earth_engine_delivers():
    """EPSG:4326 at a metre scale: pixels are square in degrees, so a degree of
    longitude holds the same number of pixels at 60 N as at the equator."""
    w_eq, h_eq = G.grid_size((0, -0.5, 1, 0.5), 1000)
    w_60, _ = G.grid_size((0, 59.5, 1, 60.5), 1000)
    assert w_eq == 112 and h_eq == 112
    assert w_60 == 112


def test_the_kerala_width_error_the_live_run_found_is_gone():
    """Live smoke run, Kerala at 200 m: planned 1385 wide, delivered 1409. The
    ratio is 1 / cos(Kerala's mid-latitude) - the shortening the old version
    applied and Earth Engine's EPSG:4326 grid does not."""
    import math
    south, north = 8.18, 12.68
    cos_mid = math.cos(math.radians((south + north) / 2))
    assert 1409 / 1385 == pytest.approx(1 / cos_mid, abs=0.002)
    width, height = G.grid_size((75.0, south, 77.5, north), 200)
    assert width == math.ceil(2.5 * 111_320 / 200)   # no cos(latitude) factor


def test_a_state_downloads_at_the_analysis_scale():
    plan = G.download_plan(KERALA, 200)
    assert plan["scale_m"] == 200 and not plan["reduced"]
    assert "analysis scale" in plan["note"]


def test_too_big_goes_coarser_and_says_so():
    plan = G.download_plan(INDIA, 200)
    assert plan["reduced"] and plan["scale_m"] > 200
    assert plan["scale_m"] % 200 == 0
    assert plan["width"] * plan["height"] <= G.MAX_DOWNLOAD_BYTES
    assert f"Delivered at {plan['scale_m']} m; 200 m would exceed" in plan["note"]
    assert "not a new detection" in plan["note"]
    # The chosen step is the FIRST that fits: one step finer would not.
    steps = [200 * s for s in G.SCALE_STEPS]
    finer = steps[steps.index(plan["scale_m"]) - 1]
    w, h = G.grid_size(INDIA, finer)
    assert w * h > G.MAX_DOWNLOAD_BYTES * G.DOWNLOAD_MARGIN or max(w, h) > G.MAX_GRID_SIDE


def test_an_impossible_area_is_refused_in_words():
    plan = G.download_plan((-180, -85, 180, 85), 10)
    assert plan["scale_m"] is None
    assert "too large" in plan["note"]


# --------------------------------------------------------- the GeoTIFF codes

class Codes:
    """A constant-valued integer raster supporting where()."""

    def __init__(self, data):
        self.data = np.array(data, dtype=np.int64)

    def where(self, test, value):
        out = self.data.copy()
        out[test.data & test.valid] = value
        return Codes(out)

    def rename(self, _):
        return self


def codes(flood, valid, permanent, shape):
    from fake_ee import FakeImage
    return G.coded_image(
        FakeImage(flood, valid), FakeImage(valid), FakeImage(permanent, valid),
        lambda value: Codes(np.full(shape, value)),
    ).data


def test_each_class_gets_its_code():
    shape = (1, 4)
    flood = np.array([[1, 0, 0, 0]], bool)
    valid = np.array([[1, 1, 1, 0]], bool)
    permanent = np.array([[0, 0, 1, 0]], bool)
    assert codes(flood, valid, permanent, shape).tolist() == [[1, 0, 2, 255]]


def test_unobserved_ground_is_never_coded_dry():
    """The property the codes exist for, over random masks."""
    rng = np.random.default_rng(7)
    for _ in range(50):
        shape = (12, 12)
        valid = rng.random(shape) > 0.4
        flood = rng.random(shape) > 0.7
        permanent = rng.random(shape) > 0.9
        out = codes(flood, valid, permanent, shape)
        assert (out[~valid] == 255).all()
        assert set(np.unique(out)) <= {0, 1, 2, 255}
        assert (out[valid & flood] == 1).all()


def test_the_zip_carries_tiff_sidecar_and_readme():
    plan = G.download_plan(INDIA, 200)
    content, name = G.geotiff_zip(b"II*\x00fake", result(), plan)
    assert name == f"antardrishti_testland_20180801_abc123de_flood_{plan['scale_m']}m.zip"
    archive = zipfile.ZipFile(io.BytesIO(content))
    names = set(archive.namelist())
    stem = name[:-4]
    assert {f"{stem}.tif", f"{stem}.tif.aux.xml", "README.txt", "attribution.json"} == names
    assert archive.read(f"{stem}.tif") == b"II*\x00fake"

    pam = ElementTree.fromstring(archive.read(f"{stem}.tif.aux.xml"))
    assert pam.find("PAMRasterBand/NoDataValue").text == "255"
    categories = [c.text for c in pam.findall("PAMRasterBand/CategoryNames/Category")]
    assert categories == [G.CODES[i] for i in range(4)], "index = pixel value"
    metadata = {m.get("key"): m.text for m in pam.findall("Metadata/MDI")}
    assert metadata["REQUEST_ID"] == "abc123def4567890abcd"
    assert metadata["DELIVERED_SCALE_M"] == str(plan["scale_m"])

    readme = archive.read("README.txt").decode()
    assert "255  not observed" in readme and "Do not read it as dry" in readme
    assert plan["note"] in readme


# ------------------------------------------------------------- the endpoints

@pytest.fixture
def app(tmp_path, monkeypatch):
    pytest.importorskip("ee")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import flood_scenario as S
    import main
    from core import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    region = S.install(monkeypatch)
    monkeypatch.setattr(main, "resolve_area_or_fail", lambda payload: (region, dict(S.META)))
    client = TestClient(main.app)
    body = client.post("/analyze", json={"region": "testland", "post_start": S.POST[0],
                                         "post_end": S.POST[1], "scale": 200,
                                         "use_llm": False}).json()
    return client, body["request_id"], S, cache


@pytest.mark.parametrize("path,media,ext", [
    ("zones.geojson", "application/geo+json", ".geojson"),
    ("zones.kml", "application/vnd.google-earth.kml+xml", ".kml"),
    ("zones.csv", "text/csv", ".csv"),
])
def test_each_zone_file_downloads_with_a_findable_name(app, path, media, ext):
    client, rid, _, _ = app
    response = client.get(f"/analyze/{rid}/export/{path}")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(media)
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;") and f"{rid[:8]}_zones{ext}" in disposition


def test_a_state_result_has_a_district_csv_and_a_district_result_says_why_not(app, monkeypatch):
    client, rid, _, _ = app
    # The harness switches district breakdown off, so there is no table here.
    response = client.get(f"/analyze/{rid}/export/districts.csv")
    assert response.status_code == 404
    assert "no district table" in response.json()["detail"]


def test_unknown_ids_are_404(app):
    client, _, _, _ = app
    for path in ("zones.kml", "zones.csv", "flood.zip", "flood-plan"):
        assert client.get(f"/analyze/nope/export/{path}").status_code == 404


def test_the_geotiff_is_rebuilt_from_the_stored_request_with_the_sensor_used(app, monkeypatch):
    client, rid, S, _ = app
    from pipeline import analysis

    seen = {}
    real_layers = analysis.flood_layers

    def layers(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return real_layers(*args, **kwargs)

    monkeypatch.setattr(analysis, "flood_layers", layers)
    monkeypatch.setattr(G, "region_bounds", lambda geometry: KERALA)
    monkeypatch.setattr(G, "download_url", lambda layers, region, plan: (
        seen.update(plan=plan, layers=layers) or "https://example.invalid/x"))
    monkeypatch.setattr(G, "fetch", lambda url: b"II*\x00fake")

    response = client.get(f"/analyze/{rid}/export/flood.zip")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert seen["args"][1:3] == S.POST
    assert seen["kwargs"]["sensor"] == "sentinel-1"
    assert seen["kwargs"]["scale"] == 200
    assert seen["plan"]["scale_m"] == 200
    # The rebuilt mask is the analysis's own: 64 km2 of flood, permanent water out.
    assert seen["layers"]["flood"].area_km2() == 64.0
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert any(n.endswith("_flood_200m.tif") for n in names)


def test_the_plan_endpoint_says_the_scale_before_downloading(app, monkeypatch):
    client, rid, _, _ = app
    monkeypatch.setattr(G, "region_bounds", lambda geometry: INDIA)
    plan = client.get(f"/analyze/{rid}/export/flood-plan").json()
    assert plan["reduced"] and plan["scale_m"] > 200


def test_a_result_from_an_earlier_rule_is_not_rebuilt_under_the_new_one(app):
    client, rid, _, cache = app
    path = cache.CACHE_DIR / f"{rid}.json"
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["request"]["flood_rule"] = "fused_vvvh_minus20_v1"
    path.write_text(json.dumps(entry), encoding="utf-8")

    response = client.get(f"/analyze/{rid}/export/flood.zip")
    assert response.status_code == 409
    assert "different map" in response.json()["detail"]
    # The zone files are views of the stored result and still download.
    assert client.get(f"/analyze/{rid}/export/zones.csv").status_code == 200


def test_flood_layers_matches_the_analysis_mask(monkeypatch):
    pytest.importorskip("ee")
    import flood_scenario as S
    from pipeline import analysis

    result = S.run(monkeypatch, "sentinel-1", baseline=False)
    region = S.install(monkeypatch)
    layers = analysis.flood_layers(region, *S.POST, sensor="sentinel-1", scale=100)
    assert (layers["flood"].data & layers["flood"].valid).tolist() == (
        result["_internal"]["flood_mask"].data & result["_internal"]["flood_mask"].valid).tolist()


class Recorder:
    """Records the Earth Engine calls download_url makes, in order."""

    def __init__(self, log):
        self.log = log

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.log.append((name, args, kwargs))
            return self
        return call


@pytest.mark.parametrize("bounds,reduced", [(KERALA, False), (INDIA, True)])
def test_the_map_is_fixed_at_the_analysis_scale_before_any_coarsening(monkeypatch, bounds, reduced):
    """Without the reproject, Earth Engine would re-run detection at the
    coarser scale - a different map from the one the numbers describe."""
    ee = pytest.importorskip("ee")
    log = []
    monkeypatch.setattr(ee.Image, "constant", lambda value: Recorder(log))
    monkeypatch.setattr(ee.Reducer, "mode", lambda: "MODE")
    plan = G.download_plan(bounds, 200)
    rec = Recorder(log)
    G.download_url({"flood": rec, "valid": rec, "permanent": rec}, rec, plan)

    names = [name for name, _, _ in log]
    reproject = names.index("reproject")
    assert log[reproject][2] == {"crs": "EPSG:4326", "scale": 200}
    assert ("reduceResolution" in names) == reduced
    if reduced:
        assert names.index("reduceResolution") > reproject
        assert log[names.index("reduceResolution")][1][0] == "MODE"
    download = log[names.index("getDownloadURL")][1][0]
    assert download["scale"] == plan["scale_m"]
    assert download["format"] == "GEO_TIFF"
    assert names.index("getDownloadURL") > reproject
