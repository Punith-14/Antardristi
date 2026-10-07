"""
Unit and integration tests, run and summarised for the project report.

    cd backend
    python -m scripts.run_tests

What it does
------------
1. Sorts every backend test file into UNIT (one function or module on its own)
   or INTEGRATION (several parts working together: the API with sign-in and
   the database, a whole analysis with Earth Engine replaced by a stand-in,
   PDF export, notebooks run end to end). The rule is written below, so the
   split can be checked.
2. Runs each group with pytest and reads the JUnit XML it writes.
3. Runs the frontend's unit tests with Node.
4. Writes document/test_results/automated_tests.md (tables for the report),
   automated_tests.json, and the raw JUnit XML files.

No Earth Engine quota is used: tests that would call it are marked and left
out of the default run, exactly as `pytest` alone does.
"""

import json
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PROJECT = BACKEND.parent
FRONTEND = PROJECT / "frontend"
OUT = Path(__import__("os").environ.get("TEST_RESULTS_DIR") or PROJECT.parent.parent / "document" / "test_results")

# A test file is INTEGRATION when it exercises parts together: the HTTP API
# (FastAPI's TestClient), the storage layer against a database, the flood
# pipeline end to end on the Earth Engine stand-in, or a notebook run whole.
INTEGRATION_SIGNS = ("TestClient", "fake_ee", "flood_scenario", "mongomock", "use_client(",
                     "exec(compile(", "_synthetic_dataset", "end_to_end", "export_pdf")

# What each test file is about, for the report table (by file-name keyword).
AREAS = [
    ("auth|signup|accounts|production|password", "Accounts, sign-in, security"),
    ("store|cache|saving|history|jobs", "Storage, cache, history, saving"),
    ("routing|alignment|dateparse|translate|verification|report|evidence|tree_format", "Ask: routing, checks, report text"),
    ("sar|polarisation|change|spatial|terrain|optical|scale|sen1floods|india|events|metrics|thresholds|method", "Flood detection and its measurement"),
    ("surface|classifier|landcover|green_cover|crop_stress|ee_jobs", "Surface analyses and classifier"),
    ("zones|districts|population|places|regions|boundar|footprint|mapping", "Zones, districts, people, places"),
    ("pdf|gis|pictures|export|swipe|report_figures", "Exports, pictures, figures"),
    ("satellites|latest|scenes", "Satellites and latest images"),
    ("group_a|timeseries|rgb_upload", "Whole analyses: flood, monthly, image upload"),
    ("config|earth_engine_start|region_bbox", "Configuration and start-up"),
]
DEFAULT_MARKERS = "not slow and not llm and not api and not earthengine"


def area_of(name):
    for pattern, label in AREAS:
        if re.search(pattern, name):
            return label
    return "Other"


def classify():
    unit, integration = [], []
    for path in sorted((BACKEND / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        (integration if any(sign in text for sign in INTEGRATION_SIGNS) else unit).append(path)
    return unit, integration


def run_pytest(files, xml_path):
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-o", "addopts=",
           "-m", DEFAULT_MARKERS, "--tb=line", "-W", "ignore", f"--junitxml={xml_path}",
           *[str(f.relative_to(BACKEND)) for f in files]]
    started = time.time()
    proc = subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True)
    return proc, time.time() - started


def read_junit(xml_path):
    """Totals and per-file counts from a pytest JUnit XML file."""
    root = ET.parse(xml_path).getroot()
    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
    totals = {"tests": 0, "passed": 0, "failed": 0, "skipped": 0, "time_s": 0.0}
    per_file, failures = {}, []
    for suite in suites:
        for case in suite.iter("testcase"):
            name = (case.get("classname") or "").split(".")[-1] or case.get("file") or "?"
            row = per_file.setdefault(name, {"tests": 0, "passed": 0, "failed": 0, "skipped": 0})
            outcome = "passed"
            if case.find("failure") is not None or case.find("error") is not None:
                outcome = "failed"
                failures.append(f"{name}::{case.get('name')}")
            elif case.find("skipped") is not None:
                outcome = "skipped"
            for bucket in (row, totals):
                bucket["tests"] += 1
                bucket[outcome] += 1
            totals["time_s"] += float(case.get("time") or 0)
    return totals, per_file, failures


def run_frontend():
    node = shutil.which("node")
    tests = sorted((FRONTEND / "src" / "lib").glob("*.test.js"))
    if not node or not tests:
        return None
    started = time.time()
    proc = subprocess.run([node, "--test", "--test-reporter=tap", *[str(t) for t in tests]],
                          cwd=FRONTEND, capture_output=True, text=True)
    out = proc.stdout + proc.stderr

    def count(key):
        found = re.findall(rf"^# {key} (\d+)", out, re.M)
        return int(found[-1]) if found else 0
    failed_names = re.findall(r"^not ok \d+ - (.+)$", out, re.M)
    return {"files": len(tests), "tests": count("tests"), "passed": count("pass"), "failed": count("fail"),
            "skipped": count("skipped"), "time_s": round(time.time() - started, 1), "failures": failed_names[:20]}


def table(rows, headers):
    line = "| " + " | ".join(headers) + " |\n|" + "|".join("---" for _ in headers) + "|\n"
    return line + "".join("| " + " | ".join(str(c) for c in r) + " |\n" for r in rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    unit, integration = classify()
    print(f"Backend test files: {len(unit)} unit, {len(integration)} integration")

    results = {}
    for label, files in (("unit", unit), ("integration", integration)):
        xml_path = OUT / f"junit_{label}.xml"
        print(f"\nRunning {label} tests ({len(files)} files)...")
        proc, wall = run_pytest(files, xml_path)
        print(proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-500:])
        totals, per_file, failures = read_junit(xml_path)
        totals["time_s"] = round(wall, 1)
        results[label] = {"totals": totals, "per_file": per_file, "failures": failures,
                          "files": [f.name for f in files]}

    print("\nRunning frontend unit tests (Node)...")
    fe = run_frontend()
    if fe:
        print(f"frontend: {fe['passed']} passed, {fe['failed']} failed in {fe['time_s']} s")
    else:
        print("frontend tests skipped (Node not found)")

    # ---- report tables
    rows = []
    for label, tool in (("unit", "pytest"), ("integration", "pytest")):
        t = results[label]["totals"]
        rows.append([f"Backend {label}", tool, len(results[label]["files"]), t["tests"], t["passed"],
                     t["failed"], t["skipped"], f"{t['time_s']} s"])
    if fe:
        rows.append(["Frontend unit", "Node test runner", fe["files"], fe["tests"], fe["passed"],
                     fe["failed"], fe["skipped"], f"{fe['time_s']} s"])
    total_tests = sum(r[3] for r in rows)
    total_passed = sum(r[4] for r in rows)

    by_area = {}
    for label in ("unit", "integration"):
        for name, c in results[label]["per_file"].items():
            key = (area_of(name), label)
            a = by_area.setdefault(key, {"files": 0, "tests": 0, "passed": 0})
            a["files"] += 1
            a["tests"] += c["tests"]
            a["passed"] += c["passed"]
    area_rows = [[area, level, v["files"], v["tests"], v["passed"]]
                 for (area, level), v in sorted(by_area.items())]

    md = [f"# Automated test results\n\nRun on {datetime.now():%d %B %Y, %H:%M} "
          f"(Python {sys.version.split()[0]}).\n",
          f"**{total_passed} of {total_tests} tests passed.**\n",
          "## Summary by level\n", table(rows, ["Level", "Tool", "Files", "Tests", "Passed", "Failed", "Skipped", "Time"]),
          "\n## Backend tests by area\n", table(area_rows, ["Area", "Level", "Files", "Tests", "Passed"]),
          "\nUnit: a function or module on its own. Integration: parts together - the HTTP API with "
          "sign-in, roles and the database; a whole flood analysis on an Earth Engine stand-in; "
          "PDF export; notebooks run end to end.\n"]
    fails = results["unit"]["failures"] + results["integration"]["failures"] + ((fe or {}).get("failures") or [])
    if fails:
        md.append("\n## Failures\n" + "".join(f"- `{f}`\n" for f in fails))
    (OUT / "automated_tests.md").write_text("\n".join(md), encoding="utf-8")
    (OUT / "automated_tests.json").write_text(json.dumps(
        {"run_at": datetime.now().isoformat(timespec="seconds"), "backend": results, "frontend": fe,
         "summary": rows}, indent=2), encoding="utf-8")

    print("\n" + table(rows, ["Level", "Tool", "Files", "Tests", "Passed", "Failed", "Skipped", "Time"]))
    print(f"Written: {OUT / 'automated_tests.md'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
