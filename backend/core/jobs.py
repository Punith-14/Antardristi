"""
Background jobs: an analysis takes a minute or two, and a web request should
not.

POST /jobs/... answers at once with a job id. The work runs on a small thread
pool (JOB_WORKERS, default 2 - Earth Engine does the heavy lifting remotely,
so threads mostly wait on the network). The website polls GET /jobs/{id} and
shows the current step ("Detecting water", "Counting people"...) until the
result is ready, then opens it.

Jobs are kept in the same SQLite file as the users, so a list of recent jobs
survives a restart. A job that was running when the server stopped is marked
failed on the next start - not left "running" for ever.
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from datetime import datetime, timezone

from core import auth, progress, settings

log = logging.getLogger("antardrishti")
_lock = threading.Lock()
_executor = None


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _db():
    connection = auth.connect()
    connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, owner TEXT, kind TEXT, status TEXT, steps TEXT,
        request TEXT, result TEXT, error TEXT,
        created_at TEXT, started_at TEXT, finished_at TEXT)""")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def executor():
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=settings.job_workers(),
                                           thread_name_prefix="job")
        return _executor


def _row(row, include_result=True):
    if row is None:
        return None
    job = {
        "id": row["id"], "owner": row["owner"], "kind": row["kind"], "status": row["status"],
        "steps": json.loads(row["steps"] or "[]"),
        "error": json.loads(row["error"]) if row["error"] else None,
        "created_at": row["created_at"], "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }
    if include_result and row["result"]:
        job["result"] = json.loads(row["result"])
    return job


def _update(job_id, **fields):
    columns = ", ".join(f"{k} = ?" for k in fields)
    with _lock, _db() as db:
        db.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*fields.values(), job_id))


def _add_step(job_id, text):
    with _lock, _db() as db:
        row = db.execute("SELECT steps FROM jobs WHERE id = ?", (job_id,)).fetchone()
        steps = json.loads(row["steps"] or "[]") if row else []
        if not steps or steps[-1]["text"] != text:
            steps.append({"text": text, "at": _now()})
        db.execute("UPDATE jobs SET steps = ? WHERE id = ?", (json.dumps(steps), job_id))


def submit(kind, request, owner, work, run_inline=False):
    """Queue `work()` (which returns a JSON-able result). Returns the job id.

    `work` may raise fastapi.HTTPException for a refusal the user should read
    (no imagery, region not found); its status and detail are kept. Anything
    else becomes an internal error with an incident id in the log.
    """
    job_id = uuid.uuid4().hex[:16]
    with _lock, _db() as db:
        db.execute("INSERT INTO jobs (id, owner, kind, status, steps, request, created_at) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (job_id, owner, kind, "queued", "[]", json.dumps(request, default=str), _now()))

    def run():
        token = progress.bind(lambda text: _add_step(job_id, text))
        _update(job_id, status="running", started_at=_now())
        started = time.monotonic()
        try:
            result = work()
            _update(job_id, status="done", result=json.dumps(result, default=str),
                    finished_at=_now())
            log.info("job done", extra={"fields": {"job": job_id, "kind": kind, "owner": owner,
                                                   "seconds": round(time.monotonic() - started, 1)}})
        except Exception as exc:                 # noqa: BLE001 - every failure is recorded
            error = _describe(exc, job_id)
            _update(job_id, status="failed", error=json.dumps(error), finished_at=_now())
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


def get(job_id, user=None, include_result=True):
    """The job, if `user` may see it (its owner, or an admin), else None."""
    with _db() as db:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    job = _row(row, include_result)
    if job and user is not None and user["role"] != "admin" and job["owner"] != user["username"]:
        return None
    return job


def recent(user, limit=20):
    with _db() as db:
        if user is None or user["role"] == "admin":
            rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        else:
            rows = db.execute("SELECT * FROM jobs WHERE owner = ? ORDER BY created_at DESC LIMIT ?",
                              (user["username"], limit))
        return [_row(r, include_result=False) for r in rows]


def fail_interrupted():
    """At startup: jobs left queued or running by a stopped server."""
    try:
        with _lock, _db() as db:
            db.execute("UPDATE jobs SET status = 'failed', finished_at = ?, error = ? "
                       "WHERE status IN ('queued', 'running')",
                       (_now(), json.dumps({"status": 503, "detail": {
                           "error": "interrupted",
                           "message": "The server restarted while this ran. Run it again."}})))
    except sqlite3.Error:
        pass
