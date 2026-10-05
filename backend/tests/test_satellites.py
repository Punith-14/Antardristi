"""
The live satellite status: CelesTrak elements, ESA's acquisition plans, the
newest Earth Engine image, and the /satellites endpoints.

No test touches the network: every outside source is replaced by a fake that
returns what the real one returns (formats copied from the live sources on
3 Oct 2026).
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core import paths  # noqa: E402
from orbits import celestrak, latest, plans, service  # noqa: E402

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CACHE", tmp_path / "cache")
    plans.reset()
    latest.reset()
    celestrak._last_failure.update(at=None, reason=None)
    service._shapes.clear()
    service._india_failed["at"] = None
    yield


# ------------------------------------------------------------ fixtures

PAGE = """
<h4>Sentinel-1D</h4>
<a href="https://sentinels.copernicus.eu/documents/d/sentinel/s1d_mp_user_20261002t181009_20261022t201800">02 - 22 October 2026</a>
<a href="https://sentinels.copernicus.eu/documents/d/sentinel/s1d_mp_user_20261001t183130_20261021t201500">01 - 21 October 2026</a>
<h4>Sentinel-1C</h4>
<a href="https://sentinels.copernicus.eu/documents/d/sentinel/s1c_mp_user_20261002t172332_20261024t194000">02 - 24 October</a>
<a href="https://sentinels.copernicus.eu/documents/d/sentinel/s1c_mp_user_20261002t172332_20261024t194000">same file again</a>
<a href="https://sentinels.copernicus.eu/documents/d/sentinel/s1d_mp_user_20260916t180643_20260922t194900">a 1D file under the 1C heading</a>
<a href="https://sentinels.copernicus.eu/documents/d/sentinel/s1c_mp_user_20260717t171528_20260809t194000?download=true">old</a>
<h4>Sentinel-1A</h4>
<a href="https://sentinels.copernicus.eu/documents/d/sentinel/s1a_mp_user_20260625t173940_20260630t194000">25 - 30 June 2026</a>
"""


def placemark(sat, start, stop, ring, mode="IW", pol="DV", rel=161):
    coords = " ".join(f"{x},{y},0" for x, y in ring)
    return f"""
    <Placemark><name>{start}</name>
      <TimeSpan><begin>{start}</begin><end>{stop}</end></TimeSpan>
      <ExtendedData>
        <Data name="SatelliteId"><value>{sat}</value></Data>
        <Data name="DatatakeId"><value>13561</value></Data>
        <Data name="Mode"><value>{mode}</value></Data>
        <Data name="Swath"><value>NA</value></Data>
        <Data name="Polarisation"><value>{pol}</value></Data>
        <Data name="ObservationTimeStart"><value>{start}</value></Data>
        <Data name="ObservationTimeStop"><value>{stop}</value></Data>
        <Data name="ObservationDuration"><value>651</value></Data>
        <Data name="OrbitAbsolute"><value>9709</value></Data>
        <Data name="OrbitRelative"><value>{rel}</value></Data>
      </ExtendedData>
      <LinearRing><tessellate>true</tessellate><altitudeMode>clampToGround</altitudeMode>
        <coordinates>{coords}</coordinates></LinearRing>
    </Placemark>"""


def kml(*marks):
    return ('<?xml version="1.0" encoding="iso-8859-1"?>'
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder><name>2026-10-03</name>'
            + "".join(marks) + "</Folder></Document></kml>")


OVER_KERALA = [(75.0, 9.0), (77.5, 9.0), (77.5, 12.0), (75.0, 12.0), (75.0, 9.0)]
OVER_ASSAM = [(91.0, 25.5), (94.0, 25.5), (94.0, 27.5), (91.0, 27.5), (91.0, 25.5)]
OVER_EUROPE = [(5.0, 45.0), (8.0, 45.0), (8.0, 48.0), (5.0, 48.0), (5.0, 45.0)]

S1C_KML = kml(
    placemark("S1C", "2026-10-03T00:40:00", "2026-10-03T00:42:00", OVER_KERALA),       # finished
    placemark("S1C", "2026-10-03T11:58:00", "2026-10-03T12:03:00", OVER_ASSAM),        # under way
    placemark("S1C", "2026-10-04T00:40:00", "2026-10-04T00:42:00", OVER_KERALA, pol="SV"),
    placemark("S1C", "2026-10-04T17:00:00", "2026-10-04T17:05:00", OVER_EUROPE),
)
S1D_KML = kml(placemark("S1D", "2026-10-05T12:52:00", "2026-10-05T12:55:00", OVER_KERALA, mode="EW", rel=63))


def fake_fetcher(calls=None, fail=()):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        if any(f in url for f in fail):
            raise OSError("down")
        if url == plans.PAGE:
            return PAGE
        if "s1c_mp_user_20261002" in url:
            return S1C_KML
        if "s1d_mp_user_20261002" in url:
            return S1D_KML
        raise AssertionError(f"unexpected url {url}")
    return fetch


# ------------------------------------------------------------ plan files

def test_plan_links_come_from_file_names_once_each():
    found = plans.links(PAGE)
    names = [f["name"] for f in found]
    assert len(names) == len(set(names)) == 6, "the duplicate link is dropped"
    misfiled = next(f for f in found if "20260916" in f["name"])
    assert misfiled["satellite"] == "S1D", "the file name decides, not the heading"
    old = next(f for f in found if "20260717" in f["name"])
    assert "?" not in old["url"] and old["satellite"] == "S1C"
    assert old["start"] == datetime(2026, 7, 17, 17, 15, 28, tzinfo=timezone.utc)


def test_relative_links_are_read_too():
    """The page links some files relative to the site. Requiring the host
    dropped all of Sentinel-1D and the newest 1C plans (October 2026)."""
    html = ('<a href="/documents/d/sentinel/s1d_mp_user_20261002t181009_20261022t201800">1D</a>'
            "<a href='https://sentinels.copernicus.eu/documents/d/sentinel/"
            "s1c_mp_user_20260925t173103_20261017t194000'>1C old</a>"
            '<a href="/documents/d/sentinel/s1c_mp_user_20261002t172332_20261024t194000">1C new</a>')
    found = plans.links(html)
    assert {f["satellite"] for f in found} == {"S1C", "S1D"}
    assert all(f["url"].startswith("https://sentinels.copernicus.eu/documents/d/sentinel/") for f in found)
    chosen = plans.current(found, NOW)
    assert [f["name"][:27] for f in chosen] == ["s1c_mp_user_20261002t172332", "s1d_mp_user_20261002t181009"]


def test_the_newest_file_covering_now_is_used_and_ended_missions_drop_out():
    chosen = plans.current(plans.links(PAGE), NOW)
    assert [f["satellite"] for f in chosen] == ["S1C", "S1D"], "1A's plan ended in June"
    assert chosen[1]["name"].startswith("s1d_mp_user_20261002"), "the later of two 1D files"


def test_a_plan_file_parses_into_data_takes():
    takes = plans.parse_kml(S1C_KML)
    assert len(takes) == 4 and takes[0]["start"] == "2026-10-03T00:40:00Z"
    take = takes[2]
    assert take["satellite"] == "S1C" and take["mode"] == "IW"
    assert take["mode_label"] == "Interferometric Wide swath"
    assert take["polarisation"] == "VV", "SV means VV only"
    assert take["orbit_relative"] == 161 and take["duration_s"] == 651
    assert take["footprint"][0] == [75.0, 9.0]


def test_upcoming_images_over_an_area_skip_finished_ones_and_flag_one_under_way():
    from shapely.geometry import Polygon
    india = Polygon(service.INDIA_APPROX)
    takes = plans.parse_kml(S1C_KML) + plans.parse_kml(S1D_KML)
    takes.sort(key=lambda t: t["start"])
    soon = plans.upcoming(takes, india, now=NOW, limit=5)
    assert [t["start"] for t in soon] == ["2026-10-03T11:58:00Z", "2026-10-04T00:40:00Z", "2026-10-05T12:52:00Z"]
    assert soon[0]["in_progress"] and not soon[1]["in_progress"]
    kerala = Polygon([(76.0, 10.0), (76.5, 10.0), (76.5, 10.5), (76.0, 10.5)])
    assert [t["satellite"] for t in plans.upcoming(takes, kerala, now=NOW)] == ["S1C", "S1D"]
    assert len(plans.upcoming(takes, india, now=NOW, limit=1)) == 1


def test_the_plan_is_read_once_kml_files_are_kept_and_failures_fall_back():
    calls = []
    first = plans.plan(now=NOW, fetcher=fake_fetcher(calls))
    assert [f["satellite"] for f in first["files"]] == ["S1C", "S1D"]
    assert len(first["datatakes"]) == 5 and first["error"] is None and not first["stale"]
    assert calls.count(plans.PAGE) == 1

    calls.clear()
    plans.plan(now=NOW + timedelta(hours=1), fetcher=fake_fetcher(calls))
    assert calls == [], "page cached for hours, KML files cached for good"

    later = NOW + timedelta(hours=plans.PAGE_REFRESH_S / 3600 + 1)
    stale = plans.plan(now=later, fetcher=fake_fetcher(fail=[plans.PAGE]))
    assert stale["stale"] and "could not be read" in stale["error"]
    assert len(stale["datatakes"]) == 5, "the last good list of files is still used"


def test_one_unreadable_plan_file_does_not_hide_the_other():
    result = plans.plan(now=NOW, fetcher=fake_fetcher(fail=["s1d_mp_user_20261002"]))
    assert {t["satellite"] for t in result["datatakes"]} == {"S1C"}
    assert "s1d_mp_user_20261002" in result["error"]


# ------------------------------------------------------------ CelesTrak

def omm(name, norad):
    return {"OBJECT_NAME": name, "OBJECT_ID": "2024-235A", "EPOCH": "2026-10-03T06:12:01.123",
            "MEAN_MOTION": 14.59198, "ECCENTRICITY": 0.0001, "INCLINATION": 98.18,
            "RA_OF_ASC_NODE": 280.1, "ARG_OF_PERICENTER": 80.2, "MEAN_ANOMALY": 279.9,
            "EPHEMERIS_TYPE": 0, "CLASSIFICATION_TYPE": "U", "NORAD_CAT_ID": norad,
            "ELEMENT_SET_NO": 999, "REV_AT_EPOCH": 9700, "BSTAR": 0.00002,
            "MEAN_MOTION_DOT": 0.0000012, "MEAN_MOTION_DDOT": 0}


RECORDS = [omm("SENTINEL-1D", 66001), omm("SENTINEL-1C", 62261), omm("SENTINEL-1A", 39634),
           omm("SENTINEL-2B", 42063), {"OBJECT_NAME": "SENTINEL-1X"}]


def test_celestrak_records_become_labelled_sentinel1_elements():
    sats = celestrak.parse(RECORDS)
    assert [s["name"] for s in sats] == ["Sentinel-1A", "Sentinel-1C", "Sentinel-1D"]
    assert [s["key"] for s in sats] == ["S1A", "S1C", "S1D"]
    assert sats[1]["norad"] == 62261 and sats[1]["omm"]["MEAN_MOTION"] == 14.59198


def test_elements_are_cached_for_two_hours_and_served_stale_when_celestrak_is_down():
    calls = []

    def fetch():
        calls.append(1)
        return RECORDS

    first = celestrak.elements(now=NOW, fetcher=fetch)
    assert len(first["satellites"]) == 3 and not first["stale"] and first["fetched_at"] == "2026-10-03T12:00:00Z"
    celestrak.elements(now=NOW + timedelta(minutes=90), fetcher=fetch)
    assert len(calls) == 1, "not asked again within two hours"

    def down():
        calls.append(1)
        raise OSError("no route")

    stale = celestrak.elements(now=NOW + timedelta(hours=3), fetcher=down)
    assert stale["stale"] and len(stale["satellites"]) == 3 and "could not be reached" in stale["error"]
    celestrak.elements(now=NOW + timedelta(hours=3, minutes=5), fetcher=down)
    assert len(calls) == 2, "after a failure, wait before asking again"


def test_with_no_copy_at_all_the_list_is_empty_and_says_why():
    result = celestrak.elements(now=NOW, fetcher=lambda: (_ for _ in ()).throw(TimeoutError()))
    assert result["satellites"] == [] and result["stale"] and "TimeoutError" in result["error"]


# ------------------------------------------------------------ Earth Engine's newest image

def test_the_newest_image_is_a_real_scene_and_is_cached():
    calls = []

    def query(geometry, now):
        calls.append(geometry)
        return {"count": 12, "id": "S1C_IW_GRDH_1SDV_20261003T003955", "time": 1791002395000,
                "platform": "C", "pass": "DESCENDING", "relative_orbit": 63, "mode": "IW"}

    answer = latest.last_image("india", "geom", now=NOW, query=query)
    assert answer["platform"] == "Sentinel-1C" and answer["pass"] == "Descending"
    assert answer["id"].startswith("S1C_") and answer["time"].endswith("Z")
    latest.last_image("india", "geom", now=NOW, query=query)
    assert len(calls) == 1


def test_no_image_and_earth_engine_errors_are_said_not_raised():
    empty = latest.last_image("a", "g", now=NOW, query=lambda g, n: {"count": 0})
    assert "No Sentinel-1 image" in empty["error"]

    def broken(g, n):
        raise RuntimeError("quota")

    assert "could not be asked" in latest.last_image("b", "g", now=NOW, query=broken)["error"]


# ------------------------------------------------------------ the summary

_real_plan = plans.plan


def real_plan(now=None):
    return _real_plan(now=now, fetcher=fake_fetcher())


def test_the_summary_shows_only_satellites_esa_is_planning(monkeypatch):
    monkeypatch.setattr(celestrak, "elements", lambda now=None: {
        "satellites": celestrak.parse(RECORDS), "fetched_at": "2026-10-03T11:00:00Z", "stale": False,
        "error": None, "source": celestrak.URL})
    monkeypatch.setattr(plans, "plan", real_plan)
    monkeypatch.setattr(service, "_last_image", lambda key, record: {"time": "2026-10-03T00:39:55Z", "platform": "Sentinel-1C"})
    result = service.summary(now=NOW)
    assert [s["name"] for s in result["satellites"]] == ["Sentinel-1C", "Sentinel-1D"]
    india = result["india"]
    assert india["area"]["name"] == "India" and india["area"]["approximate"] is True, "EE not reachable in tests"
    assert india["next_planned"][0]["in_progress"] and len(india["next_planned"]) == 3
    assert india["last_image"]["platform"] == "Sentinel-1C"
    assert [f["satellite"] for f in result["sources"]["plans"]["files"]] == ["S1C", "S1D"]
    assert "can still change" in result["note"]


def test_without_a_readable_plan_every_sentinel1_is_shown_rather_than_none(monkeypatch):
    monkeypatch.setattr(celestrak, "elements", lambda now=None: {
        "satellites": celestrak.parse(RECORDS), "fetched_at": None, "stale": False, "error": None, "source": ""})
    monkeypatch.setattr(plans, "plan", lambda now=None: {"files": [], "datatakes": [], "fetched_at": None,
                                                           "stale": True, "error": "down", "source": plans.PAGE})
    monkeypatch.setattr(service, "_last_image", lambda key, record: {"error": "no EE"})
    result = service.summary(now=NOW)
    assert len(result["satellites"]) == 3 and result["india"]["next_planned"] == []


# ------------------------------------------------------------ the API

@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import main
    from core import auth
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789")
    auth._failures.clear()
    auth.create_user("viewer1", "Correct-Horse-Battery-7", "viewer")
    return TestClient(main.app, raise_server_exceptions=False)


def test_the_india_summary_is_public_and_an_area_needs_a_session(client, monkeypatch):
    from geo import regions
    monkeypatch.setattr(service, "summary", lambda: {"satellites": [], "india": {}, "note": "x"})
    assert client.get("/satellites").status_code == 200
    assert client.get("/satellites/area?region=morigaon").status_code == 401

    token = client.post("/auth/login", json={"username": "viewer1", "password": "Correct-Horse-Battery-7"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(service, "area", lambda name: None)
    missing = client.get("/satellites/area?region=atlantis", headers=headers)
    assert missing.status_code == 404 and missing.json()["detail"]["error"] == "region_not_found"

    def ambiguous(name):
        raise regions.RegionAmbiguous(name, ["Aurangabad (Bihar)", "Aurangabad (Maharashtra)"])

    monkeypatch.setattr(service, "area", ambiguous)
    assert client.get("/satellites/area?region=aurangabad", headers=headers).status_code == 409
    monkeypatch.setattr(service, "area", lambda name: {"area": {"name": "Morigaon"}, "next_planned": []})
    assert client.get("/satellites/area?region=morigaon", headers=headers).json()["area"]["name"] == "Morigaon"
    assert client.get("/satellites/area?region=%20", headers=headers).status_code == 422
