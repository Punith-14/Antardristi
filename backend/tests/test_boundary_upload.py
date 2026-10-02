"""
D2: an official boundary uploaded as a file.

Each format reads; each bad file is refused with the reason (projected
coordinates, swapped axes, outside India, crossing outlines); detail is
simplified with the area change reported; the result names the file and
never calls the boundary official.
"""

import io
import json
import math
import sys
import zipfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

pytest.importorskip("shapely")

from geo import boundary_upload as B  # noqa: E402
from geo import footprint as F  # noqa: E402


def square(lon, lat, size=0.1):
    return [[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]


def geojson(*features, crs=None):
    doc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": props,
         "geometry": {"type": "Polygon", "coordinates": [ring]}} for props, ring in features]}
    if crs:
        doc["crs"] = {"type": "name", "properties": {"name": crs}}
    return json.dumps(doc).encode()


KERALA = [({"DISTRICT": "Alappuzha"}, square(76.3, 9.4)),
          ({"DISTRICT": "Kottayam"}, square(76.5, 9.5))]


# ------------------------------------------------------------- formats

def test_geojson_lists_every_shape_by_its_own_name_field():
    parsed = B.parse("districts.geojson", geojson(*KERALA))
    assert [f["name"] for f in parsed["features"]] == ["Alappuzha", "Kottayam"]
    assert all(f["ok"] for f in parsed["features"])
    assert parsed["file"]["format"] == "geojson" and len(parsed["file"]["sha256"]) == 64
    assert "geometry" not in json.dumps(B.summary(parsed)["features"])


def kml(with_hole=True):
    hole = ("<innerBoundaryIs><LinearRing><coordinates>76.32,9.42 76.34,9.42 76.34,9.44 "
            "76.32,9.42</coordinates></LinearRing></innerBoundaryIs>") if with_hole else ""
    return (
        '<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        "<Placemark><name>Kuttanad</name><Polygon><outerBoundaryIs><LinearRing><coordinates>"
        "76.3,9.4,0 76.4,9.4,0 76.4,9.5,0 76.3,9.5,0 76.3,9.4,0"
        f"</coordinates></LinearRing></outerBoundaryIs>{hole}</Polygon></Placemark>"
        "</Document></kml>").encode()


def test_kml_reads_name_outline_and_hole_and_drops_heights():
    parsed = B.parse("taluk.kml", kml())
    feature = parsed["features"][0]
    assert feature["name"] == "Kuttanad" and feature["ok"]
    rings = feature["geometry"]["coordinates"]
    assert len(rings) == 2, "outer ring and hole"
    assert all(len(p) == 2 for p in rings[0]), "heights dropped"
    without = B.parse("taluk.kml", kml(with_hole=False))["features"][0]["info"]["area_km2"]
    assert feature["info"]["area_km2"] < without, "the hole is subtracted"


def test_kmz_is_read_from_inside_the_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("doc.kml", kml())
    assert B.parse("taluk.kmz", buffer.getvalue())["features"][0]["name"] == "Kuttanad"


WGS84_PRJ = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137,'
             '298.257223563]],PRIMEM["Greenwich",0],UNIT["Degree",0.0174532925199433]]')
UTM_PRJ = 'PROJCS["WGS_1984_UTM_Zone_43N",GEOGCS["GCS_WGS_1984"]]'


def shapefile_zip(prj=WGS84_PRJ, rings=None):
    import shapefile
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON) as w:
        w.field("dtname", "C")
        for name, ring in rings or [("Wayanad", square(76.0, 11.6))]:
            w.poly([ring])
            w.record(name)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for ext, part in (("shp", shp), ("shx", shx), ("dbf", dbf)):
            z.writestr(f"district.{ext}", part.getvalue())
        if prj:
            z.writestr("district.prj", prj)
    return buffer.getvalue()


def test_a_zipped_shapefile_is_read_with_its_attribute_names():
    parsed = B.parse("district.zip", shapefile_zip())
    assert parsed["file"]["format"] == "shapefile"
    assert parsed["features"][0]["name"] == "Wayanad"
    assert parsed["features"][0]["ok"]


def test_a_bare_shp_is_refused_with_how_to_fix_it():
    with pytest.raises(B.UnreadableBoundary, match="Zip them together"):
        B.parse("district.shp", b"\x00\x00\x27\x0a")


# ------------------------------------------------------------- refusals

def test_a_projected_shapefile_is_refused_not_guessed():
    with pytest.raises(B.UnreadableBoundary, match="projected coordinate system"):
        B.parse("utm.zip", shapefile_zip(prj=UTM_PRJ))


def test_a_geojson_declaring_another_crs_is_refused():
    with pytest.raises(B.UnreadableBoundary, match="EPSG:4326"):
        B.parse("x.geojson", geojson(*KERALA, crs="urn:ogc:def:crs:EPSG::32643"))


def test_metre_coordinates_are_recognised_as_projected():
    utm = [[500000, 1040000], [510000, 1040000], [510000, 1050000], [500000, 1040000]]
    with pytest.raises(B.UnreadableBoundary, match="look like metres"):
        B.parse("x.geojson", geojson(({}, utm)))


def test_swapped_axes_are_named():
    swapped = [[p[1], p[0]] for p in square(76.3, 9.4)]
    with pytest.raises(B.UnreadableBoundary, match="swapped"):
        B.parse("x.geojson", geojson(({}, swapped)))


def test_outside_india_is_refused():
    with pytest.raises(B.UnreadableBoundary, match="outside India"):
        B.parse("x.geojson", geojson(({}, square(2.3, 48.8))))


def test_a_crossing_outline_is_refused_with_the_fix():
    bowtie = [[76.3, 9.4], [76.4, 9.5], [76.4, 9.4], [76.3, 9.5], [76.3, 9.4]]
    with pytest.raises(B.UnreadableBoundary, match="Fix Geometries"):
        B.parse("x.geojson", geojson(({}, bowtie)))


def test_one_bad_shape_does_not_sink_the_file():
    bowtie = [[76.3, 9.4], [76.4, 9.5], [76.4, 9.4], [76.3, 9.5], [76.3, 9.4]]
    parsed = B.parse("x.geojson", geojson(({"name": "good"}, square(76.3, 9.4)),
                                          ({"name": "bad"}, bowtie)))
    ok = {f["name"]: f["ok"] for f in parsed["features"]}
    assert ok == {"good": True, "bad": False}
    with pytest.raises(B.UnreadableBoundary, match="cannot be analysed"):
        B.request_boundary(parsed, 1)


# ------------------------------------------------------ area and detail

def test_spherical_area_is_right():
    # 0.1 x 0.1 degrees at about 10 N: 11.13 km by 10.96 km.
    area = B.ring_area_km2(square(76.0, 9.95))
    assert area == pytest.approx(11.12 * 10.95, rel=0.01)


def circle(n, lon=76.5, lat=10.0, r=0.2):
    ring = [[lon + r * math.cos(2 * math.pi * i / n), lat + r * math.sin(2 * math.pi * i / n)]
            for i in range(n)]
    return ring + [ring[0]]


def test_a_detailed_outline_is_simplified_and_the_change_is_reported():
    parsed = B.parse("detailed.geojson", geojson(({"name": "round"}, circle(20000))))
    info = parsed["features"][0]["info"]
    assert info["simplified"] is True
    assert info["original_vertices"] == 20001
    assert info["vertices"] <= B.MAX_VERTICES
    assert abs(info["area_change_pct"]) < 0.5
    boundary = B.request_boundary(parsed, 0)
    assert boundary["source"]["area_change_pct"] == info["area_change_pct"]


def test_a_small_outline_is_left_alone():
    info = B.parse("x.geojson", geojson(*KERALA))["features"][0]["info"]
    assert info["simplified"] is False and info["area_change_pct"] == 0.0


# ------------------------------------------------- footprint and labelling

def test_the_footprint_names_the_file_and_never_claims_official():
    parsed = B.parse("districts.geojson", geojson(*KERALA))
    shape = F.from_boundary(B.request_boundary(parsed, 1))
    meta = F.metadata(shape)
    assert meta["name"] == "Kottayam (uploaded boundary)"
    assert meta["admin_level"] == "custom" and meta["state"] is None
    assert "districts.geojson" in meta["boundary_source"]
    assert parsed["file"]["sha256"][:12] in meta["boundary_source"]
    assert "not been checked against any official source" in meta["note"]
    assert "geometry" not in meta["footprint"]


def test_a_simplified_boundary_says_so_in_the_note():
    parsed = B.parse("detailed.geojson", geojson(({"name": "round"}, circle(20000))))
    meta = F.metadata(F.from_boundary(B.request_boundary(parsed, 0)))
    assert "simplified from 20,001" in meta["note"] and "changing its area by" in meta["note"]


def test_the_api_rechecks_a_boundary_it_did_not_parse():
    with pytest.raises(F.InvalidFootprint, match="outside India"):
        F.from_boundary({"type": "Polygon", "coordinates": [square(2.3, 48.8)]})
    with pytest.raises(F.InvalidFootprint, match="Polygon or MultiPolygon"):
        F.from_boundary({"type": "Point", "coordinates": [76, 10]})
    with pytest.raises(F.InvalidFootprint, match="one area, not 2"):
        F.build(polygon=square(76, 10)[:-1],
                boundary={"type": "Polygon", "coordinates": [square(76, 10)]})


# ------------------------------------------------------------- endpoints

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
    monkeypatch.setattr(B, "STORE", tmp_path / "boundaries")
    S.install(monkeypatch)
    from fake_ee import FakeRegion
    monkeypatch.setattr(F, "geometry", lambda shape: FakeRegion(S.SHAPE))
    return TestClient(main.app), S


def upload(client, name, data):
    return client.post("/boundary/parse", files={"file": (name, data, "application/octet-stream")})


def test_one_shape_comes_back_ready_to_analyse(app):
    client, S = app
    body = upload(client, "kuttanad.kml", kml()).json()
    assert body["boundary"]["source"]["feature"] == "Kuttanad"
    response = client.post("/analyze", json={"boundary": body["boundary"], "post_start": S.POST[0],
                                             "post_end": S.POST[1], "use_llm": False})
    assert response.status_code == 200, response.text
    region = response.json()["region"]
    assert region["name"] == "Kuttanad (uploaded boundary)"
    assert region["footprint"]["kind"] == "boundary"


def test_several_shapes_are_picked_by_index_without_reuploading(app):
    client, _ = app
    body = upload(client, "districts.geojson", geojson(*KERALA)).json()
    assert "boundary" not in body and len(body["features"]) == 2
    sha = body["file"]["sha256"]
    picked = client.get(f"/boundary/{sha}/1").json()
    assert picked["source"]["feature"] == "Kottayam"
    assert client.get(f"/boundary/{sha}/7").status_code == 422
    assert client.get(f"/boundary/{'0' * 64}/0").status_code == 404
    assert client.get("/boundary/../../etc/0").status_code == 404


def test_a_bad_file_is_a_422_that_says_why(app):
    client, _ = app
    response = upload(client, "utm.zip", shapefile_zip(prj=UTM_PRJ))
    assert response.status_code == 422
    assert "projected" in response.json()["detail"]["message"]


def test_adding_the_boundary_field_rekeyed_nothing(app):
    import main
    from detection import sar
    request = main.FloodRequest(region="kerala", post_start="2018-08-01", post_end="2018-08-31")
    assert "boundary" not in request.cache_key()
