"""
Saving analyses (recent for a week, saved for good) and report pictures
(drawn from the analysis, stored with it when it is saved).

Earth Engine is replaced by a fake that returns a plain PNG of the size
asked for; everything Antardrishti draws on top of it is real.
"""

import io
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core import auth, jobs, pictures, store  # noqa: E402

GOOD = "Monsoon-River-2026!"
USER = {"username": "asha", "role": "analyst"}
OTHER = {"username": "ravi", "role": "analyst"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789")
    monkeypatch.delenv("MONGODB_URI", raising=False)
    auth._failures.clear()


RESULT = {
    "request_id": "req1",
    "region": {"name": "Morigaon", "bbox": [92.0, 26.0, 92.6, 26.5]},
    "period": {"post": {"start": "2026-07-01", "end": "2026-07-10"},
               "pre": {"start": "2026-02-01", "end": "2026-04-30"}},
    "observation": {"sensor_used": "sentinel-1"},
    "evidence": [{"id": "E1", "quantity": "flood_extent", "value": 84.5, "unit": "km2"}],
    "zones_geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[92.1, 26.1], [92.3, 26.1],
                                                                             [92.3, 26.3], [92.1, 26.3],
                                                                             [92.1, 26.1]]]},
         "properties": {"kind": "outline", "id": "Z1", "rank": 1, "area_km2": 60.2, "severity": "high"}},
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[92.4, 26.35], [92.5, 26.35],
                                                                             [92.5, 26.42], [92.4, 26.42],
                                                                             [92.4, 26.35]]]},
         "properties": {"kind": "outline", "id": "Z2", "rank": 2, "area_km2": 12.1, "severity": "low"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [92.2, 26.2]},
         "properties": {"kind": "marker", "id": "Z1", "rank": 1, "severity": "high"}},
    ]},
}


def finished_job(owner="asha"):
    return jobs.submit("analyze", {"region": "morigaon"}, owner, lambda: dict(RESULT), run_inline=True)


# ------------------------------------------------------------ saving

def test_a_run_is_recent_until_saved_then_kept_for_good():
    job_id = finished_job()
    row = jobs.history(USER)[0]
    assert row["saved"] is False and row["expire_at"], "recent runs expire"
    saved = jobs.save(job_id, USER, title="  Morigaon flood, July 2026 ", note="For the DDMA meeting")
    assert saved["saved"] and saved["expire_at"] is None
    assert saved["title"] == "Morigaon flood, July 2026" and saved["note"] == "For the DDMA meeting"
    assert [r["job_id"] for r in jobs.history(USER, saved_only=True)] == [job_id]
    jobs.edit(job_id, USER, title="Renamed")
    assert jobs.history(USER)[0]["title"] == "Renamed"
    back = jobs.unsave(job_id, USER)
    assert back["saved"] is False and back["expire_at"]


def test_only_the_owner_saves_or_deletes():
    job_id = finished_job()
    assert jobs.save(job_id, OTHER) is None
    assert jobs.delete(job_id, OTHER) is False
    assert jobs.delete(job_id, USER) is True and jobs.get(job_id) is None


def test_expired_recent_runs_are_purged_and_saved_ones_stay():
    keep, drop = finished_job(), finished_job()
    jobs.save(keep, USER)
    store.collection("jobs").update(drop, set={"expire_at": "2000-01-01T00:00:00Z"})
    assert jobs.purge_expired() == 1
    assert jobs.get(keep) and jobs.get(drop) is None


def test_an_unfinished_job_cannot_be_saved():
    job_id = jobs.submit("analyze", {}, "asha", lambda: (_ for _ in ()).throw(RuntimeError("x")),
                         run_inline=True)
    with pytest.raises(ValueError):
        jobs.save(job_id, USER)


# ------------------------------------------------------------ picture geometry

def test_picture_geometry():
    w, h = pictures.dimensions([92.0, 26.0, 92.6, 26.5])
    assert w == 1200 and 1080 < h < 1150, "square ground pixels at 26 N"
    assert pictures.to_pixel(92.0, 26.5, [92.0, 26.0, 92.6, 26.5], (1200, 1000)) == (0.0, 0.0)
    assert pictures.nice_km(13) == 20
    box = pictures.circle_bbox(92.3, 26.2, 10)
    assert abs((box[3] - box[1]) * 110.57 - 20) < 0.1
    padded = pictures.pad_bbox([92.0, 26.0, 92.0001, 26.0001])
    assert padded[2] - padded[0] >= 0.02, "a tiny box is given a minimum size"


def test_every_flood_result_gets_an_overview_zone_close_ups_and_before_after():
    kinds = [k for k, _ in pictures.auto_kinds(RESULT)]
    assert kinds == ["overview", "zone-Z1", "zone-Z2", "before", "after"]
    no_baseline = {**RESULT, "period": {"post": RESULT["period"]["post"]}}
    assert "before" not in dict(pictures.auto_kinds(no_baseline))
    assert pictures.auto_kinds({**RESULT, "observation": {"sensor_used": None}}) == []


def blank_png(size):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", size, (120, 120, 120)).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeEE:
    """Stands in for Earth Engine: records each picture asked for."""

    def __init__(self):
        self.calls = []

    def map_png(self, layers, bbox, size, fetch=None):
        self.calls.append((layers, bbox, size))
        return blank_png(size)


@pytest.fixture
def fake_ee(monkeypatch):
    fake = FakeEE()
    monkeypatch.setattr(pictures, "map_png", fake.map_png)
    return fake


def layers_for(period):
    return {"period": period}


def test_a_picture_is_the_map_with_title_legend_scale_and_credit(fake_ee):
    from PIL import Image
    png, meta = pictures.render("zone-Z1", "req1", RESULT, layers_for)
    image = Image.open(io.BytesIO(png))
    map_w, map_h = fake_ee.calls[0][2]
    assert image.size == (map_w, map_h + 64 + 92), "title band above, legend and credit below"
    assert meta["title"].startswith("Zone 1: 60.2 km²") and meta["km_per_px"] > 0
    pictures.render("before", "req1", RESULT, layers_for)
    assert fake_ee.calls[-1][0] == {"period": "pre"}, "before is drawn from the comparison period"


def test_auto_pictures_are_drawn_once_and_kept_when_the_analysis_is_saved(fake_ee):
    job_id = finished_job()
    first = pictures.ensure_auto("req1", "overview", RESULT, layers_for)
    again = pictures.ensure_auto("req1", "overview", RESULT, layers_for)
    assert len(fake_ee.calls) == 1 and first["_id"] == again["_id"]
    assert first["expire_at"]
    jobs.save(job_id, USER)
    assert "expire_at" not in store.collection("pictures").get(first["_id"])


def test_captures_belong_to_their_owner_and_are_limited(fake_ee):
    job_id = finished_job()
    pic = pictures.capture("req1", RESULT, layers_for, "asha", [92.2, 26.1, 92.3, 26.2],
                           caption="Mayong village, road cut", job_id=job_id)
    assert pic["caption"] == "Mayong village, road cut" and pic["url"].endswith(".png")
    listing = pictures.listing(RESULT, "req1", "asha")
    assert [c["id"] for c in listing["captures"]] == [pic["id"]]
    assert pictures.listing(RESULT, "req1", "ravi")["captures"] == []
    assert pictures.update(pic["id"], "ravi", caption="mine now") is None
    assert pictures.update(pic["id"], "asha", caption="Mayong")["caption"] == "Mayong"
    for _ in range(pictures.MAX_CAPTURES - 1):
        pictures.capture("req1", RESULT, layers_for, "asha", [92.2, 26.1, 92.3, 26.2])
    with pytest.raises(ValueError, match="up to"):
        pictures.capture("req1", RESULT, layers_for, "asha", [92.2, 26.1, 92.3, 26.2])
    jobs.delete(job_id, USER)
    assert store.collection("pictures").get(pic["id"]) is None, "deleting the analysis deletes its captures"


def test_the_report_gets_the_pictures_in_order(fake_ee):
    pictures.capture("req1", RESULT, layers_for, "asha", [92.2, 26.1, 92.3, 26.2], caption="Mine")
    items, missing = pictures.for_report(RESULT, "req1", "asha", layers_for)
    titles = [t for t, _, _ in items]
    assert titles[0] == "Morigaon: flood map" and titles[-1] == "Morigaon: captured view"
    assert items[-1][1] == "Mine" and missing == []
    assert len(items) == 6, "overview, two zones, before, after, one capture"


def test_the_pdf_has_a_pictures_section(fake_ee):
    pytest.importorskip("reportlab")
    from pipeline import export_pdf
    items, _ = pictures.for_report(RESULT, "req1", "asha", layers_for)
    stored = {**RESULT, "pictures": items, "pictures_missing": ["Before"]}
    pdf = export_pdf.render(stored)
    assert pdf[:4] == b"%PDF" and len(pdf) > 20000


# ------------------------------------------------------------ the API

@pytest.fixture
def client(monkeypatch, fake_ee):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import main
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setattr(main, "_stored_flood_or_404", lambda rid: {**RESULT, "request_id": rid})
    monkeypatch.setattr(main, "_picture_layers", lambda rid: ({**RESULT, "request_id": rid}, layers_for))
    for name in ("asha", "ravi"):
        auth.create_user(name, GOOD, "analyst")
    c = TestClient(main.app, raise_server_exceptions=False)
    tokens = {n: c.post("/auth/login", json={"username": n, "password": GOOD}).json()["token"]
              for n in ("asha", "ravi")}
    c.cookies.clear()
    return c, {n: {"Authorization": f"Bearer {t}"} for n, t in tokens.items()}


def test_save_rename_and_delete_through_the_api(client):
    c, h = client
    job_id = finished_job("asha")
    saved = c.post(f"/history/{job_id}/save", headers=h["asha"], json={"title": "July flood"})
    assert saved.status_code == 200 and saved.json()["saved"]
    assert c.get("/history?saved=true", headers=h["asha"]).json()["items"][0]["title"] == "July flood"
    assert c.post(f"/history/{job_id}/save", headers=h["ravi"], json={}).status_code == 404
    assert c.delete(f"/history/{job_id}", headers=h["ravi"]).status_code == 404
    assert c.delete(f"/history/{job_id}", headers=h["asha"]).status_code == 200, \
        "a user may delete their own analysis without being admin"


def test_pictures_through_the_api(client):
    c, h = client
    listing = c.get("/analyze/req1/pictures", headers=h["asha"]).json()
    assert [p["kind"] for p in listing["auto"]][:2] == ["overview", "zone-Z1"]
    assert listing["auto"][0]["ready"] is False
    png = c.get(listing["auto"][0]["url"], headers=h["asha"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert c.get("/analyze/req1/pictures", headers=h["asha"]).json()["auto"][0]["ready"]

    made = c.post("/analyze/req1/pictures", headers=h["asha"],
                  json={"centre": [92.25, 26.2], "radius_km": 5, "caption": "Mayong"})
    assert made.status_code == 200, made.text
    pic = made.json()
    assert c.get(pic["url"], headers=h["ravi"]).status_code == 404, "captures are private"
    assert c.patch(f"/pictures/{pic['id']}", headers=h["asha"], json={"caption": "Mayong village"}).json()["caption"] \
        == "Mayong village"
    assert c.post("/analyze/req1/pictures", headers=h["asha"], json={"radius_km": 5}).status_code == 422
    assert c.delete(f"/pictures/{pic['id']}", headers=h["ravi"]).status_code == 404
    assert c.delete(f"/pictures/{pic['id']}", headers=h["asha"]).status_code == 200
