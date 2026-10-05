"""
The document store behaves the same on both backends - SQLite (laptop,
tests, offline) and MongoDB (Atlas in production, mongomock here) - and the
old SQLite tables are carried over without losing an account.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import auth, cache, jobs, store  # noqa: E402


@pytest.fixture(params=["sqlite", "mongodb"])
def backend(request, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789")
    auth._failures.clear()
    if request.param == "mongodb":
        mongomock = pytest.importorskip("mongomock")
        monkeypatch.setenv("MONGODB_URI", "mongodb://mock.example/antardrishti")
        store.use_client(mongomock.MongoClient())
    else:
        monkeypatch.delenv("MONGODB_URI", raising=False)
    yield request.param
    store._mongo.update(client=None, uri=None)


def test_the_same_calls_work_on_both_backends(backend):
    col = store.collection("things")
    col.insert({"_id": "a", "owner": "asha", "n": 1, "at": "2026-10-01T00:00:00Z"}, blob=b"\x00\x01")
    col.insert({"_id": "b", "owner": "asha", "n": 5, "at": "2026-10-03T00:00:00Z"})
    col.insert({"_id": "c", "owner": "ravi", "n": 3, "at": "2026-10-02T00:00:00Z", "saved": True})
    with pytest.raises(store.DuplicateKey):
        col.insert({"_id": "a"})

    assert col.get("a")["n"] == 1 and "_blob" not in col.get("a")
    assert col.get("a", blob=True)["_blob"] == b"\x00\x01"
    assert [d["_id"] for d in col.find({"owner": "asha"}, sort=[("at", -1)])] == ["b", "a"]
    assert [d["_id"] for d in col.find({"n": {"$gte": 3}}, sort=[("n", 1)])] == ["c", "b"]
    assert [d["_id"] for d in col.find({"owner": {"$in": ["ravi"]}})] == ["c"]
    assert [d["_id"] for d in col.find({"saved": {"$exists": True}})] == ["c"]
    assert col.count({"at": {"$lt": "2026-10-02T12:00:00Z"}}) == 2
    assert len(col.find(limit=2)) == 2

    assert col.update("a", set={"n": 9}, inc={"hits": 2}, push={"steps": "one"})
    col.update("a", push={"steps": "two"}, unset=["at"])
    doc = col.get("a")
    assert doc["n"] == 9 and doc["hits"] == 2 and doc["steps"] == ["one", "two"] and "at" not in doc
    assert not col.update("missing", set={"n": 1})
    assert col.update_many({"owner": "asha"}, set={"flag": True}) == 2
    assert col.set_blob("b", b"png")
    assert col.get("b", blob=True)["_blob"] == b"png"

    assert col.delete("a") and not col.delete("a")
    assert col.delete_many({"owner": "asha"}) == 1
    assert col.count() == 1


def test_results_are_packed_small_and_come_back_the_same(backend):
    result = {"evidence": [{"id": f"E{i}", "value": i * 1.5} for i in range(500)], "text": "x" * 5000}
    packed = store.pack(result)
    assert len(packed) < len(str(result)) / 4
    assert store.unpack(packed) == result


def test_accounts_and_jobs_run_on_either_backend(backend):
    auth.create_user("asha", "Monsoon-River-2026!", "analyst")
    assert auth.authenticate("asha", "Monsoon-River-2026!")["role"] == "analyst"
    job_id = jobs.submit("analyze", {"region": "testland"}, "asha",
                         lambda: {"request_id": "r1", "region": {"name": "Testland"},
                                  "evidence": [{"id": "E1", "quantity": "flood_extent",
                                                "value": 12.5, "unit": "km2"}]},
                         run_inline=True)
    job = jobs.get(job_id)
    assert job["status"] == "done" and job["result"]["request_id"] == "r1"
    rows = jobs.history({"username": "asha", "role": "analyst"})
    assert rows[0]["headline"]["value"] == 12.5 and rows[0]["saved"] is False and rows[0]["expire_at"]


def test_the_analysis_cache_uses_the_database_when_mongodb_is_configured(backend, tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    payload = {"region": "testland", "post_start": "2026-07-01"}
    cache.put(payload, {"request_id": "x", "evidence": [1, 2, 3], "_internal": object()})
    key = cache.key_for(payload)
    assert cache.get_by_id(key)["evidence"] == [1, 2, 3]
    assert cache.request_by_id(key) == payload
    assert cache.get(payload)["_cache"]["hit"]
    on_disk = (tmp_path / "cache" / f"{key}.json").exists()
    assert on_disk is (backend == "sqlite"), "files only without MongoDB"
    assert cache.stats()["entries"] == 1
    assert cache.clear() == 1 and cache.get_by_id(key) is None


def test_uploaded_boundaries_follow_the_backend(backend, tmp_path, monkeypatch):
    from geo import boundary_upload
    monkeypatch.setattr(boundary_upload, "STORE", tmp_path / "boundaries")
    sha = "a" * 64
    boundary_upload.save({"file": {"sha256": sha, "name": "x.kml"}, "features": []})
    assert boundary_upload.load(sha)["file"]["name"] == "x.kml"
    assert (tmp_path / "boundaries" / f"{sha}.json").exists() is (backend == "sqlite")


def test_old_users_and_jobs_tables_are_carried_over(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "old.db"))
    monkeypatch.delenv("MONGODB_URI", raising=False)
    old = sqlite3.connect(tmp_path / "old.db")
    old.execute("""CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
        role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
        last_login TEXT, full_name TEXT, email TEXT, organisation TEXT, user_type TEXT)""")
    old.execute("INSERT INTO users VALUES ('punith', ?, 'admin', 1, '2026-09-01T00:00:00Z', NULL,"
                " NULL, NULL, NULL, NULL)", (auth.hash_password("whatever it was"),))
    old.execute("""CREATE TABLE jobs (id TEXT PRIMARY KEY, owner TEXT, kind TEXT, status TEXT, steps TEXT,
        request TEXT, result TEXT, error TEXT, created_at TEXT, started_at TEXT, finished_at TEXT)""")
    old.execute("INSERT INTO jobs VALUES ('j-old', 'punith', 'analyze', 'done', '[]', '{\"region\": \"kerala\"}',"
                " '{\"request_id\": \"abc\", \"region\": {\"name\": \"Kerala\"}, \"evidence\": []}', NULL,"
                " '2026-09-02T00:00:00Z', NULL, '2026-09-02T00:01:00Z')")
    old.commit()
    old.close()
    assert auth.authenticate("punith", "whatever it was")["role"] == "admin", \
        "an old password still works - the rules apply only when a password is set"
    row = jobs.history({"username": "punith", "role": "admin"})[0]
    assert row["job_id"] == "j-old" and row["place"] == "Kerala"
    assert jobs.get("j-old")["result"]["request_id"] == "abc"
