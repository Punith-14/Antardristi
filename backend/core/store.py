"""
Where the app keeps what it must remember: one small document-store API with
two backends.

    MongoDB    when MONGODB_URI is set (MongoDB Atlas in production)
    SQLite     otherwise - the file at DATABASE_PATH (data/app.db), so the app
               still runs on a laptop with no internet and the tests need no
               server

Every module talks to collections through the same few calls, so none of
them knows which backend is underneath:

    col = store.collection("users")
    col.insert({"_id": "asha@x.org", "role": "analyst", ...})
    col.get("asha@x.org")
    col.find({"owner": "asha", "saved": True}, sort=[("created_at", -1)], limit=20)
    col.update("asha@x.org", set={...}, unset=[...], inc={...}, push={...})
    col.delete("asha@x.org"); col.delete_many({...}); col.count({...})

Filters are plain equality or {"$lt", "$lte", "$gt", "$gte", "$ne", "$in",
"$exists"} on top-level fields - the subset both backends do the same way.
Times are stored as ISO-8601 UTC strings ("2026-10-04T10:15:00Z"), which
sort and compare correctly as text in both.

Large payloads (an analysis result, a PNG picture) go in a separate binary
"blob", compressed where it helps, and are only read when asked for - a list
of History rows never drags 250 KB results along with it.
"""

import json
import os
import sqlite3
import threading
import zlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from core import paths

_lock = threading.Lock()
_mongo = {"client": None, "uri": None}


# ------------------------------------------------------------ helpers

def now_iso(moment=None):
    return (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_in(**delta):
    return now_iso(datetime.now(timezone.utc) + timedelta(**delta))


def pack(obj):
    """JSON -> compressed bytes (an analysis result shrinks about 8x)."""
    return zlib.compress(json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8"), 6)


def unpack(data):
    if data is None:
        return None
    return json.loads(zlib.decompress(bytes(data)).decode("utf-8"))


def mongo_uri():
    return os.environ.get("MONGODB_URI", "").strip() or None


def mongo_db_name():
    return os.environ.get("MONGODB_DB", "antardrishti").strip() or "antardrishti"


def backend_name():
    return "mongodb" if mongo_uri() else "sqlite"


def sqlite_path():
    return os.environ.get("DATABASE_PATH") or str(paths.DATA / "app.db")


# ------------------------------------------------------------ filters

def _matches(doc, flt):
    for key, cond in (flt or {}).items():
        value = doc.get(key)
        if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
            for op, arg in cond.items():
                if op == "$exists":
                    if (key in doc and doc[key] is not None) != bool(arg):
                        return False
                elif op == "$ne":
                    if value == arg:
                        return False
                elif op == "$in":
                    if value not in arg:
                        return False
                elif op in ("$lt", "$lte", "$gt", "$gte"):
                    if value is None:
                        return False
                    if op == "$lt" and not value < arg:
                        return False
                    if op == "$lte" and not value <= arg:
                        return False
                    if op == "$gt" and not value > arg:
                        return False
                    if op == "$gte" and not value >= arg:
                        return False
                else:
                    raise ValueError(f"unsupported filter operator {op}")
        elif value != cond:
            return False
    return True


def _sort(docs, sort):
    for key, direction in reversed(sort or []):
        docs.sort(key=lambda d: (d.get(key) is None, d.get(key) if d.get(key) is not None else ""),
                  reverse=direction < 0)
    return docs


def _apply(doc, set=None, unset=None, inc=None, push=None):
    for k, v in (set or {}).items():
        doc[k] = v
    for k in unset or []:
        doc.pop(k, None)
    for k, v in (inc or {}).items():
        doc[k] = (doc.get(k) or 0) + v
    for k, v in (push or {}).items():
        doc.setdefault(k, [])
        doc[k] = list(doc[k]) + [v]
    return doc


# ------------------------------------------------------------ SQLite

class SqliteCollection:
    """One table per collection: id, the document as JSON, an optional blob."""

    def __init__(self, name):
        self.name = name
        self.table = f"doc_{name}"

    @contextmanager
    def _db(self):
        from pathlib import Path
        Path(sqlite_path()).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(sqlite_path(), check_same_thread=False, timeout=30)
        try:
            connection.execute(f"CREATE TABLE IF NOT EXISTS {self.table} "
                               "(id TEXT PRIMARY KEY, doc TEXT NOT NULL, blob BLOB)")
            with connection:
                yield connection
        finally:
            connection.close()

    def insert(self, doc, blob=None):
        with _lock, self._db() as db:
            try:
                db.execute(f"INSERT INTO {self.table} (id, doc, blob) VALUES (?,?,?)",
                           (doc["_id"], json.dumps(doc, default=str), blob))
            except sqlite3.IntegrityError as exc:
                raise DuplicateKey(doc["_id"]) from exc
        return doc

    def _load(self, row, blob):
        doc = json.loads(row[0])
        if blob:
            doc["_blob"] = row[1]
        return doc

    def get(self, _id, blob=False):
        with self._db() as db:
            row = db.execute(f"SELECT doc, {'blob' if blob else 'NULL'} FROM {self.table} WHERE id = ?",
                             (_id,)).fetchone()
        return self._load(row, blob) if row else None

    def find(self, flt=None, sort=None, limit=None, blob=False):
        with self._db() as db:
            rows = db.execute(f"SELECT doc, {'blob' if blob else 'NULL'} FROM {self.table}").fetchall()
        docs = [d for d in (self._load(r, blob) for r in rows) if _matches(d, flt)]
        docs = _sort(docs, sort)
        return docs[:limit] if limit else docs

    def find_one(self, flt, blob=False):
        found = self.find(flt, limit=1, blob=blob)
        return found[0] if found else None

    def update(self, _id, set=None, unset=None, inc=None, push=None):
        with _lock, self._db() as db:
            row = db.execute(f"SELECT doc FROM {self.table} WHERE id = ?", (_id,)).fetchone()
            if not row:
                return False
            doc = _apply(json.loads(row[0]), set, unset, inc, push)
            db.execute(f"UPDATE {self.table} SET doc = ? WHERE id = ?", (json.dumps(doc, default=str), _id))
        return True

    def update_many(self, flt, set=None, unset=None):
        ids = [d["_id"] for d in self.find(flt)]
        for _id in ids:
            self.update(_id, set=set, unset=unset)
        return len(ids)

    def set_blob(self, _id, blob):
        with _lock, self._db() as db:
            return db.execute(f"UPDATE {self.table} SET blob = ? WHERE id = ?", (blob, _id)).rowcount > 0

    def delete(self, _id):
        with _lock, self._db() as db:
            return db.execute(f"DELETE FROM {self.table} WHERE id = ?", (_id,)).rowcount > 0

    def delete_many(self, flt):
        ids = [d["_id"] for d in self.find(flt)]
        with _lock, self._db() as db:
            for _id in ids:
                db.execute(f"DELETE FROM {self.table} WHERE id = ?", (_id,))
        return len(ids)

    def count(self, flt=None):
        return len(self.find(flt))


# ------------------------------------------------------------ MongoDB

class MongoCollection:
    """The same calls on a pymongo collection; the blob lives in field _blob."""

    def __init__(self, name, client_factory):
        self.name = name
        self._factory = client_factory

    @property
    def col(self):
        return self._factory()[self.name]

    def insert(self, doc, blob=None):
        from pymongo.errors import DuplicateKeyError
        record = dict(doc)
        if blob is not None:
            record["_blob"] = blob
        try:
            self.col.insert_one(record)
        except DuplicateKeyError as exc:
            raise DuplicateKey(doc["_id"]) from exc
        return doc

    @staticmethod
    def _clean(doc, blob):
        if doc is None:
            return None
        if blob and doc.get("_blob") is not None:
            doc["_blob"] = bytes(doc["_blob"])
        return doc

    def get(self, _id, blob=False):
        projection = None if blob else {"_blob": 0}
        return self._clean(self.col.find_one({"_id": _id}, projection), blob)

    def find(self, flt=None, sort=None, limit=None, blob=False):
        cursor = self.col.find(flt or {}, None if blob else {"_blob": 0})
        if sort:
            cursor = cursor.sort(list(sort))
        if limit:
            cursor = cursor.limit(limit)
        return [self._clean(d, blob) for d in cursor]

    def find_one(self, flt, blob=False):
        found = self.find(flt, limit=1, blob=blob)
        return found[0] if found else None

    def _ops(self, set=None, unset=None, inc=None, push=None):
        ops = {}
        if set:
            ops["$set"] = set
        if unset:
            ops["$unset"] = {k: "" for k in unset}
        if inc:
            ops["$inc"] = inc
        if push:
            ops["$push"] = push
        return ops

    def update(self, _id, set=None, unset=None, inc=None, push=None):
        ops = self._ops(set, unset, inc, push)
        if not ops:
            return self.col.count_documents({"_id": _id}) > 0
        return self.col.update_one({"_id": _id}, ops).matched_count > 0

    def update_many(self, flt, set=None, unset=None):
        ops = self._ops(set, unset)
        return self.col.update_many(flt or {}, ops).matched_count if ops else 0

    def set_blob(self, _id, blob):
        return self.col.update_one({"_id": _id}, {"$set": {"_blob": blob}}).matched_count > 0

    def delete(self, _id):
        return self.col.delete_one({"_id": _id}).deleted_count > 0

    def delete_many(self, flt):
        return self.col.delete_many(flt or {}).deleted_count

    def count(self, flt=None):
        return self.col.count_documents(flt or {})


class DuplicateKey(Exception):
    """A document with that _id already exists."""


# Indexes that matter once there are thousands of documents.
MONGO_INDEXES = {
    "users": [("email", 1)],
    "jobs": [("owner", 1), ("created_at", -1), ("expire_at", 1)],
    "pictures": [("request_id", 1), ("job_id", 1), ("expire_at", 1)],
    "tokens": [("expire_at", 1)],
    "login_events": [("username", 1), ("at", -1)],
    "analyses": [("stored_at", -1)],
}


def _mongo_db():
    uri = mongo_uri()
    with _lock:
        if _mongo["client"] is None or _mongo["uri"] != uri:
            from pymongo import MongoClient
            client = MongoClient(uri, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000,
                                 appname="antardrishti")
            db = client[mongo_db_name()]
            for name, fields in MONGO_INDEXES.items():
                for field, direction in fields:
                    try:
                        db[name].create_index([(field, direction)])
                    except Exception:            # noqa: BLE001 - an index is an optimisation
                        pass
            _mongo.update(client=client, uri=uri, db=db)
        return _mongo["db"]


def use_client(client):
    """Tests: run the MongoDB backend on a given client (mongomock)."""
    with _lock:
        _mongo.update(client=client, uri=mongo_uri(), db=client[mongo_db_name()])


def collection(name):
    if mongo_uri():
        return MongoCollection(name, _mongo_db)
    return SqliteCollection(name)


def ping():
    """(ok, detail) - for /ready."""
    try:
        if mongo_uri():
            _mongo_db().client.admin.command("ping")
            return True, "mongodb reachable"
        collection("health").count()
        return True, "sqlite file writable"
    except Exception as exc:                     # noqa: BLE001
        return False, f"{backend_name()}: {type(exc).__name__}"
