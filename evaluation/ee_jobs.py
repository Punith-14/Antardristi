"""
Heavy Earth Engine work as batch exports, not getInfo() calls.

A year of Sentinel-2 percentiles and a harmonic fit over a whole district is
far more than an interactive request can finish: notebook 10's first version
asked for it with getInfo() and sat for half an hour with nothing to show.
Batch exports have no such limit, run on Google's side in parallel, and
survive the notebook being closed.

So each district (notebook 10) or year (notebook 11) becomes one export. Two
destinations, chosen automatically:

* asset - a table in your Earth Engine project, read straight back by the
  notebook. Needs write access to the project's asset folder.
* drive - a CSV in a Google Drive folder, used when assets cannot be written
  (some Cloud projects are registered for compute but give no asset storage,
  or the signed-in account is not allowed to write there). You download the
  CSVs into evaluation/ee_exports/ and re-run the reading cell.

Re-running never repeats work: finished results are read, running exports are
waited for, finished-but-not-downloaded Drive exports are not resubmitted,
and only missing ones are submitted.

The `ee` module is passed in rather than imported, so the logic is testable.
"""

import csv
import re
import time
from pathlib import Path

FOLDER = "antardrishti"
DRIVE_FOLDER = "antardrishti_exports"
DONE = {"SUCCEEDED", "COMPLETED"}
FAILED = {"FAILED", "CANCELLED", "CANCELLING"}
DROP = {"system:index", ".geo"}


def slug(text):
    return re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")


def folder(project):
    return f"projects/{project}/assets/{FOLDER}"


def asset_id(project, name):
    return f"{folder(project)}/{slug(name)}"


def ensure_folder(ee, project):
    path = folder(project)
    try:
        ee.data.getAsset(path)
    except Exception:
        ee.data.createFolder(path)
    return path


def exists(ee, asset):
    try:
        ee.data.getAsset(asset)
        return True
    except Exception:
        return False


def states(ee):
    """{task description: state} for this project's export tasks, newest first wins."""
    out = {}
    for op in ee.data.listOperations():
        meta = op.get("metadata", {})
        description = meta.get("description")
        if description and description not in out:
            out[description] = meta.get("state", "UNKNOWN")
    return out


def plan(names, have, current, finished_means_download=False):
    """What to do for each export: 'read', 'wait', 'download' or 'submit'.

    have: {name: result available locally or as an asset}; current: {name:
    task state}. In Drive mode a finished export whose CSV is not downloaded
    yet is 'download', never submitted again.
    """
    out = {}
    for name in names:
        state = current.get(name)
        if have.get(name):
            out[name] = "read"
        elif state in DONE and finished_means_download:
            out[name] = "download"
        elif state and state not in DONE and state not in FAILED:
            out[name] = "wait"
        else:
            out[name] = "submit"
    return out


def wait(ee, names, poll=60, log=print, sleep=time.sleep, timeout_hours=12):
    """Block until every named export has finished; print changes as they happen.

    Returns {name: final state}. Interrupting the cell is safe: the exports
    keep running on Google's side, and re-running the cell picks them up.
    """
    seen, started = {}, time.time()
    pending = set(names)
    while pending:
        current = states(ee)
        for name in sorted(pending):
            state = current.get(name, "UNKNOWN")
            if seen.get(name) != state:
                minutes = (time.time() - started) / 60
                log(f"  [{minutes:5.1f} min] {name}: {state}")
                seen[name] = state
            if state in DONE or state in FAILED:
                pending.discard(name)
        if pending:
            if time.time() - started > timeout_hours * 3600:
                log("  stopped waiting; re-run the cell later to continue")
                break
            sleep(poll)
    return seen


def read(ee, asset, page=5000):
    """Every feature's properties from a table asset, a page at a time."""
    fc = ee.FeatureCollection(asset)
    n = fc.size().getInfo()
    rows = []
    for offset in range(0, n, page):
        for feature in fc.toList(page, offset).getInfo():
            props = dict(feature.get("properties", {}))
            for key in DROP:
                props.pop(key, None)
            rows.append(props)
    return rows


def read_csv(path):
    """Rows of a Drive export CSV, numbers as floats, blanks as None."""
    def value(text):
        if text == "":
            return None
        try:
            return float(text)
        except ValueError:
            return text
    with open(path, newline="", encoding="utf-8") as fh:
        return [{k: value(v) for k, v in row.items() if k not in DROP} for row in csv.DictReader(fh)]


class Exports:
    """One place that knows where results go and how to get them back."""

    def __init__(self, ee, project, local_dir, log=print):
        self.ee, self.project, self.local = ee, project, Path(local_dir)
        try:
            ensure_folder(ee, project)
            self.mode = "asset"
        except Exception as exc:
            self.mode = "drive"
            log("Cannot write to the Earth Engine asset folder "
                f"({str(exc)[:90]}).\nUsing Google Drive instead: results go to the Drive folder "
                f"'{DRIVE_FOLDER}' as CSV files.")

    @property
    def where(self):
        if self.mode == "asset":
            return folder(self.project)
        return f"Google Drive folder '{DRIVE_FOLDER}' -> download into {self.local}"

    def path(self, name):
        return self.local / f"{slug(name)}.csv"

    def have(self, name):
        if self.path(name).exists():
            return True
        return self.mode == "asset" and exists(self.ee, asset_id(self.project, name))

    def plan(self, names):
        return plan(names, {n: self.have(n) for n in names}, states(self.ee),
                    finished_means_download=self.mode == "drive")

    def start(self, name, collection):
        table = self.ee.batch.Export.table
        if self.mode == "asset":
            task = table.toAsset(collection=collection, description=name,
                                 assetId=asset_id(self.project, name))
        else:
            task = table.toDrive(collection=collection, description=name, folder=DRIVE_FOLDER,
                                 fileNamePrefix=slug(name), fileFormat="CSV")
        task.start()
        return task

    def rows(self, name):
        """The export's rows, or None if they are not available yet."""
        if self.path(name).exists():
            return read_csv(self.path(name))
        if self.mode == "asset" and exists(self.ee, asset_id(self.project, name)):
            return read(self.ee, asset_id(self.project, name))
        return None

    def missing(self, names):
        """What to tell the user about results that are finished but not here."""
        if self.mode == "drive":
            self.local.mkdir(parents=True, exist_ok=True)
            files = ", ".join(f"{slug(n)}.csv" for n in names)
            return (f"Download these from the Google Drive folder '{DRIVE_FOLDER}' into\n  {self.local}\n"
                    f"then re-run this cell: {files}")
        return "Not finished yet: " + ", ".join(names) + ". Re-run this cell later."
