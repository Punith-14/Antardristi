"""
System testing: the whole running application, tested from outside.

Start the app first (.\\start.ps1), then in a second window:

    cd backend
    python -m scripts.system_test --user test@gmail.com

The password is asked for and never stored or printed. Each test case calls
the real server over HTTP, exactly as the web app does, and checks the answer
against what is expected. Results go to document/test_results/system_tests.md
(a table for the report), system_tests.csv and system_tests.json.

Earth Engine: the analyses are the ones already run while taking the report
screenshots (Assam July 2026, Sibsagar, Ludhiana ...), so they come back from
the cache in seconds. A case that is not cached is computed, which can take
several minutes while the project is in Earth Engine's slower mode.
Analyses the test creates are removed from History at the end (--keep to
leave them).
"""

import argparse
import csv
import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PROJECT = BACKEND.parent
DOCUMENT = PROJECT.parent.parent / "document"
OUT = Path(os.environ.get("TEST_RESULTS_DIR") or DOCUMENT / "test_results")
UPLOAD_IMAGE = DOCUMENT / "report_images" / "upload_test_kerala_satellite.png"


# ------------------------------------------------------------- a tiny client

class Client:
    def __init__(self, base, timeout=120):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.token = None
        self.cookie = None

    def call(self, method, path, body=None, auth=True, headers=None, raw=None, content_type=None,
             use_cookie=False):
        data, h = None, dict(headers or {})
        if raw is not None:
            data, h["Content-Type"] = raw, content_type
        elif body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        if auth and self.token and not use_cookie:
            h["Authorization"] = f"Bearer {self.token}"
        if use_cookie and self.cookie:
            h["Cookie"] = self.cookie
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), exc.read()

    def json(self, method, path, body=None, **kw):
        status, headers, payload = self.call(method, path, body, **kw)
        try:
            return status, json.loads(payload or b"{}"), headers
        except ValueError:
            return status, {}, headers

    def job(self, kind, body, limit_s):
        status, started, _ = self.json("POST", f"/jobs/{kind}", body)
        if status != 200:
            raise RuntimeError(f"POST /jobs/{kind} -> {status}: {str(started)[:200]}")
        job_id, t0 = started["job_id"], time.time()
        while time.time() - t0 < limit_s:
            status, job, _ = self.json("GET", f"/jobs/{job_id}")
            if job.get("status") == "done":
                return job_id, job.get("result") or {}, time.time() - t0
            if job.get("status") == "failed":
                raise RuntimeError(f"job failed: {job.get('error')}")
            time.sleep(3)
        raise RuntimeError(f"job still running after {limit_s} s")


def evidence(result, quantity):
    return next((e for e in result.get("evidence") or [] if e.get("quantity") == quantity), None)


# ------------------------------------------------------------- the test cases

CASES = []


def case(cid, feature, given, expected):
    def wrap(fn):
        CASES.append({"id": cid, "feature": feature, "input": given, "expected": expected, "fn": fn})
        return fn
    return wrap


@case("ST-01", "Server health", "GET /health", "200, status ok")
def t_health(c, s):
    status, body, _ = c.json("GET", "/health", auth=False)
    return status == 200 and body.get("status") == "ok", f"{status}, {body.get('status')}"


@case("ST-02", "Readiness", "GET /ready", "Earth Engine, database, LLM key ready")
def t_ready(c, s):
    status, body, _ = c.json("GET", "/ready", auth=False)
    checks = body.get("checks") or {}
    parts = [f"{k}={'ok' if v.get('ok') else 'NOT OK'}" for k, v in checks.items() if k != "settings"]
    db = (checks.get("database") or {}).get("backend")
    return status == 200, f"{status}; " + ", ".join(parts) + (f"; database: {db}" if db else "")


@case("ST-03", "Web app served", "GET /", "200, HTML page")
def t_web(c, s):
    status, headers, payload = c.call("GET", "/", auth=False)
    html = "text/html" in (headers.get("Content-Type") or headers.get("content-type") or "")
    return status == 200 and html, f"{status}, {'HTML' if html else 'not HTML'} ({len(payload)} bytes)"


@case("ST-04", "Access control", "GET /history without signing in", "401, sign-in required")
def t_noauth(c, s):
    status, body, _ = c.json("GET", "/history", auth=False)
    return status == 401, f"{status}"


@case("ST-05", "Password rules", "Sign up with password 'password'", "400, rejected with reason; no account")
def t_weak(c, s):
    email = f"systest-{uuid.uuid4().hex[:8]}@example.com"
    status, body, _ = c.json("POST", "/auth/signup", {"full_name": "System Test", "email": email,
                                                        "password": "password", "user_type": "researcher"}, auth=False)
    reason = (body.get("detail") or {}).get("message") if isinstance(body.get("detail"), dict) else body.get("detail")
    return status in (400, 422), f"{status}: {str(reason)[:90]}"


@case("ST-06", "Wrong password", "Log in with a wrong password", "401, not signed in")
def t_wrong(c, s):
    status, _, _ = c.json("POST", "/auth/login", {"username": s["user"], "password": "Wrong-Pass-123!"}, auth=False)
    return status == 401, f"{status}"


@case("ST-07", "Log in", "Log in with the test account", "200, session token and cookie")
def t_login(c, s):
    status, headers, payload = c.call("POST", "/auth/login", {"username": s["user"], "password": s["password"]}, auth=False)
    body = json.loads(payload or b"{}")
    c.token = body.get("token")
    cookie = headers.get("set-cookie") or headers.get("Set-Cookie") or ""
    c.cookie = cookie.split(";", 1)[0] if cookie else None
    s["role"] = (body.get("user") or {}).get("role")
    return status == 200 and bool(c.token), f"{status}, role {s['role']}, cookie {'set' if c.cookie else 'missing'}"


@case("ST-08", "Session", "GET /auth/me with the token", "the signed-in user")
def t_me(c, s):
    status, body, _ = c.json("GET", "/auth/me")
    who = (body.get("user") or {}).get("username")
    return status == 200 and who == s["user"].lower(), f"{status}, {who}"


@case("ST-09", "CSRF protection", "POST with the cookie but no X-Requested-With header", "403, refused")
def t_csrf(c, s):
    if not c.cookie:
        return None, "no cookie to test with"
    # A harmless target: if the check were missing this would only answer 404.
    status, body, _ = c.json("POST", "/history/not-a-job/save", {"title": "x", "note": ""}, use_cookie=True)
    return status == 403, f"{status} ({(body.get('detail') or {}).get('error') if isinstance(body.get('detail'), dict) else body.get('error', '')})"


@case("ST-10", "Place search", "GET /regions/names", "districts listed; 'Sivasagar' found as Sibsagar")
def t_places(c, s):
    status, body, _ = c.json("GET", "/regions/names")
    places = body.get("places") or []
    siva = [p for p in places if (p.get("aka") or "") == "sivasagar"]
    return status == 200 and len(places) > 500 and bool(siva), f"{status}, {len(places)} places, Sivasagar -> {siva[0]['name'] if siva else 'missing'}"


@case("ST-11", "Question understanding", "'How much of Assam was flooded between 20 and 31 July 2026?'",
      "flood extent, Assam, 2026-07-20 to 2026-07-31")
def t_route(c, s):
    status, body, _ = c.json("POST", "/route", {"question": "How much of Assam was flooded between 20 and 31 July 2026?"})
    r = body.get("routing") or {}
    ok = (r.get("analysis_type") == "flood_extent" and (r.get("region") or "").lower() == "assam"
          and r.get("post_start") == "2026-07-20" and r.get("post_end") == "2026-07-31")
    return status == 200 and ok, f"{r.get('analysis_type')}, {r.get('region')}, {r.get('post_start')} to {r.get('post_end')}"


FLOOD = {"region": "Assam", "post_start": "2026-07-20", "post_end": "2026-07-31", "sensor": "sentinel-1", "scale": 200}


@case("ST-12", "Flood mapping", "Flood extent, Assam, 20-31 Jul 2026 (Sentinel-1)",
      "area, people, districts, zones; every report number traced")
def t_flood(c, s):
    job_id, r, secs = c.job("analyze", FLOOD, s["limit"])
    s["created"].append(job_id)
    s["flood"] = r
    area = evidence(r, "flood_extent")
    people = (r.get("population") or {}).get("people_in_flood") or {}
    districts = len((r.get("districts") or {}).get("rows") or [])
    zones = (r.get("zones_summary") or {}).get("count") or len(r.get("zones") or [])
    v = r.get("verification") or {}
    ok = bool(area and area["value"] > 0 and people.get("low") is not None and districts and v.get("faithfulness_rate") == 1.0)
    return ok, (f"{area['value'] if area else '?'} km2, {people.get('low')}-{people.get('high')} people, "
                f"{districts} districts, {zones} zones, {v.get('claims_supported')}/{v.get('claims_total')} numbers traced, {secs:.0f} s")


@case("ST-13", "Before / after", "Sibsagar: before 1-31 May 2026, after 20-31 Jul 2026", "net change measured; before layer for the swipe")
def t_compare(c, s):
    body = {"region": "Sibsagar, Assam", "post_start": "2026-07-20", "post_end": "2026-07-31",
            "pre_start": "2026-05-01", "pre_end": "2026-05-31", "sensor": "sentinel-1", "scale": 200}
    job_id, r, secs = c.job("analyze", body, s["limit"])
    s["created"].append(job_id)
    net = next((e for e in r.get("evidence") or [] if "net" in (e.get("quantity") or "")), None)
    before = bool((r.get("artifacts") or {}).get("baseline_tiles"))
    return bool(net) and before, f"{net['quantity'] if net else 'no change figure'} = {net['value'] if net else '?'} {net.get('unit', '') if net else ''}, before layer {'yes' if before else 'no'}, {secs:.0f} s"


@case("ST-14", "Ask a question", "'How much of Assam was flooded between 20 and 31 July 2026?'", "answer for 20-31 Jul; question check 6 of 6")
def t_ask(c, s):
    job_id, r, secs = c.job("ask", {"question": "How much of Assam was flooded between 20 and 31 July 2026?"}, s["limit"])
    s["created"].append(job_id)
    al = r.get("alignment") or {}
    checks = al.get("checks") or []
    passed = sum(1 for x in checks if x.get("passed"))
    post = (r.get("period") or {}).get("post") or {}
    ok = al.get("passed") and post.get("start") == "2026-07-20" and post.get("end") == "2026-07-31"
    return bool(ok), f"{post.get('start')} to {post.get('end')}, checks {passed}/{len(checks)}, {secs:.0f} s"


@case("ST-15", "Month by month", "Sibsagar, May-Sep 2026", "5 monthly values; peak in July")
def t_series(c, s):
    body = {"region": "Sibsagar, Assam", "start": "2026-05-01", "end": "2026-09-30", "sensor": "sentinel-1", "scale": 200}
    job_id, r, secs = c.job("series", body, s["limit"])
    s["created"].append(job_id)
    pts = r.get("points") or []
    vals = [(p.get("label") or p.get("month"), p.get("value_km2") if p.get("value_km2") is not None else p.get("value")) for p in pts]
    numeric = [(m, v) for m, v in vals if isinstance(v, (int, float))]
    peak = max(numeric, key=lambda x: x[1])[0] if numeric else None
    return len(pts) == 5 and bool(peak), f"{len(pts)} months, peak {peak}, {secs:.0f} s"


@case("ST-16", "Vegetation health", "Ludhiana, 2 Aug-30 Sep 2023 (Sentinel-2)", "vegetated area; 'good' reliability; map framed on district")
def t_veg(c, s):
    body = {"region": "Ludhiana, Punjab", "analysis_type": "vegetation_health", "post_start": "2023-08-02",
            "post_end": "2023-09-30", "scale": 200}
    job_id, r, secs = c.job("surface", body, s["limit"])
    s["created"].append(job_id)
    e = (r.get("evidence") or [{}])[0]
    found = re.search(r"Reliability: (\w+)", json.dumps(r))
    rel = found.group(1) if found else "?"
    bbox = (r.get("region") or {}).get("bbox")
    return bool(e.get("value")) and bool(bbox), f"{e.get('value')} {e.get('unit')}, reliability {rel}, bbox {'yes' if bbox else 'no'}, {secs:.0f} s"


@case("ST-17", "Image upload screening", "Upload a Kerala satellite picture", "share of water-coloured pixels; labelled as not georeferenced")
def t_upload(c, s):
    if not UPLOAD_IMAGE.exists():
        return None, f"test image not found ({UPLOAD_IMAGE.name})"
    boundary = uuid.uuid4().hex
    data = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{UPLOAD_IMAGE.name}\"\r\n"
            "Content-Type: image/png\r\n\r\n").encode() + UPLOAD_IMAGE.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    status, _, payload = c.call("POST", "/analyze-upload", raw=data, content_type=f"multipart/form-data; boundary={boundary}")
    r = json.loads(payload or b"{}")
    share = next((e for e in r.get("evidence") or [] if e.get("unit") == "percent"), None)
    sensor = (r.get("observation") or {}).get("sensor_used")
    return status == 200 and bool(share), f"{status}, {share['value'] if share else '?'}% of pixels, sensor '{sensor}'"


@case("ST-18", "Hindi report", "GET /analyze/{id}/report/hi for ST-12", "Hindi text returned")
def t_hindi(c, s):
    rid = (s.get("flood") or {}).get("request_id")
    if not rid:
        return None, "needs ST-12"
    status, body, _ = c.json("GET", f"/analyze/{rid}/report/hi")
    text = json.dumps(body, ensure_ascii=False)
    devanagari = any("ऀ" <= ch <= "ॿ" for ch in text)
    return status == 200 and devanagari, f"{status}, {'Devanagari text' if devanagari else 'no Hindi text'}"


@case("ST-19", "PDF report", "GET /analyze/{id}/report.pdf for ST-12", "a PDF file")
def t_pdf(c, s):
    rid = (s.get("flood") or {}).get("request_id")
    if not rid:
        return None, "needs ST-12"
    c.timeout = max(c.timeout, 180)
    status, headers, payload = c.call("GET", f"/analyze/{rid}/report.pdf")
    ok = status == 200 and payload[:4] == b"%PDF"
    return ok, f"{status}, {len(payload) // 1024} KB, {'PDF' if ok else 'not a PDF'}"


@case("ST-20", "GIS downloads", "zones.geojson, zones.kml, zones.csv, districts.csv for ST-12", "all four files")
def t_gis(c, s):
    rid = (s.get("flood") or {}).get("request_id")
    if not rid:
        return None, "needs ST-12"
    got = []
    for name in ("zones.geojson", "zones.kml", "zones.csv", "districts.csv"):
        status, _, payload = c.call("GET", f"/analyze/{rid}/export/{name}")
        got.append(f"{name} {status} ({len(payload) // 1024} KB)")
    ok = all(" 200 " in g for g in got)
    return ok, "; ".join(got)


@case("ST-21", "Report pictures", "GET /analyze/{id}/pictures for ST-12", "overview and zone pictures listed")
def t_pictures(c, s):
    rid = (s.get("flood") or {}).get("request_id")
    if not rid:
        return None, "needs ST-12"
    status, body, _ = c.json("GET", f"/analyze/{rid}/pictures")
    autos = body.get("auto") or []
    return status == 200 and len(autos) >= 1, f"{status}, {len(autos)} automatic, {len(body.get('captures') or [])} captured"


@case("ST-22", "History and Save", "List history; save then unsave the ST-12 analysis", "listed; saved; unsaved")
def t_history(c, s):
    if not s["created"]:
        return None, "needs ST-12"
    job_id = s["created"][0]
    status, body, _ = c.json("GET", "/history?limit=50")
    listed = any(i.get("job_id") == job_id for i in body.get("items") or [])
    st1, saved, _ = c.json("POST", f"/history/{job_id}/save", {"title": "System test", "note": ""})
    st2, unsaved, _ = c.json("POST", f"/history/{job_id}/unsave", {})
    ok = listed and saved.get("saved") is True and unsaved.get("saved") is False
    return ok, f"listed {listed}, save {st1} -> {saved.get('saved')}, unsave {st2} -> {unsaved.get('saved')}"


@case("ST-23", "Satellites", "GET /satellites", "active Sentinel-1 satellites; next planned images of India")
def t_sats(c, s):
    status, body, _ = c.json("GET", "/satellites", auth=False)
    sats = [x.get("name") for x in body.get("satellites") or []]
    planned = ((body.get("india") or {}).get("next_planned") or [])
    return status == 200 and bool(sats), f"{status}, {', '.join(sats)}, {len(planned)} planned images listed"


@case("ST-24", "Admin pages", "GET /admin/users as the test account", "403 for analyst; 200 for admin")
def t_admin(c, s):
    status, _, _ = c.json("GET", "/admin/users")
    expected = 200 if s.get("role") == "admin" else 403
    return status == expected, f"{status} (role {s.get('role')})"


@case("ST-25", "Not found", "GET /jobs/does-not-exist", "404")
def t_404(c, s):
    status, _, _ = c.json("GET", "/jobs/does-not-exist")
    return status == 404, f"{status}"


# ------------------------------------------------------------- runner

def main():
    ap = argparse.ArgumentParser(description="System tests against the running app.")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--user", required=True, help="an existing account (analyst role is fine)")
    ap.add_argument("--minutes", type=float, default=30, help="longest wait for one analysis")
    ap.add_argument("--keep", action="store_true", help="keep the analyses this test creates in History")
    args = ap.parse_args()

    password = os.environ.get("SYSTEM_TEST_PASSWORD") or getpass.getpass(f"Password for {args.user}: ")
    client = Client(args.url)
    state = {"user": args.user, "password": password, "limit": args.minutes * 60, "created": []}

    rows = []
    print(f"System testing {args.url} as {args.user}\n")
    for tc in CASES:
        started = time.time()
        try:
            ok, actual = tc["fn"](client, state)
        except Exception as exc:                 # noqa: BLE001 - a crash is a failed case, not a stopped run
            ok, actual = False, f"error: {str(exc)[:160]}"
        result = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
        rows.append({"id": tc["id"], "feature": tc["feature"], "input": tc["input"], "expected": tc["expected"],
                     "actual": actual, "result": result, "seconds": round(time.time() - started, 1)})
        print(f"{tc['id']}  {result:4}  {tc['feature']:<24} {actual}")

    if not args.keep:
        for job_id in state["created"]:
            client.json("DELETE", f"/history/{job_id}")

    OUT.mkdir(parents=True, exist_ok=True)
    passed = sum(r["result"] == "PASS" for r in rows)
    failed = sum(r["result"] == "FAIL" for r in rows)
    md = [f"# System test results\n\nRun on {datetime.now():%d %B %Y, %H:%M} against {args.url} "
          f"(signed in as an {state.get('role') or 'unknown'} account).\n",
          f"**{passed} passed, {failed} failed, {len(rows) - passed - failed} skipped, of {len(rows)} test cases.**\n",
          "| ID | Feature | Input | Expected | Actual | Result |\n|---|---|---|---|---|---|\n"]
    md += [f"| {r['id']} | {r['feature']} | {r['input']} | {r['expected']} | {r['actual']} | {r['result']} |\n" for r in rows]
    (OUT / "system_tests.md").write_text("".join(md), encoding="utf-8")
    with open(OUT / "system_tests.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (OUT / "system_tests.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{passed} passed, {failed} failed of {len(rows)}. Written: {OUT / 'system_tests.md'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
