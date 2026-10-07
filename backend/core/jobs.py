"""
Background jobs: an analysis takes a minute or two, and a web request should
not.

POST /jobs/... answers at once with a job id. The work runs on a small thread
pool (JOB_WORKERS, default 2 - Earth Engine does the heavy lifting remotely,
so threads mostly wait on the network). The website polls GET /jobs/{id} and
shows the current step ("Measuring flood water", "Counting people"...) until
the result is ready, then opens it.

A finished job is also the user's History entry. It keeps its full result
(compressed, as the store's blob) and a short summary for the History list,
so the list never has to open the results. Unsaved jobs are "recent" and are
deleted after settings.recent_days(); saving one removes its expiry and lets
it carry a title and a note.

A job that was running when the server stopped is marked failed on the next
start - not left "running" for ever. Storage is core/store.py.
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from core import auth, progress, settings, store

log = logging.getLogger("antardrishti")
_lock = threading.Lock()
_executor = None
_migrated = set()

TITLE_MAX = 120
NOTE_MAX = 1000


def _now():
    return store.now_iso()


def _expiry():
    return store.iso_in(days=settings.recent_days())


def _migrate_legacy_jobs():
    """Copy jobs from the old `jobs` table into the store, once per file."""
    if store.mongo_uri() or auth.db_path() in _migrated:
        return
    _migrated.add(auth.db_path())
    try:
        with auth.connect() as db:
            if not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
                return
            rows = [dict(r) for r in db.execute("SELECT * FROM jobs").fetchall()]
    except sqlite3.Error:
        return
    col = store.collection("jobs")
    from core import history
    for row in rows:
        if col.get(row["id"]):
            continue
        result = json.loads(row["result"]) if row.get("result") else None
        request = json.loads(row["request"] or "{}")
        doc = {"_id": row["id"], "owner": row["owner"], "kind": row["kind"], "status": row["status"],
               "steps": json.loads(row["steps"] or "[]"),
               "error": json.loads(row["error"]) if row.get("error") else None,
               "request": request, "created_at": row["created_at"], "started_at": row["started_at"],
               "finished_at": row["finished_at"], "saved": False, "title": None, "note": None,
               "expire_at": _expiry()}
        if result is not None:
            doc["summary"] = history.summarise({**doc, "id": row["id"], "result": result}, request)
        try:
            col.insert(doc, blob=store.pack(result) if result is not None else None)
        except store.DuplicateKey:
            pass


def _col():
    _migrate_legacy_jobs()
    return store.collection("jobs")


def executor():
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=settings.job_workers(),
                                           thread_name_prefix="job")
        return _executor


def _row(doc, include_result=True):
    if doc is None:
        return None
    job = {
        "id": doc["_id"], "owner": doc.get("owner"), "kind": doc.get("kind"), "status": doc.get("status"),
        "steps": doc.get("steps") or [], "error": doc.get("error"),
        "created_at": doc.get("created_at"), "started_at": doc.get("started_at"),
        "finished_at": doc.get("finished_at"), "saved": bool(doc.get("saved")),
        "title": doc.get("title"), "note": doc.get("note"), "expire_at": doc.get("expire_at"),
        # What was asked: the web app refills its form from this when a past
        # result is opened, instead of leaving the defaults in place.
        "request": doc.get("request") or {},
    }
    if include_result and doc.get("_blob") is not None:
        job["result"] = store.unpack(doc["_blob"])
    return job


def _add_step(job_id, text):
    col = _col()
    doc = col.get(job_id)
    steps = (doc or {}).get("steps") or []
    if not steps or steps[-1]["text"] != text:
        col.update(job_id, push={"steps": {"text": text, "at": _now()}})


def submit(kind, request, owner, work, run_inline=False):
    """Queue `work()` (which returns a JSON-able result). Returns the job id.

    `work` may raise fastapi.HTTPException for a refusal the user should read
    (no imagery, region not found); its status and detail are kept. Anything
    else becomes an internal error with an incident id in the log.
    """
    job_id = uuid.uuid4().hex[:16]
    request = json.loads(json.dumps(request, default=str))
    _col().insert({"_id": job_id, "owner": owner, "kind": kind, "status": "queued", "steps": [],
                   "request": request, "error": None, "created_at": _now(), "started_at": None,
                   "finished_at": None, "saved": False, "title": None, "note": None,
                   "expire_at": _expiry()})

    def run():
        token = progress.bind(lambda text: _add_step(job_id, text))
        col = _col()
        col.update(job_id, set={"status": "running", "started_at": _now()})
        started = time.monotonic()
        try:
            result = json.loads(json.dumps(work(), default=str))
            from core import history
            summary = history.summarise({"id": job_id, "kind": kind, "status": "done", "result": result,
                                         "created_at": _now(), "finished_at": _now()}, request)
            col.set_blob(job_id, store.pack(result))
            col.update(job_id, set={"status": "done", "finished_at": _now(), "summary": summary})
            log.info("job done", extra={"fields": {"job": job_id, "kind": kind, "owner": owner,
                                                   "seconds": round(time.monotonic() - started, 1)}})
        except Exception as exc:                 # noqa: BLE001 - every failure is recorded
            col.update(job_id, set={"status": "failed", "error": _describe(exc, job_id),
                                    "finished_at": _now()})
        finally:
            progress.unbind(token)

    if run_inline:
        run()
    else:
        context = copy_context()
        executor().submit(context.run, run)
    return job_id


def _describe(exc, job_id):
    status = getattr(exc, "status_code", None)
    if status is not None:
        return {"status": status, "detail": getattr(exc, "detail", str(exc))}
    from core import earth_engine
    if isinstance(exc, earth_engine.EarthEngineUnavailable):
        return {"status": 503, "detail": {"error": "earth_engine_unavailable", "message": str(exc)}}
    incident = uuid.uuid4().hex[:12]
    log.error("job failed", exc_info=exc, extra={"fields": {"job": job_id, "incident": incident}})
    return {"status": 500, "detail": {"error": "internal_error", "incident": incident,
                                      "message": "The analysis failed unexpectedly. Quote incident "
                                                 f"{incident} when reporting it."}}


def _visible(doc, user):
    """Its owner, or an admin (or anyone, with sign-in off: user None)."""
    return doc is not None and (user is None or user["role"] == "admin"
                                or doc.get("owner") == user["username"])


def get(job_id, user=None, include_result=True):
    """The job, if `user` may see it, else None."""
    doc = _col().get(job_id, blob=include_result)
    return _row(doc, include_result) if _visible(doc, user) else None


def recent(user, limit=20):
    flt = None if user is None or user["role"] == "admin" else {"owner": user["username"]}
    return [_row(d, include_result=False)
            for d in _col().find(flt, sort=[("created_at", -1)], limit=limit)]


def _history_row(doc):
    row = dict(doc.get("summary") or {})
    row.update({"job_id": doc["_id"], "kind": doc.get("kind"), "created_at": doc.get("created_at"),
                "finished_at": doc.get("finished_at"), "saved": bool(doc.get("saved")),
                "title": doc.get("title"), "note": doc.get("note"), "expire_at": doc.get("expire_at")})
    return row


def history(user, limit=50, saved_only=False):
    """The user's own finished jobs, newest first, as short rows.

    Always the caller's own - an admin's History page is their work too, not
    everyone's. With sign-in off (user None) every job is shown.
    """
    flt = {"status": "done"}
    if user is not None:
        flt["owner"] = user["username"]
    if saved_only:
        flt["saved"] = True
    docs = _col().find(flt, sort=[("created_at", -1)], limit=limit)
    return [_history_row(d) for d in docs if d.get("summary")]


def _own(job_id, user):
    doc = _col().get(job_id)
    if doc is None or not _visible(doc, user):
        return None
    return doc


def _clean(text, limit):
    text = (text or "").strip()
    return text[:limit] or None


def save(job_id, user, title=None, note=None):
    """Keep a finished job for good, with an optional title and note."""
    doc = _own(job_id, user)
    if doc is None:
        return None
    if doc.get("status") != "done":
        raise ValueError("Only a finished analysis can be saved.")
    changes = {"saved": True, "saved_at": _now()}
    if title is not None:
        changes["title"] = _clean(title, TITLE_MAX)
    if note is not None:
        changes["note"] = _clean(note, NOTE_MAX)
    _col().update(job_id, set=changes, unset=["expire_at"])
    from core import pictures
    pictures.keep_for(job_id, (doc.get("summary") or {}).get("request_id"))
    return _history_row(_col().get(job_id))


def unsave(job_id, user):
    """Back to recent: it expires settings.recent_days() from now."""
    if _own(job_id, user) is None:
        return None
    _col().update(job_id, set={"saved": False, "expire_at": _expiry()})
    from core import pictures
    pictures.expire_for(job_id)
    return _history_row(_col().get(job_id))


def edit(job_id, user, title=None, note=None):
    if _own(job_id, user) is None:
        return None
    changes = {}
    if title is not None:
        changes["title"] = _clean(title, TITLE_MAX)
    if note is not None:
        changes["note"] = _clean(note, NOTE_MAX)
    if changes:
        _col().update(job_id, set=changes)
    return _history_row(_col().get(job_id))


def delete(job_id, user):
    if _own(job_id, user) is None:
        return False
    from core import pictures
    pictures.delete_for_job(job_id)
    return _col().delete(job_id)


def purge_expired():
    """Recent (unsaved) jobs past their expiry, and their pictures."""
    from core import pictures
    expired = _col().find({"expire_at": {"$lt": _now()}})
    for doc in expired:
        pictures.delete_for_job(doc["_id"])
        _col().delete(doc["_id"])
    return len(expired)


def fail_interrupted():
    """At startup: jobs left queued or running by a stopped server."""
    try:
        for status in ("queued", "running"):
            _col().update_many({"status": status}, set={
                "status": "failed", "finished_at": _now(),
                "error": {"status": 503, "detail": {
                    "error": "interrupted",
                    "message": "The server restarted while this ran. Run it again."}}})
    except Exception:                            # noqa: BLE001 - startup must not fail on this
        pass
