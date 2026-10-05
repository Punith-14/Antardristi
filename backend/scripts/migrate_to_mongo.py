"""
Copy everything kept on this machine into MongoDB Atlas, once.

    cd backend
    venv\\Scripts\\python -m scripts.migrate_to_mongo            (dry run: counts only)
    venv\\Scripts\\python -m scripts.migrate_to_mongo --write    (copy)

Reads MONGODB_URI from backend/.env. Copies:

    users, jobs (History), pictures, login records   from data/app.db
    analysis results                                  from data/cache/*.json
    uploaded boundaries                               from data/boundaries/*.json

Nothing local is deleted, and a document already in MongoDB is left alone,
so running it twice is safe.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core import auth, jobs, paths, store  # noqa: E402

COLLECTIONS = ("users", "jobs", "pictures", "login_events", "tokens")


def sqlite_docs(name):
    """Documents of one collection from the SQLite store (after migrating the
    pre-store tables into it, which auth/jobs do on first use)."""
    uri = os.environ.pop("MONGODB_URI", None)
    try:
        auth.list_users()            # carries old `users` rows into the store
        jobs.recent(None, limit=1)   # carries old `jobs` rows into the store
        with sqlite3.connect(store.sqlite_path()) as db:
            if not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                              (f"doc_{name}",)).fetchone():
                return []
            return [(json.loads(doc), blob) for doc, blob in db.execute(f"SELECT doc, blob FROM doc_{name}")]
    finally:
        if uri:
            os.environ["MONGODB_URI"] = uri


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="copy (without it, only count)")
    args = parser.parse_args()
    if not os.environ.get("MONGODB_URI"):
        sys.exit("MONGODB_URI is not set in backend/.env.")

    plan = {name: sqlite_docs(name) for name in COLLECTIONS}
    cache_files = sorted(paths.CACHE.glob("*.json"))
    boundary_files = sorted((paths.DATA / "boundaries").glob("*.json"))
    for name, docs in plan.items():
        print(f"{name:14} {len(docs):5} from data/app.db")
    print(f"{'analyses':14} {len(cache_files):5} from data/cache")
    print(f"{'boundaries':14} {len(boundary_files):5} from data/boundaries")
    if not args.write:
        print("\nDry run. Add --write to copy.")
        return

    ok, detail = store.ping()
    if not ok:
        sys.exit(f"MongoDB is not reachable: {detail}")
    copied = skipped = 0
    for name, docs in plan.items():
        col = store.collection(name)
        for doc, blob in docs:
            try:
                col.insert(doc, blob=blob)
                copied += 1
            except store.DuplicateKey:
                skipped += 1
    analyses = store.collection("analyses")
    for path in cache_files:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            analyses.insert({"_id": path.stem, "request": entry.get("request"),
                             "stored_at": store.now_iso(), "stored_at_ts": entry.get("stored_at", time.time())},
                            blob=store.pack(entry.get("response") or {}))
            copied += 1
        except store.DuplicateKey:
            skipped += 1
        except (ValueError, OSError):
            print(f"  skipped unreadable {path.name}")
    boundaries = store.collection("boundaries")
    for path in boundary_files:
        try:
            boundaries.insert({"_id": path.stem, "created_at": store.now_iso(),
                               "expire_at": store.iso_in(days=7)},
                              blob=store.pack(json.loads(path.read_text(encoding="utf-8"))))
            copied += 1
        except store.DuplicateKey:
            skipped += 1
    print(f"\nCopied {copied}, already there {skipped}. Local files were not touched.")


if __name__ == "__main__":
    main()
