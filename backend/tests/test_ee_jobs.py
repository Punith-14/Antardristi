"""
Notebooks 10 and 11 run their heavy Earth Engine work as batch exports
(evaluation/ee_jobs.py). Re-running must never repeat finished work or
submit a second copy of a running export.
"""

import sys
from types import SimpleNamespace

from tests.test_green_cover_eval import EVALUATION  # noqa: F401  (puts evaluation/ on sys.path)

import ee_jobs as J  # noqa: E402


def test_names_and_paths():
    assert J.slug("Bangalore Urban") == "Bangalore_Urban"
    assert J.slug("green_The Nilgiris!") == "green_The_Nilgiris"
    assert J.asset_id("my-proj", "green Karnal") == "projects/my-proj/assets/antardrishti/green_Karnal"


def test_plan_reads_waits_or_submits():
    current = {"b": "RUNNING", "c": "PENDING", "d": "FAILED", "e": "SUCCEEDED"}
    plan = J.plan(["a", "b", "c", "d", "e"], {"a": True}, current)
    assert plan == {"a": "read", "b": "wait", "c": "wait", "d": "submit", "e": "submit"}
    drive = J.plan(["a", "b", "c", "d", "e"], {"a": True}, current, finished_means_download=True)
    assert drive["e"] == "download", "a finished Drive export is downloaded, never redone"
    assert drive["d"] == "submit"


class NoAssets:
    """A project whose asset folder cannot be written - the user's case."""

    def __init__(self):
        self.started = []
        task = lambda **kw: SimpleNamespace(start=lambda: self.started.append(kw))  # noqa: E731
        self.batch = SimpleNamespace(Export=SimpleNamespace(table=SimpleNamespace(toAsset=task, toDrive=task)))

        def refuse(path):
            raise RuntimeError(f"Asset '{path}' does not exist or doesn't allow this operation.")
        self.data = SimpleNamespace(getAsset=refuse, createFolder=refuse,
                                    listOperations=lambda: [{"metadata": {"description": "green_Karnal",
                                                                          "state": "SUCCEEDED"}}])


def test_without_asset_storage_exports_go_to_drive(tmp_path):
    ee, lines = NoAssets(), []
    x = J.Exports(ee, "hackrx-submission", tmp_path / "ee_exports", log=lines.append)
    assert x.mode == "drive" and "Google Drive" in lines[0]
    x.start("green_Bastar", "fc")
    assert ee.started[0]["folder"] == J.DRIVE_FOLDER and ee.started[0]["fileFormat"] == "CSV"
    assert "assetId" not in ee.started[0]

    assert x.plan(["green_Karnal", "green_Bastar"]) == {"green_Karnal": "download", "green_Bastar": "submit"}
    assert x.rows("green_Karnal") is None
    message = x.missing(["green_Karnal"])
    assert "green_Karnal.csv" in message and str(tmp_path / "ee_exports") in message

    (tmp_path / "ee_exports" / "green_Karnal.csv").write_text(
        "system:index,ndvi_p10,tree,district_note,.geo\n0,0.61,1,,{}\n1,0.12,0,x,{}\n", encoding="utf-8")
    assert x.plan(["green_Karnal"]) == {"green_Karnal": "read"}
    assert x.rows("green_Karnal") == [{"ndvi_p10": 0.61, "tree": 1.0, "district_note": None},
                                      {"ndvi_p10": 0.12, "tree": 0.0, "district_note": "x"}]


class FakeEE:
    """Operations advance one step per listOperations() call."""

    def __init__(self, scripts):
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.assets = set()
        self.data = SimpleNamespace(listOperations=self._list, getAsset=self._get,
                                    createFolder=self.assets.add)

    def _list(self):
        ops = []
        for name, steps in self.scripts.items():
            state = steps.pop(0) if len(steps) > 1 else steps[0]
            ops.append({"metadata": {"description": name, "state": state}})
        return ops

    def _get(self, path):
        if path not in self.assets:
            raise LookupError(path)
        return {"id": path}


def test_wait_prints_changes_and_stops_when_all_finish():
    ee = FakeEE({"x": ["PENDING", "RUNNING", "RUNNING", "SUCCEEDED"], "y": ["RUNNING", "FAILED"]})
    lines, sleeps = [], []
    final = J.wait(ee, ["x", "y"], poll=1, log=lines.append, sleep=sleeps.append)
    assert final == {"x": "SUCCEEDED", "y": "FAILED"}
    assert sum("x:" in line for line in lines) == 3, "only changes are printed"
    assert len(sleeps) == 3
    assert J.wait(ee, [], log=lines.append, sleep=sleeps.append) == {}


def test_folder_is_created_once():
    ee = FakeEE({})
    path = J.ensure_folder(ee, "p")
    assert path in ee.assets and J.exists(ee, path)
    J.ensure_folder(ee, "p")
    assert not J.exists(ee, "projects/p/assets/antardrishti/missing")


def test_read_pages_through_a_table(monkeypatch):
    rows = [{"properties": {"system:index": str(i), "v": i}} for i in range(12)]

    class FC:
        def __init__(self, asset):
            pass

        def size(self):
            return SimpleNamespace(getInfo=lambda: len(rows))

        def toList(self, count, offset):
            return SimpleNamespace(getInfo=lambda: rows[offset:offset + count])

    ee = SimpleNamespace(FeatureCollection=FC)
    out = J.read(ee, "asset", page=5)
    assert [r["v"] for r in out] == list(range(12))
    assert all("system:index" not in r for r in out)
    assert "ee_jobs" in sys.modules
