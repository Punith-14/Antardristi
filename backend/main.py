import json
import os
from pathlib import Path
from typing import ClassVar

from dotenv import load_dotenv

# Load .env before importing modules that read environment variables.
load_dotenv()

import threading
import time

from fastapi import FastAPI
from fastapi import File
from fastapi import Request
from fastapi import Form
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import analysis
from core import alignment
from core import mailer
from geo import regions
from core import cache
from geo import footprint
from pipeline import routing
from pipeline import timeseries
from pipeline import export_gis
from pipeline import latest as latest_mode
from detection import sar
from detection import surface
from legacy.gee_fetch import NoUsableImagery, analyze_region_water
from pipeline.report import build_report
from detection import rgb_upload
from legacy.query_parser import parse_query
from geo.regions import REGIONS, get_region_geometry, resolve_region

from core import paths
from core import earth_engine
from core import auth
from core import jobs
from core import progress
from core import security
from core import settings


BASE_DIR = paths.PROJECT_ROOT

app = FastAPI(title="Antardrishti API", version="1.0.0")
security.configure_logging()


@app.exception_handler(earth_engine.EarthEngineUnavailable)
def earth_engine_unavailable(request, exc):
    """Earth Engine could not start: a 503 that says why, not a 500 traceback.

    `reason` lets the frontend tell "your internet is down" from "your
    credentials are wrong" - they need different fixes.
    """
    reason = ("network" if isinstance(exc, earth_engine.EarthEngineUnreachable)
              else "authentication")
    return JSONResponse(status_code=503, content={"detail": {
        "error": f"earth_engine_{reason}", "message": str(exc)}})


# Said once at startup, before any request fails because of it.
for _problem in earth_engine.configuration_problems():
    print(f"WARNING: {_problem}. Earth Engine may not start.")

for _problem in settings.problems():
    print(f"WARNING: {_problem}.")


# ------------------------------------------------------- the front door
#
# One middleware for everything a request passes on the way in and out, in
# this order: size limit, sign-in and role, CSRF, quota rate limit, then the
# endpoint; on the way out, security headers and one log line. An exception
# nothing else handled becomes a 500 with an incident id - the traceback
# goes to the log, never to the browser.

def _deny(status, error, message, headers=None):
    return JSONResponse(status_code=status, headers=headers or {},
                        content={"detail": {"error": error, "message": message}})


def _token_from(request):
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip(), "header"
    cookie = request.cookies.get(auth.COOKIE)
    return (cookie, "cookie") if cookie else (None, None)


@app.middleware("http")
async def front_door(request: Request, call_next):
    started = time.monotonic()
    request_id = security.incident_id()
    method, path = request.method, request.url.path
    user = None
    response = None
    try:
        too_big = security.body_too_large(request.headers)
        if too_big:
            response = _deny(413, "too_large", too_big)
        else:
            token, carrier = _token_from(request)
            user = auth.read_token(token) if token else None
            request.state.user = user
            needed = auth.required_role(method, path)
            if settings.auth_required() and needed is not None:
                if user is None:
                    response = _deny(401, "login_required", "Please sign in.")
                elif not auth.allows(user, needed):
                    response = _deny(403, "forbidden",
                                     f"This needs the {needed} role; you are {user['role']}.")
                elif (carrier == "cookie" and method not in ("GET", "HEAD", "OPTIONS")
                      and not request.headers.get(auth.CSRF_HEADER)):
                    response = _deny(403, "csrf", "Missing the X-Requested-With header.")
            if response is None and security.spends_quota(method, path):
                key = user["username"] if user else (request.client.host if request.client else "?")
                allowed, retry = security.ANALYSIS_LIMITER.allow(key, settings.analyses_per_hour())
                if not allowed:
                    response = _deny(429, "rate_limited",
                                     f"Hourly limit of {settings.analyses_per_hour()} analyses "
                                     f"reached. Try again in {retry // 60 + 1} minutes.",
                                     {"Retry-After": str(retry)})
            if response is None:
                response = await call_next(request)
    except Exception as exc:                     # noqa: BLE001 - see above
        security.log.error("unhandled error", exc_info=exc, extra={"fields": {
            "incident": request_id, "method": method, "path": path}})
        response = _deny(500, "internal_error",
                         f"Something went wrong on the server. Quote incident {request_id}.")
    security.add_security_headers(response, path)
    response.headers["X-Request-ID"] = request_id
    security.event("request", id=request_id, method=method, path=path,
                   status=response.status_code, ms=round((time.monotonic() - started) * 1000),
                   user=user["username"] if user else None)
    return response


app.add_middleware(
    CORSMiddleware,
    # Only the web app's own origins. "*" with credentials would let any
    # website call this API from a signed-in browser.
    allow_origins=settings.cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

# Upload overlays only. data/ used to be served whole - the cache, raw data
# and uploaded boundary files with it.
(BASE_DIR / "outputs").mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=BASE_DIR / "outputs"), name="outputs")


@app.on_event("startup")
def _startup():
    created = auth.bootstrap_admin()
    if created:
        security.event("bootstrap admin created", username=created["username"])
    jobs.fail_interrupted()
    if os.environ.get("MAINTENANCE_SWEEP", "true").lower() != "false":
        def sweep():
            while True:
                try:
                    purge_everything()
                except Exception:                # noqa: BLE001 - a sweep must never stop the server
                    pass
                time.sleep(6 * 3600)
        threading.Thread(target=sweep, name="maintenance-sweep", daemon=True).start()
    if os.environ.get("SATELLITE_WARM", "true").lower() != "false":
        # Orbits, ESA's plan and the latest image, fetched once in the
        # background so the first visitor does not wait for three servers.
        from orbits import service as satellite_service
        threading.Thread(target=satellite_service.warm, name="satellite-warm", daemon=True).start()
    if settings.auth_required() and not auth.list_users():
        print("WARNING: no user accounts exist. Create an admin with\n"
              "    python -m scripts.manage_users create <username> --role admin\n"
              "or set ADMIN_USERNAME and ADMIN_PASSWORD in .env and restart.")


class QueryRequest(BaseModel):
    question: str
    mode: str | None = None
    region: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    cloud_limit: int | None = None
    composite_method: str | None = None


def build_analysis_response(
    question,
    mode=None,
    region=None,
    start_date=None,
    end_date=None,
    cloud_limit=None,
    composite_method=None,
):
    parsed = parse_query(question, mode)
    region_slug, region_config = resolve_region(question, region)
    region_geometry = get_region_geometry(region_config)

    analysis_kwargs = {
        "bbox": region_config["bbox"],
        "region_geometry": region_geometry,
        "slug": region_slug,
        "mode": parsed["mode"],
    }
    if start_date:
        analysis_kwargs["start_date"] = start_date
    if end_date:
        analysis_kwargs["end_date"] = end_date
    if cloud_limit is not None:
        analysis_kwargs["cloud_limit"] = cloud_limit
    if composite_method:
        analysis_kwargs["composite_method"] = composite_method

    try:
        water_percentage, explanation, analysis_metadata = analyze_region_water(
            **analysis_kwargs
        )
    except NoUsableImagery as exc:
        # Not a crash: the satellite genuinely could not see the ground. Report
        # that honestly rather than returning a misleading zero.
        return {
            "question": question,
            "intent": parsed["intent"],
            "mode": parsed["mode"],
            "analysis": parsed["analysis"],
            "region": {
                "slug": region_slug,
                "name": region_config["name"],
                "bbox": region_config["bbox"],
                "summary": region_config["summary"],
            },
            "water_percentage": None,
            "explanation": str(exc),
            "source_image": None,
            "output_image": None,
            "unobserved": {
                "reason": "no_usable_imagery",
                "scenes_available": exc.available,
                "scenes_passing_cloud_filter": 0,
                "cloud_limit": exc.cloud_limit,
                "date_range": {"start": exc.start_date, "end": exc.end_date},
                "suggestion": (
                    "Raise cloud_limit, widen the date window, or use Sentinel-1 "
                    "SAR, which is unaffected by cloud."
                ),
            },
            "metadata": {
                "source": "Google Earth Engine / COPERNICUS/S2_SR_HARMONIZED",
                "generated_at": parsed["generated_at"],
            },
            "suggested_next_queries": [],
        }

    return {
        "question": question,
        "intent": parsed["intent"],
        "mode": parsed["mode"],
        "analysis": parsed["analysis"],
        "region": {
            "slug": region_slug,
            "name": region_config["name"],
            "bbox": region_config["bbox"],
            "summary": region_config["summary"],
        },
        "water_percentage": water_percentage,
        "explanation": explanation,
        "source_image": analysis_metadata["raw_image_url"],
        "output_image": analysis_metadata["output_image_url"],
        "metadata": {
            "source": analysis_metadata["source"],
            "date_range": analysis_metadata["date_range"],
            "scene_count": analysis_metadata["scene_count"],
            "scenes_available": analysis_metadata["scenes_available"],
            "scenes_rejected_by_cloud_filter": analysis_metadata[
                "scenes_rejected_by_cloud_filter"
            ],
            "cloud_limit": analysis_metadata["cloud_limit"],
            "method": analysis_metadata["method"],
            "threshold": analysis_metadata["threshold"],
            "composite_method": analysis_metadata["composite_method"],
            "stats_scale_m": analysis_metadata["stats_scale_m"],
            "severity": analysis_metadata["severity"],
            "index_type": analysis_metadata["index_type"],
            "permanent_water_excluded": analysis_metadata["permanent_water_excluded"],
            "permanent_water_percentage": analysis_metadata["permanent_water_percentage"],
            "cloud_shadow_masked": analysis_metadata["cloud_shadow_masked"],
            "water_area_sq_km": analysis_metadata["water_area_sq_km"],
            "analysis_area_sq_km": analysis_metadata["analysis_area_sq_km"],
            "generated_at": parsed["generated_at"],
            "prototype_note": (
                "Region-based analysis uses spectral Sentinel-2 indices in Earth Engine."
            ),
        },
        "suggested_next_queries": [
            f"Compare flood situation in {region_config['name']} before and after monsoon",
            f"Show water presence trend in {region_config['name']}",
            f"Analyse agriculture stress in {region_config['name']}",
        ],
    }


FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST") or (BASE_DIR / "frontend" / "dist"))


@app.get("/")
def home():
    """The web app when it has been built (one origin, one server), else a
    short JSON line saying the API is up."""
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        from fastapi.responses import FileResponse
        return FileResponse(index, media_type="text/html")
    return {"message": "Antardrishti backend is running",
            "earth_engine_ready": earth_engine.is_ready(),
            "configuration_problems": earth_engine.configuration_problems()}


@app.get("/regions")
def list_regions():
    return {"regions": [{"slug": slug, **config} for slug, config in REGIONS.items()]}


@app.get("/detect")
def detect():
    return build_analysis_response("Show flooded areas in Kerala", mode="disaster", region="kerala")


class FloodRequest(BaseModel):
    """Where and when.

    Where is either a name - resolved through FAO GAUL 2015 - or a shape
    supplied directly. The shape exists because the boundary set is a 2015
    vintage: none of the thirty post-2015 districts measured by
    measure_boundary_coverage.py can be resolved by name, and Ladakh has no
    boundary at all. Earth Engine will measure inside any shape, so the caller
    can draw one.

    `pre` is optional: without it you get an extent, with it you get change.
    """

    region: str | None = None
    # One of these three, or none. See footprint.build.
    bbox: list[float] | None = None
    point: list[float] | None = None
    radius_km: float | None = None
    polygon: list[list[float]] | None = None
    # An uploaded boundary (geo/boundary_upload.py): GeoJSON Polygon or
    # MultiPolygon plus its `source`. Left out of cache keys while unset.
    boundary: dict | None = None

    # Required unless `latest` is set, in which case the newest Sentinel-1
    # pass decides them.
    post_start: str | None = None
    post_end: str | None = None
    pre_start: str | None = None
    pre_end: str | None = None
    # True: analyse the most recent Sentinel-1 pass over the area. Resolved to
    # real dates BEFORE the cache key is built, so today's "latest" and next
    # week's "latest" are never confused with each other.
    latest: bool | None = None
    # "sentinel-1" (default), "sentinel-2", or None to let cloud cover decide.
    # Both produce the same evidence quantities, so two runs that differ only
    # in this field are a like-for-like sensor comparison.
    sensor: str | None = "sentinel-1"
    # Optical only. None means the default, and is left out of the cache key
    # (see cache_key_for) so adding this field did not re-key every stored
    # analysis and re-run them all against Earth Engine quota.
    cloud_limit: int | None = None
    # None or "threshold": the validated darkness rule. "change": change
    # detection against the pre window - Sentinel-1 only, needs pre_start and
    # pre_end, and unvalidated until notebook 06 has been run.
    method: str | None = None
    # None: the terrain check runs wherever it was measured (once notebook 09
    # has filled sar.TERRAIN_RULE). False: switched off - the old map.
    terrain_check: bool | None = None
    scale: int = 100

    # Added after results were already cached. Left out of the cache key while
    # unset, so an old request and the same request today share a request_id.
    # ClassVar, or pydantic treats it as a field without a type and refuses to
    # build the model - which stops main.py importing at all.
    LATE_FIELDS: ClassVar[tuple[str, ...]] = ("cloud_limit", "method", "latest", "terrain_check",
                                              "boundary")

    def cache_key(self):
        """The request as the cache sees it.

        Late fields are dropped while unset, so adding a field never re-keys
        anything. The detection rule's version is ADDED, so changing the rule
        re-keys everything on purpose: a result computed at -20 dB must not be
        served as the answer of the scale-aware rule. Old entries stay
        reachable by request_id, and each still carries the validation of the
        rule that produced it.
        """
        key = self.model_dump()
        for name in self.LATE_FIELDS:
            if key.get(name) is None:
                key.pop(name, None)
        key["flood_rule"] = sar.RULE_VERSION
        # Added only once a terrain rule exists and is not switched off, so
        # until notebook 09 ships one no key changes - and the day it does,
        # every result is recomputed rather than served without the check.
        terrain = sar.terrain_version()
        if terrain and self.terrain_check is not False:
            key["terrain_rule"] = terrain
        # A named region resolves to a different outline under another
        # boundary set, so a result made under one is never served under the
        # other. Absent for the original 2015 set: nothing cached re-keys.
        from geo import boundaries
        tag = boundaries.cache_tag()
        if tag and self.region:
            key["boundary_set"] = tag
        return key
    generate_report: bool = True
    use_llm: bool = True


@app.post("/analyze")
def analyze(payload: FloodRequest):
    """Flood analysis returning the full evidence-bound response.

    Shape is defined by contracts/analysis_response.json. Every quantitative
    claim in `report.text` is checkable against `evidence` via `verification`.

    With `latest`, the newest Sentinel-1 pass over the area sets the dates.
    Every result leaves with the age of its newest image worked out NOW.
    """
    latest_info = None
    if payload.latest:
        # The date comes from the newest Sentinel-1 pass, so the analysis has
        # to be Sentinel-1 too: an optical composite of a radar pass's day is
        # usually empty, and would be no answer to "now".
        if payload.sensor == "sentinel-2":
            raise HTTPException(
                status_code=400,
                detail=("Latest-pass mode follows Sentinel-1 radar passes. Use sensor "
                        "'sentinel-1', or give dates for an optical analysis."),
            )
        if payload.method == "change":
            raise HTTPException(
                status_code=400,
                detail=("Latest-pass mode uses the standard threshold. For change "
                        "detection, give post and baseline dates."),
            )
        payload = payload.model_copy(update={"sensor": "sentinel-1"})
        geometry, _ = resolve_area_or_fail(payload)
        try:
            found = sar.latest_acquisition(geometry)
        except sar.NoSarImagery as exc:
            raise HTTPException(
                status_code=404,
                detail=(f"No Sentinel-1 pass over this area in the last "
                        f"{latest_mode.LOOKBACK_DAYS} days ({exc})."),
            ) from exc
        start, end = latest_mode.window(found["date"])
        payload = payload.model_copy(update={"post_start": start, "post_end": end,
                                             "latest": None})
        latest_info = {**found, "next_pass": latest_mode.next_pass_estimate(
            found["recent_passes"])}
    elif not (payload.post_start and payload.post_end):
        raise HTTPException(
            status_code=400,
            detail="Give post_start and post_end, or set latest to use the newest pass.",
        )

    result = _analyze_dates(payload)

    if latest_info:
        coverage = (result.get("observation") or {}).get("coverage_fraction")
        if coverage is not None and coverage < latest_mode.LOW_COVERAGE:
            # One pass often covers part of a state. Offer the few days before
            # it, with the dates stated, rather than silently widening.
            start, end = latest_mode.extended_window(latest_info["date"])
            latest_info["extend_offer"] = {
                "post_start": start, "post_end": end,
                "reason": (f"The latest pass covered {coverage:.0%} of the area. "
                           f"Including the {latest_mode.EXTEND_DAYS} days before it "
                           "may cover more, at the cost of mixing dates."),
            }
        result["latest"] = latest_info

    if result.get("acquisition"):
        result["acquisition"] = latest_mode.freshness(result["acquisition"])
    return result


def _analyze_dates(payload: FloodRequest):
    """The analysis for concrete dates - cached by request."""
    cache_key = payload.cache_key()
    request_id = cache.key_for(cache_key)

    # Said even when the answer is already stored: found live, a cached job
    # finished with no steps at all, and the progress card had nothing to say.
    progress.step("Checking for a stored result")
    cached = cache.get(cache_key)
    if cached:
        cached["request_id"] = request_id
        progress.step("Found it - no new satellite computation needed")
        return cached

    progress.step("Finding the area")
    geometry, meta = resolve_area_or_fail(payload)

    try:
        result = analysis.analyse_flood(
            region_geometry=geometry,
            region_meta=meta,
            post_start=payload.post_start,
            post_end=payload.post_end,
            pre_start=payload.pre_start,
            pre_end=payload.pre_end,
            force_sensor=payload.sensor,
            scale=payload.scale,
            cloud_limit=(payload.cloud_limit if payload.cloud_limit is not None
                         else analysis.optical.DEFAULT_CLOUD_LIMIT),
            method=payload.method or "threshold",
            terrain_check=payload.terrain_check,
        )
    except (sar.NoSarImagery, surface.NoOpticalImagery) as exc:
        sensor_name = (
            "Sentinel-2" if isinstance(exc, surface.NoOpticalImagery) else "Sentinel-1"
        )
        return {
            "schema_version": "1.0",
            "region": meta,
            "period": {
                "post": {"start": payload.post_start, "end": payload.post_end}
            },
            "evidence": [],
            "zones": [],
            "unobserved": {
                "reason": "no_usable_imagery",
                "notes": [str(exc)],
            },
            "report": {
                "text": (
                    f"No usable {sensor_name} imagery was available for "
                    f"{meta['name']} in the requested period, so no measurement "
                    "could be made. This is not a finding of 'no flooding' - the "
                    "ground was not observed."
                ),
                "fallback_used": True,
                "generator_model": None,
            },
        }
    except sar.NoBaselineImagery as exc:
        # Not "no data": the post window was seen, and the darkness rule would
        # answer. But the caller asked for change detection, and handing back a
        # different method's answer without saying so is the failure this
        # project is built against. Say what is missing and what would work.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        # An unknown sensor or method, or change detection asked for without
        # what it needs. The caller's mistake, so a 400 that says what to fix.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    tiles = analysis.tile_urls(result)
    result.pop("_internal", None)
    if tiles:
        result["artifacts"] = tiles

    result["request_id"] = request_id

    if payload.generate_report:
        progress.step("Writing and checking the report")
        report, verification = build_report(result, prefer_llm=payload.use_llm)
        result["report"] = report
        result["verification"] = verification

    return cache.put(cache_key, result)


class AskRequest(BaseModel):
    """A plain-language question. No structure required from the caller.

    An area may be supplied alongside the question, which is what the map's
    draw tool sends. The router still reads the question for what to measure
    and when; only WHERE is taken from the shape. That matters for the
    districts GAUL 2015 cannot name at all - "was there flooding here last
    August" over a drawn box works where the same question naming Ladakh
    cannot.
    """

    question: str
    region: str | None = None       # overrides whatever the router infers
    bbox: list[float] | None = None
    point: list[float] | None = None
    radius_km: float | None = None
    polygon: list[list[float]] | None = None
    # An uploaded boundary (geo/boundary_upload.py): GeoJSON Polygon or
    # MultiPolygon plus its `source`. Left out of cache keys while unset.
    boundary: dict | None = None
    scale: int = 200
    use_llm: bool = True
    dry_run: bool = False           # route only, do not run the analysis

    def area_fields(self):
        """The shape fields, for passing straight through to a sub-request."""
        return {
            "bbox": self.bbox,
            "point": self.point,
            "radius_km": self.radius_km,
            "polygon": self.polygon,
            "boundary": self.boundary,
        }

    def has_area(self):
        return any(value is not None for value in self.area_fields().values())


@app.post("/ask")
def ask(payload: AskRequest):
    """Answer a question asked in ordinary language.

    Routing is a separate, checkable step: the model proposes a structured
    query, the proposal is validated, and an invalid one falls back to rules
    rather than being acted on. The decision is returned alongside the result
    so a user can see what was understood.
    """
    progress.step("Understanding the question")
    decision = routing.route(payload.question, prefer_model=payload.use_llm)

    drawn = payload.has_area()

    if drawn:
        # The shape settles WHERE, so a region the router inferred from the
        # question is no longer what gets measured. Recording that here means
        # the routing block in the response cannot imply a district was used
        # when a drawn box actually was.
        decision["region"] = None
        decision["region_source"] = "user_defined_area"
    elif payload.region:
        decision["region"] = payload.region
        decision["region_source"] = "supplied_by_caller"

    if not decision.get("analysis_type"):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Could not tell what to measure from that question.",
                "question": payload.question,
                "understood": routing.describe(decision),
                "try": [
                    "Show flooding in Kendrapara in August 2018",
                    "Vegetation health in Punjab during kharif 2023",
                    "How much has Bangalore grown since 2019",
                ],
            },
        )

    if not drawn and not decision.get("region"):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "No Indian region was named in that question.",
                "question": payload.question,
                "understood": routing.describe(decision),
                "hint": (
                    "Name a state or district, for example 'in Kerala' - or "
                    "draw an area on the map, which also works for districts "
                    "created after 2015."
                ),
            },
        )

    # Does the route answer the question that was asked?
    #
    # Separate from verify_report, which reads the finished prose against the
    # evidence record and is structurally unable to notice that the evidence
    # record measured the wrong thing. May-vs-May passed 16 of 16 claims there.
    # This runs BEFORE the analysis so a mismatch is reported without spending
    # Earth Engine quota on an answer to a question nobody asked.
    alignment_result = alignment.check(payload.question, decision)

    if payload.dry_run:
        return {
            "routing": decision,
            "understood": routing.describe(decision),
            "alignment": alignment_result,
        }

    common = {
        "region": decision["region"],
        **payload.area_fields(),
        "post_start": decision["post_start"],
        "post_end": decision["post_end"],
        "pre_start": decision.get("pre_start"),
        "pre_end": decision.get("pre_end"),
        "scale": payload.scale,
        "use_llm": payload.use_llm,
    }

    if decision["analysis_type"] == "flood_extent":
        result = analyze(FloodRequest(**common, sensor="sentinel-1",
                                      latest=decision.get("latest") or None))
    else:
        result = analyze_surface(
            SurfaceRequest(**common, analysis_type=decision["analysis_type"])
        )

    # The question and its alignment check are stored as their own record,
    # not written into the analysis's cache entry.
    #
    # They have to be stored somewhere: the PDF is rendered from the cache,
    # and without them a PDF of the May-vs-May answer would print a green
    # "Verified" banner - every number faithful, the question never checked.
    #
    # They cannot go in the analysis entry, because that entry is keyed on
    # the analysis parameters, and two differently worded questions can route
    # to the same analysis. Whichever was asked last would overwrite the
    # other's alignment, and a PDF could print one question's verdict under
    # another question's text.
    ask_payload = {
        "kind": "ask",
        "question": payload.question,
        "request_id": result.get("request_id"),
    }
    ask_id = cache.key_for(ask_payload)
    cache.put(ask_payload, {
        "question": payload.question,
        "routing": decision,
        "alignment": alignment_result,
        "understood": routing.describe(decision),
        "request_id": result.get("request_id"),
    })

    result["routing"] = decision
    result["alignment"] = alignment_result
    result["understood"] = routing.describe(decision)
    result["question"] = payload.question
    result["ask_id"] = ask_id
    return result


@app.post("/route")
def route_only(payload: AskRequest):
    """Routing without running anything. Cheap, and used by the benchmark."""
    decision = routing.route(payload.question, prefer_model=payload.use_llm)
    return {"routing": decision, "understood": routing.describe(decision)}


class SurfaceRequest(BaseModel):
    """Any non-flood surface analysis: vegetation, water, built-up, bare.

    Takes a name or a drawn shape, same as FloodRequest.
    """

    region: str | None = None
    bbox: list[float] | None = None
    point: list[float] | None = None
    radius_km: float | None = None
    polygon: list[list[float]] | None = None
    # An uploaded boundary (geo/boundary_upload.py): GeoJSON Polygon or
    # MultiPolygon plus its `source`. Left out of cache keys while unset.
    boundary: dict | None = None

    analysis_type: str
    post_start: str
    post_end: str
    pre_start: str | None = None
    pre_end: str | None = None
    cloud_limit: int = 40
    scale: int = 100
    generate_report: bool = True
    use_llm: bool = True


def resolve_area_or_fail(payload):
    """Geometry for a request, whether it named a place or drew one.

    A drawn shape wins when both are present, because a caller who dragged a
    box on the map meant the box - but only after saying so, since silently
    preferring one over the other is how you end up measuring something the
    user did not ask for.
    """
    try:
        geometry, meta = footprint.build(
            bbox=payload.bbox,
            point=payload.point,
            radius_km=payload.radius_km,
            polygon=payload.polygon,
            boundary=getattr(payload, "boundary", None),
        )
    except footprint.InvalidFootprint as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_footprint", "message": str(exc)},
        ) from exc

    if geometry is not None:
        if payload.region:
            meta["note"] += (
                f" A region name ({payload.region!r}) was also supplied and "
                "ignored; the drawn area was measured."
            )
        return geometry, meta

    if not payload.region:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "no_area",
                "message": (
                    "Supply a region name, or an area as bbox, "
                    "point + radius_km, or polygon."
                ),
            },
        )

    return resolve_region_or_fail(payload.region)


def resolve_region_or_fail(name):
    """Geometry for a region, or an HTTP error that explains itself.

    Three distinct outcomes, kept distinct on purpose. A name that is not in
    the dataset is a 404 that names the vintage, because the usual cause is a
    district created after 2015 rather than a typo. A name that matches several
    places is a 409 listing them - picking one would return another district's
    statistics under the requested district's name, and nothing downstream
    could tell.
    """
    try:
        geometry, meta = analysis.resolve_geometry(name)
    except regions.RegionAmbiguous as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "ambiguous_region", "message": str(exc),
                    "matches": exc.matches},
        ) from exc

    if geometry is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "region_not_found",
                "message": str(regions.RegionNotFound(name)),
                "boundary_source": regions.BOUNDARY_SOURCE,
                "boundary_vintage": regions.BOUNDARY_VINTAGE,
            },
        )

    return geometry, meta


@app.get("/analyses")
def list_analyses():
    """What the platform can measure, and whether the thresholds are validated."""
    return {
        "flood": {
            "type": "flood_extent",
            "label": "Flood extent",
            "domain": "disaster",
            "sensor": "sentinel-1",
            "endpoint": "/analyze",
            "validated": True,
            # Read from sar.py rather than restated. A second copy of these
            # numbers went stale once already, and this endpoint is what the
            # frontend shows users as our accuracy claim. Reported at 200 m,
            # the scale the frontend runs at, with every measured scale beside it.
            "validation": sar.rule_for_scale(200)["validation"],
            "validation_by_scale": {
                f"{m} m": {"threshold_db": r["db"], **r["validation"]}
                for m, r in sar.SCALE_RULES.items()
            },
            # Every sensor and method the form can offer, each with the
            # accuracy measured for it - from the detectors' own constants.
            "methods": analysis.method_options(200),
            # The measured terrain check, or None until notebook 09 ships one.
            "terrain": ({**sar.TERRAIN_RULE, "text": sar.terrain_text(sar.TERRAIN_RULE)}
                        if sar.TERRAIN_RULE else None),
        },
        "surface": surface.catalogue(),
    }


class SeriesRequest(BaseModel):
    """Flood extent month by month, for a named region or a drawn area."""

    region: str | None = None
    bbox: list[float] | None = None
    point: list[float] | None = None
    radius_km: float | None = None
    polygon: list[list[float]] | None = None
    # An uploaded boundary (geo/boundary_upload.py): GeoJSON Polygon or
    # MultiPolygon plus its `source`. Left out of cache keys while unset.
    boundary: dict | None = None

    start: str
    end: str
    # Required to be a sensor, not None. Letting cloud cover pick per month
    # could measure June with radar and July with optical, and the jump
    # between them would be the instrument changing.
    sensor: str = "sentinel-1"
    scale: int = 200


@app.post("/analyze/series")
def analyze_series(payload: SeriesRequest):
    """One flood analysis per calendar month, arranged and checked as a series.

    Each month runs through /analyze itself, so each is a complete analysis
    with its own evidence record and request_id, cached independently: a
    series that overlaps an earlier one reuses its months, and any month can
    be opened or exported as a PDF on its own.

    Reports are not generated per month. Twelve language-model calls would
    hit the rate limit and add prose nobody reads in a chart; the evidence
    record for each month is complete without one.
    """
    if payload.sensor not in analysis.SENSORS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"A series needs one named sensor ({', '.join(analysis.SENSORS)}), "
                "so every month is measured by the same instrument."
            ),
        )

    try:
        windows = timeseries.monthly_windows(payload.start, payload.end)
    except timeseries.InvalidSeries as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    area = {
        "region": payload.region,
        "bbox": payload.bbox,
        "point": payload.point,
        "radius_km": payload.radius_km,
        "polygon": payload.polygon,
        "boundary": payload.boundary,
    }

    results = []
    for window in windows:
        results.append(analyze(FloodRequest(
            **area,
            post_start=window["start"],
            post_end=window["end"],
            sensor=payload.sensor,
            scale=payload.scale,
            generate_report=False,
            use_llm=False,
        )))

    return timeseries.build(windows, results, sensor=payload.sensor)


@app.post("/analyze/surface")
def analyze_surface(payload: SurfaceRequest):
    """Vegetation, water, built-up, bare ground and moisture stress.

    Same contract as /analyze. Uses Sentinel-2, so cloud limits what can be
    seen; coverage is measured and reported rather than assumed.
    """
    # A boundary is left out while unset, so adding the field re-keyed nothing.
    cache_key = {"kind": "surface", **{k: v for k, v in payload.model_dump().items()
                                       if not (k == "boundary" and v is None)}}
    from geo import boundaries
    if boundaries.cache_tag() and payload.region:
        cache_key["boundary_set"] = boundaries.cache_tag()
    request_id = cache.key_for(cache_key)

    cached = cache.get(cache_key)
    if cached:
        cached["request_id"] = request_id
        return cached

    if payload.analysis_type not in surface.ANALYSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown analysis '{payload.analysis_type}'. "
                f"Available: {sorted(surface.ANALYSES)}"
            ),
        )

    geometry, meta = resolve_area_or_fail(payload)

    try:
        result = surface.analyse(
            region_geometry=geometry,
            region_meta=meta,
            analysis_type=payload.analysis_type,
            post_start=payload.post_start,
            post_end=payload.post_end,
            pre_start=payload.pre_start,
            pre_end=payload.pre_end,
            cloud_limit=payload.cloud_limit,
            scale=payload.scale,
        )
    except surface.NoOpticalImagery as exc:
        return {
            "schema_version": "1.0",
            "analysis_type": payload.analysis_type,
            "region": meta,
            "period": {"post": {"start": payload.post_start, "end": payload.post_end}},
            "evidence": [],
            "zones": [],
            "unobserved": {
                "reason": "no_usable_imagery",
                "notes": [
                    str(exc),
                    "Raise cloud_limit, widen the window, or use the "
                    "Sentinel-1 flood endpoint, which is unaffected by cloud.",
                ],
            },
            "report": {
                "text": (
                    f"No usable Sentinel-2 imagery was available for "
                    f"{meta['name']} in the requested period, so nothing could "
                    "be measured. This is not a finding of absence - the ground "
                    "was not observed."
                ),
                "fallback_used": True,
                "generator_model": None,
            },
        }

    tiles = surface.tile_urls(result)
    result.pop("_internal", None)
    if tiles:
        result["artifacts"] = tiles

    result["request_id"] = request_id

    if payload.generate_report:
        report, verification = build_report(result, prefer_llm=payload.use_llm)
        result["report"] = report
        result["verification"] = verification

    return cache.put(cache_key, result)


@app.get("/analyze/{request_id}")
def get_analysis(request_id: str):
    """Retrieve a stored analysis by the request_id returned from /analyze."""
    stored = cache.get_by_id(request_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"No analysis {request_id}.")
    stored["request_id"] = request_id
    return stored


def hindi_report(stored, request_id, compute=True):
    """The Hindi translation of a stored result's verified report.

    Cached by the English text it translates, so a re-generated English
    report is never shown with an old translation. Only a VERIFIED English
    report is translated: translating a failed one would carry its errors
    into a language the verifier cannot read.
    """
    import hashlib

    from pipeline import translate
    from pipeline.report import LLMUnavailable

    report = stored.get("report") or {}
    english = report.get("text") or ""
    if not english:
        return {"available": False, "reason": "This result has no report to translate."}
    if not (stored.get("verification") or {}).get("passed"):
        return {"available": False, "reason": (
            "The English report did not pass verification, so it is not translated.")}
    key = {"kind": "translation", "language": "hi", "request_id": request_id,
           "english_sha256": hashlib.sha256(english.encode("utf-8")).hexdigest()}
    cached = cache.get(key, ttl=None)
    if cached:
        cached.pop("_cache", None)
        return cached
    if not compute:
        return None
    try:
        result = translate.to_hindi(english)
    except LLMUnavailable as exc:
        return {"available": False, "reason": f"Translation service unavailable: {exc}"}
    result["source"] = "machine translation of the verified English report"
    if result.get("available"):
        cache.put(key, result)
    return result


@app.get("/analyze/{request_id}/report/hi")
def analysis_report_hindi(request_id: str):
    """The finding in Hindi: a checked translation of the verified English,
    or the reason it is not available. The English stays authoritative."""
    stored = _stored_flood_or_404(request_id)
    return hindi_report(stored, request_id)


@app.get("/analyze/{request_id}/report.pdf")
def analysis_pdf(request: Request, request_id: str, ask_id: str | None = None, lang: str = "en",
                 pictures: bool = True):
    """The stored analysis as a PDF, for the answer that has to leave the app.

    Rendered from the stored response for this request_id and nothing else -
    no Earth Engine call, no model call, no recomputation. The PDF is a view
    of the evidence record, so it cannot disagree with it.

    `ask_id`, returned by /ask, adds the question that was asked and the
    check of whether the analysis answers it. Without it the PDF can only
    vouch for the numbers, not for whether they were the right numbers.
    """
    stored = cache.get_by_id(request_id)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail=f"No analysis {request_id}. Run POST /analyze first.",
        )
    stored["request_id"] = request_id
    # Age at export time, not at analysis time: a PDF made today of an
    # analysis cached last week must say how old the image is today.
    if stored.get("acquisition"):
        stored["acquisition"] = latest_mode.freshness(stored["acquisition"])

    if lang == "hi":
        # The Hindi finding goes beside the English, never instead of it.
        stored["report_hi"] = hindi_report(stored, request_id)

    # Villages and roads, if they were looked up - never fetched here: the PDF
    # renders from what is stored, with no network call.
    places = stored_places(request_id)
    if places:
        stored["places"] = places

    if ask_id:
        asked = cache.get_by_id(ask_id)
        if asked is None:
            raise HTTPException(
                status_code=404,
                detail=f"No question {ask_id}. It may have expired; ask again.",
            )
        # A question record belongs to exactly one analysis. Printing it on
        # another would put one question's verdict under a different answer.
        if asked.get("request_id") != request_id:
            raise HTTPException(
                status_code=400,
                detail=f"Question {ask_id} was not answered by analysis {request_id}.",
            )
        for key in ("question", "routing", "alignment", "understood"):
            if key in asked:
                stored[key] = asked[key]

    # Report pictures: the automatic ones (any not drawn yet are drawn now,
    # within a time budget) and the ones this user captured. A flood result
    # whose map can no longer be rebuilt simply has none.
    if pictures and (stored.get("observation") or {}).get("sensor_used"):
        try:
            from core import pictures as report_pictures
            try:
                rebuilt, layers_for = _picture_layers(request_id)
                stored.setdefault("region", {})["bbox"] = rebuilt["region"].get("bbox")
            except HTTPException:
                layers_for = None
            stored["pictures"], stored["pictures_missing"] = report_pictures.for_report(
                stored, request_id, _owner(request), layers_for)
        except Exception:                        # noqa: BLE001 - pictures never block the PDF
            stored["pictures"] = []

    try:
        from pipeline import export_pdf
        content = export_pdf.render(stored)
    except ImportError as exc:
        # reportlab is the one dependency only this endpoint needs. Saying so
        # beats a 500 with a traceback about a module nobody knew was used.
        raise HTTPException(
            status_code=501,
            detail=f"PDF export needs reportlab: pip install reportlab ({exc}).",
        ) from exc

    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'attachment; filename="{export_pdf.filename_for(stored)}"',
        },
    )


@app.get("/analyze/{request_id}/zones.geojson")
def zones_geojson(request_id: str):
    """Zone centroids as GeoJSON, for direct consumption by Leaflet.

    Keyed on request_id rather than on the original query parameters: guessing
    the parameters to rebuild a cache key breaks the moment any default differs.
    """
    stored = cache.get_by_id(request_id)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail=f"No analysis {request_id}. Run POST /analyze first.",
        )
    return stored.get("zones_geojson") or {
        "type": "FeatureCollection",
        "features": [],
    }


# ------------------------------------------------------------ GIS downloads
#
# The zone and district files are views of the stored result - no Earth
# Engine call, so they cannot disagree with the evidence record. The GeoTIFF
# is rebuilt from the stored request (see /export/flood.tif).

def _stored_flood_or_404(request_id):
    stored = cache.get_by_id(request_id)
    if stored is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis {request_id}. Run POST /analyze first.")
    stored["request_id"] = request_id
    return stored


def _download(content, media_type, filename):
    return Response(content=content, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{filename}"'})


def _places_key(request_id):
    return {"kind": "places", "request_id": request_id}


def stored_places(request_id):
    """The places block computed earlier for a result, or None. No network."""
    places = cache.get(_places_key(request_id), ttl=None)
    if places:
        places.pop("_cache", None)
    return places


@app.get("/analyze/{request_id}/places")
def analysis_places(request_id: str, refresh: bool = False):
    """Villages and roads in the flood zones, from OpenStreetMap (geo/places.py).

    Computed from the stored result's zone outlines - no Earth Engine call -
    and cached beside it. Kept out of the evidence record: a gazetteer is not
    a measurement. A failure is a 200 with {"error": ...}, never a broken
    result: the flood figures do not depend on it.
    """
    from geo import places as places_lookup

    stored = _stored_flood_or_404(request_id)
    if not refresh:
        cached = stored_places(request_id)
        if cached:
            return cached
    try:
        block = places_lookup.for_result(stored)
    except Exception as exc:                     # noqa: BLE001 - see the docstring
        block = {"error": f"Village and road lookup failed: {type(exc).__name__}: {str(exc)[:160]}"}
    if "error" not in block:
        cache.put(_places_key(request_id), block)
    return block


@app.get("/analyze/{request_id}/export/places.csv")
def export_places_csv(request_id: str):
    """Villages and roads per zone, once looked up."""
    stored = _stored_flood_or_404(request_id)
    places = stored_places(request_id)
    if not places:
        raise HTTPException(status_code=404, detail=(
            "Villages and roads have not been looked up for this result yet: "
            f"GET /analyze/{request_id}/places first."))
    return _download(export_gis.places_csv(stored, places), "text/csv",
                     f"{export_gis.file_stem(stored, 'places')}.csv")


@app.get("/analyze/{request_id}/export/zones.geojson")
def export_zones_geojson(request_id: str):
    """Zone outlines with area, severity, rank and people, plus attribution."""
    stored = _stored_flood_or_404(request_id)
    body = json.dumps(export_gis.zones_geojson(stored), indent=1)
    return _download(body, "application/geo+json",
                     f"{export_gis.file_stem(stored, 'zones')}.geojson")


@app.get("/analyze/{request_id}/export/zones.kml")
def export_zones_kml(request_id: str):
    """The zones for Google Earth, coloured by severity."""
    stored = _stored_flood_or_404(request_id)
    return _download(export_gis.zones_kml(stored), "application/vnd.google-earth.kml+xml",
                     f"{export_gis.file_stem(stored, 'zones')}.kml")


@app.get("/analyze/{request_id}/export/zones.csv")
def export_zones_csv(request_id: str):
    stored = _stored_flood_or_404(request_id)
    return _download(export_gis.zones_csv(stored), "text/csv",
                     f"{export_gis.file_stem(stored, 'zones')}.csv")


@app.get("/analyze/{request_id}/export/districts.csv")
def export_districts_csv(request_id: str):
    stored = _stored_flood_or_404(request_id)
    body = export_gis.districts_csv(stored)
    if body is None:
        raise HTTPException(status_code=404, detail=(
            "This result has no district table: districts are broken down for "
            "a state or a drawn area, not for a single district."))
    return _download(body, "text/csv", f"{export_gis.file_stem(stored, 'districts')}.csv")


def _flood_rebuild(request_id):
    """The stored result, its request, the area and the download plan.

    Refuses what cannot be rebuilt faithfully: a result with nothing
    observed, one that is not a flood map, and one computed under an earlier
    detection rule - rebuilding that today would deliver a different map
    from the one its numbers describe.
    """
    stored = _stored_flood_or_404(request_id)
    request = cache.request_by_id(request_id)
    if request is None or "flood_rule" not in request:
        raise HTTPException(status_code=409, detail=(
            "This result's request was not stored with it, so its flood map "
            "cannot be rebuilt. Run the analysis again."))
    if request["flood_rule"] != sar.RULE_VERSION:
        raise HTTPException(status_code=409, detail=(
            f"This result was computed under detection rule {request['flood_rule']}, "
            f"and the current rule is {sar.RULE_VERSION}. Rebuilding it now would give "
            "a different map from the one its numbers describe. Run the analysis again."))
    sensor_used = (stored.get("observation") or {}).get("sensor_used")
    if not sensor_used:
        raise HTTPException(status_code=409, detail=(
            "Nothing was observed in this result, so there is no flood map to download."))

    payload = FloodRequest(**{k: v for k, v in request.items()
                              if k not in ("flood_rule", "terrain_rule")})
    if payload.cache_key().get("terrain_rule") != request.get("terrain_rule"):
        raise HTTPException(status_code=409, detail=(
            "The terrain check has changed since this result was computed, so "
            "rebuilding it now would give a different map from the one its numbers "
            "describe. Run the analysis again."))
    geometry, _ = resolve_area_or_fail(payload)
    plan = export_gis.download_plan(export_gis.region_bounds(geometry), payload.scale)
    return stored, payload, geometry, sensor_used, plan


@app.get("/analyze/{request_id}/export/flood-plan")
def export_flood_plan(request_id: str):
    """The scale the GeoTIFF will be delivered at, before downloading it."""
    _, _, _, _, plan = _flood_rebuild(request_id)
    return plan


@app.get("/analyze/{request_id}/export/flood.zip")
def export_flood_geotiff(request_id: str):
    """The flood map as a GeoTIFF of codes, zipped with its sidecar and README.

    1 flooded, 0 observed and dry, 2 permanent water, 255 not observed.
    Rebuilt from the stored request with the same rule; only the map, none of
    the statistics.
    """
    stored, payload, geometry, sensor_used, plan = _flood_rebuild(request_id)
    if plan.get("scale_m") is None:
        raise HTTPException(status_code=413, detail=plan["note"])
    try:
        layers = analysis.flood_layers(
            geometry, payload.post_start, payload.post_end,
            payload.pre_start, payload.pre_end,
            sensor=sensor_used, scale=payload.scale,
            cloud_limit=(payload.cloud_limit if payload.cloud_limit is not None
                         else analysis.optical.DEFAULT_CLOUD_LIMIT),
            method=payload.method or "threshold",
            terrain_check=payload.terrain_check,
        )
        tif = export_gis.fetch(export_gis.download_url(layers, geometry, plan))
    except (sar.NoSarImagery, surface.NoOpticalImagery, sar.NoBaselineImagery) as exc:
        raise HTTPException(status_code=409, detail=f"The imagery is no longer available: {exc}") from exc
    except (HTTPException, earth_engine.EarthEngineUnavailable):
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=(
            f"Earth Engine could not produce the download: {str(exc)[:200]}")) from exc

    content, filename = export_gis.geotiff_zip(tif, stored, plan)
    return _download(content, "application/zip", filename)


@app.get("/cache")
def cache_stats():
    return cache.stats()


@app.delete("/cache")
def cache_clear():
    return {"cleared": cache.clear()}


@app.post("/query")
def query(payload: QueryRequest):
    return build_analysis_response(
        payload.question,
        payload.mode,
        payload.region,
        payload.start_date,
        payload.end_date,
        payload.cloud_limit,
        payload.composite_method,
    )


@app.post("/boundary/parse")
async def boundary_parse(file: UploadFile = File(...)):
    """Read an uploaded boundary file - GeoJSON, KML/KMZ or a zipped shapefile.

    Returns the file's identity (name, SHA-256) and every shape in it, each
    checked and, if needed, simplified - with the area change that caused.
    A file with one usable shape also returns the ready `boundary` field for
    /analyze; with several, the client picks one through
    GET /boundary/{sha256}/{index}.
    """
    from geo import boundary_upload

    data = await file.read()
    try:
        parsed = boundary_upload.parse(file.filename, data)
    except footprint.InvalidFootprint as exc:
        raise HTTPException(status_code=422, detail={
            "error": "unreadable_boundary", "message": str(exc)}) from exc
    boundary_upload.save(parsed)
    body = boundary_upload.summary(parsed)
    usable = [f for f in parsed["features"] if f["ok"]]
    if len(usable) == 1:
        body["boundary"] = boundary_upload.request_boundary(parsed, usable[0]["index"])
    return body


@app.get("/boundary/{file_sha}/{index}")
def boundary_feature(file_sha: str, index: int):
    """One shape from an uploaded file, as the `boundary` field for /analyze."""
    from geo import boundary_upload

    try:
        parsed = boundary_upload.load(file_sha)
        if parsed is None:
            raise HTTPException(status_code=404, detail=(
                "That boundary file is no longer stored. Upload it again."))
        return boundary_upload.request_boundary(parsed, index)
    except footprint.InvalidFootprint as exc:
        raise HTTPException(status_code=422, detail={
            "error": "unreadable_boundary", "message": str(exc)}) from exc


@app.post("/analyze-upload")
async def analyze_upload(
    image: UploadFile = File(...),
    question: str = Form(""),
    mode: str | None = Form(None),       # accepted for old clients, unused
):
    """Screen an uploaded RGB image for water-coloured pixels.

    Same contract as /analyze: an evidence record, caveats, a verified report
    and a request_id that works with GET /analyze/{id} and the PDF export.
    What differs is what the numbers can mean - a fraction of image pixels,
    never an area of ground - and detection/rgb_upload.py says why.

    The original file is not written to disk. The old path kept every upload,
    and a phone photo's EXIF can carry the GPS position of wherever it was
    taken. Only the overlay is kept, re-encoded without metadata.
    """
    suffix = Path(image.filename or "upload.png").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        # 400, not a 200 with an "error" key: a client checking the status
        # code would otherwise treat the refusal as a result.
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Upload a PNG, JPG, JPEG or WEBP.",
        )

    content = await image.read()
    try:
        return rgb_upload.analyse(content, image.filename or "upload", question)
    except rgb_upload.UnreadableImage as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



# ------------------------------------------------------- health and readiness

@app.get("/health")
def health():
    """Liveness: the process answers. For a load balancer or uptime check."""
    return {"status": "ok", "version": app.version}


@app.get("/ready")
def ready():
    """Readiness: what the service needs, checked without spending quota.

    Earth Engine is started (one cheap call) so a broken credential shows
    here rather than on a user's first analysis.
    """
    checks = {}
    try:
        earth_engine.initialize()
        checks["earth_engine"] = {"ok": True}
    except earth_engine.EarthEngineUnavailable as exc:
        checks["earth_engine"] = {"ok": False, "message": str(exc)}
    checks["groq_api_key"] = {"ok": bool(os.environ.get("GROQ_API_KEY"))}
    from core import store
    db_ok, db_detail = store.ping()
    checks["database"] = {"ok": db_ok, "backend": store.backend_name(), "message": db_detail}
    try:
        checks["accounts"] = {"ok": (not settings.auth_required()) or bool(auth.list_users())}
    except Exception as exc:                     # noqa: BLE001
        checks["accounts"] = {"ok": False, "message": str(exc)[:120]}
    checks["settings"] = {"ok": not settings.problems(), "warnings": settings.problems()}
    ok = all(c["ok"] for k, c in checks.items() if k != "settings")
    return JSONResponse(status_code=200 if ok else 503,
                        content={"status": "ready" if ok else "not_ready", "checks": checks})


# ------------------------------------------------------------- sign-in

class LoginRequest(BaseModel):
    username: str
    password: str


def _auth_error(exc):
    return HTTPException(status_code=exc.status, detail={"error": getattr(exc, "code", "auth"),
                                                         "message": exc.message})


def _address(request):
    return request.client.host if request.client else None


def _base_url(request):
    return str(request.base_url).rstrip("/")


def _signed_in(user, request):
    """The response that starts a session: cookie for the browser, token for scripts."""
    token, expires = auth.issue_token(user)
    response = JSONResponse({"user": user, "token": token, "expires": expires})
    response.set_cookie(
        auth.COOKIE, token, max_age=settings.session_hours() * 3600, httponly=True,
        samesite="strict", path="/",
        secure=request.url.scheme == "https" or os.environ.get("COOKIE_SECURE") == "true")
    return response


@app.post("/auth/login")
def login(payload: LoginRequest, request: Request):
    """Check the password; set the session cookie and return the token.

    The username field also takes the email a self-registered account uses.
    """
    try:
        user = auth.authenticate(payload.username, payload.password, address=_address(request))
    except auth.AuthError as exc:
        security.event("login failed", username=payload.username[:40],
                       address=request.client.host if request.client else None)
        raise _auth_error(exc) from exc
    security.event("login", username=user["username"], role=user["role"])
    return _signed_in(user, request)


class SignupRequest(BaseModel):
    full_name: str
    email: str
    password: str
    organisation: str = ""
    user_type: str


@app.post("/auth/signup")
def signup(payload: SignupRequest, request: Request):
    """Create an account and sign it in. The access comes with the account at
    once (auth.SIGNUP_ROLE); admin can never be chosen here."""
    if not settings.signup_enabled():
        raise HTTPException(status_code=403, detail={
            "error": "signup_closed", "message": "Sign-up is closed. Ask an admin for an account."})
    address = request.client.host if request.client else "?"
    allowed, retry = security.SIGNUP_LIMITER.allow(address, settings.signups_per_hour())
    if not allowed:
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry)}, detail={
            "error": "rate_limited",
            "message": f"Too many new accounts from this network. Try again in {retry // 60 + 1} minutes."})
    try:
        user = auth.register(payload.full_name, payload.email, payload.password,
                             payload.organisation, payload.user_type)
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc
    security.event("signup", username=user["username"], user_type=user["user_type"],
                   role=user["role"], address=address)
    auth.record_event(user["username"], "signup", True, address)
    if not user["verified"]:
        # Email verification is on: no session until the link is clicked.
        url = mailer.link(_base_url(request), "verify", auth.verification_link_token(user["username"]))
        mailer.send_verification(user, url)
        return JSONResponse({"user": None, "verification_sent": True, "email": user["email"]})
    return _signed_in(user, request)


class TokenRequest(BaseModel):
    token: str


class EmailRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    password: str


class PasswordChange(BaseModel):
    current: str
    new: str


@app.get("/auth/password-rules")
def password_rules():
    """The rules the forms show as a live checklist."""
    from core import passwords
    return {"min_length": passwords.MIN_LENGTH,
            "rules": [{"key": k, "label": label} for k, label in passwords.RULES]}


@app.post("/auth/verify")
def verify_email(payload: TokenRequest, request: Request):
    """The link from the confirmation email: confirms the address and signs in."""
    try:
        user = auth.verify_email(payload.token)
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc
    return _signed_in(user, request)


def _limited(request, what):
    allowed, retry = security.SIGNUP_LIMITER.allow(f"{what}:{_address(request)}",
                                                   settings.signups_per_hour())
    if not allowed:
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry)}, detail={
            "error": "rate_limited", "message": f"Too many requests. Try again in {retry // 60 + 1} minutes."})


@app.post("/auth/resend")
def resend_verification(payload: EmailRequest, request: Request):
    """Send the confirmation email again. Says the same thing for any address."""
    _limited(request, "resend")
    user = auth.get_user((payload.email or "").strip().lower())
    if user and not user["verified"]:
        url = mailer.link(_base_url(request), "verify", auth.verification_link_token(user["username"]))
        mailer.send_verification(user, url)
    return {"sent": True, "message": "If that account is waiting for confirmation, a new link is on its way."}


@app.post("/auth/forgot")
def forgot_password(payload: EmailRequest, request: Request):
    """Email a reset link. The answer is the same whether or not the account
    exists, so this cannot be used to find out who has an account."""
    _limited(request, "forgot")
    doc, token = auth.reset_link_token(payload.email)
    if doc is not None:
        user = auth.get_user(doc["_id"])
        address = user.get("email") or (user["username"] if "@" in user["username"] else None)
        url = mailer.link(_base_url(request), "reset", token)
        if address:
            mailer.send_reset({**user, "email": address}, url)
        else:
            security.event("password reset link (no email on account)", username=user["username"], link=url)
        auth.record_event(user["username"], "reset_requested", True, _address(request))
    return {"sent": True, "message": "If an account uses that email, a reset link is on its way. "
                                     "It works for 30 minutes."}


@app.post("/auth/reset")
def reset_password(payload: ResetRequest, request: Request):
    """Set a new password from an emailed link; every old session ends."""
    try:
        user = auth.reset_password(payload.token, payload.password)
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc
    return _signed_in(user, request)


@app.post("/auth/password")
def change_password(payload: PasswordChange, request: Request):
    """Change your own password. Other sessions end; this one continues."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "login_required", "message": "Please sign in."})
    try:
        user = auth.change_password(user["username"], payload.current, payload.new)
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc
    return _signed_in(user, request)


@app.post("/auth/logout-all")
def logout_everywhere(request: Request):
    """End every session of this account, this one included."""
    user = getattr(request.state, "user", None)
    if user is not None:
        auth.sign_out_everywhere(user["username"])
    response = JSONResponse({"signed_out": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


@app.get("/auth/activity")
def my_activity(request: Request):
    """Your recent sign-ins and account changes."""
    user = getattr(request.state, "user", None)
    return {"events": auth.events(user["username"] if user else None, limit=30)}


@app.post("/auth/logout")
def logout():
    response = JSONResponse({"signed_out": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


@app.get("/auth/me")
def me(request: Request):
    """Who is signed in (None if nobody), whether sign-in is required, and
    what the sign-up page offers."""
    return {"user": getattr(request.state, "user", None),
            "auth_required": settings.auth_required(),
            "signup": {"enabled": settings.signup_enabled(),
                       "verification": settings.email_verification_required(),
                       "email": settings.smtp() is not None,
                       "user_types": [{"key": k, "label": v} for k, v in auth.USER_TYPES.items()]}}


# ---------------------------------------------------------- user admin

class NewUser(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class UserChange(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = None


def _acting(request):
    user = getattr(request.state, "user", None)
    return user["username"] if user else None


@app.get("/admin/users")
def admin_list_users():
    return {"users": auth.list_users(), "roles": list(auth.ROLES)}


@app.post("/admin/users")
def admin_create_user(payload: NewUser, request: Request):
    try:
        user = auth.create_user(payload.username, payload.password, payload.role)
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc
    security.event("user created", username=user["username"], role=user["role"],
                   by=_acting(request))
    return user


@app.patch("/admin/users/{username}")
def admin_update_user(username: str, payload: UserChange, request: Request):
    try:
        user = auth.update_user(username, role=payload.role, active=payload.active,
                                password=payload.password, acting=_acting(request))
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc
    security.event("user changed", username=username, by=_acting(request),
                   fields=[k for k, v in payload.model_dump().items() if v is not None])
    return user


@app.delete("/admin/users/{username}")
def admin_delete_user(username: str, request: Request):
    try:
        auth.delete_user(username, acting=_acting(request))
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc
    security.event("user deleted", username=username, by=_acting(request))
    return {"deleted": username}


@app.get("/admin/logins")
def admin_logins(limit: int = 100):
    """Recent sign-ins and account events, everyone's."""
    return {"events": auth.events(None, limit=max(1, min(limit, 500)))}


def purge_everything():
    """Everything past its time: recent analyses nobody saved, their pictures,
    spent links, old login records, uploads past retention."""
    from geo import boundary_upload
    from core import pictures as report_pictures
    return {
        "recent_analyses": jobs.purge_expired(),
        "pictures": report_pictures.purge_expired(),
        "links_and_login_records": auth.purge_expired(),
        "boundaries": security.purge_old_files(boundary_upload.STORE, patterns=("*.json",)),
        "overlays": security.purge_old_files(BASE_DIR / "outputs" / "images",
                                             patterns=("upload_*.png",)),
    }


@app.post("/admin/maintenance/purge")
def admin_purge():
    """Delete what is past its retention period, now rather than at the next sweep."""
    return {"removed": purge_everything(), "retention_days": settings.upload_retention_days(),
            "recent_days": settings.recent_days()}


# ------------------------------------------------------------- jobs

def _owner(request):
    user = getattr(request.state, "user", None)
    return user["username"] if user else "anonymous"


@app.post("/jobs/analyze")
def job_analyze(payload: FloodRequest, request: Request):
    """Start a flood analysis in the background; poll GET /jobs/{id}."""
    job_id = jobs.submit("analyze", payload.model_dump(), _owner(request),
                         lambda: analyze(payload))
    return {"job_id": job_id, "status_url": f"/jobs/{job_id}"}


@app.post("/jobs/surface")
def job_surface(payload: SurfaceRequest, request: Request):
    """A vegetation / water / built-up / bare-ground analysis in the background."""
    job_id = jobs.submit("surface", payload.model_dump(), _owner(request),
                         lambda: analyze_surface(payload))
    return {"job_id": job_id, "status_url": f"/jobs/{job_id}"}


@app.post("/jobs/ask")
def job_ask(payload: AskRequest, request: Request):
    job_id = jobs.submit("ask", payload.model_dump(), _owner(request), lambda: ask(payload))
    return {"job_id": job_id, "status_url": f"/jobs/{job_id}"}


@app.post("/jobs/series")
def job_series(payload: SeriesRequest, request: Request):
    job_id = jobs.submit("series", payload.model_dump(), _owner(request),
                         lambda: analyze_series(payload))
    return {"job_id": job_id, "status_url": f"/jobs/{job_id}"}


@app.get("/jobs")
def job_list(request: Request):
    return {"jobs": jobs.recent(getattr(request.state, "user", None))}


# ------------------------------------------------------------- satellites

@app.get("/satellites")
def satellites():
    """Where each active Sentinel-1 is (orbital elements for the browser to
    propagate), ESA's next planned images over India, and the newest image of
    India already in Earth Engine. Public: the landing page shows it. Every
    source is cached, so this spends no quota per visit."""
    from orbits import service as satellite_service
    return satellite_service.summary()


@app.get("/satellites/area")
def satellites_area(region: str):
    """The same for one state or district: its outline, ESA's planned images
    over it, and its newest image in Earth Engine."""
    from orbits import service as satellite_service
    name = (region or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail={"error": "region_required",
                                                     "message": "Name a state or district."})
    try:
        found = satellite_service.area(name)
    except regions.RegionAmbiguous as exc:
        raise HTTPException(status_code=409, detail={"error": "ambiguous_region",
                                                     "message": str(exc), "matches": exc.matches}) from exc
    if found is None:
        raise HTTPException(status_code=404, detail={"error": "region_not_found",
                                                     "message": str(regions.RegionNotFound(name))})
    return found


@app.get("/history")
def history(request: Request, limit: int = 50, saved: bool = False):
    """Your finished analyses, newest first, one short row each, and how much
    of this hour's analysis allowance you have used. Unsaved ones are kept
    for settings.recent_days(); `saved=true` lists only the saved ones."""
    user = getattr(request.state, "user", None)
    key = user["username"] if user else (request.client.host if request.client else "?")
    return {"items": jobs.history(user, max(1, min(limit, 200)), saved_only=saved),
            "usage": {"used": security.ANALYSIS_LIMITER.used(key),
                      "limit": settings.analyses_per_hour()},
            "recent_days": settings.recent_days()}


class SaveRequest(BaseModel):
    title: str | None = None
    note: str | None = None


def _history_or_404(row, job_id):
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "not_found",
                                                     "message": f"No analysis {job_id} of yours."})
    return row


@app.post("/history/{job_id}/save")
def history_save(job_id: str, payload: SaveRequest, request: Request):
    """Keep an analysis for good (it stops expiring), with a title and note."""
    try:
        row = jobs.save(job_id, getattr(request.state, "user", None), payload.title, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "not_finished", "message": str(exc)}) from exc
    return _history_or_404(row, job_id)


@app.post("/history/{job_id}/unsave")
def history_unsave(job_id: str, request: Request):
    """Back to recent: it will be deleted after settings.recent_days()."""
    return _history_or_404(jobs.unsave(job_id, getattr(request.state, "user", None)), job_id)


@app.patch("/history/{job_id}")
def history_edit(job_id: str, payload: SaveRequest, request: Request):
    return _history_or_404(jobs.edit(job_id, getattr(request.state, "user", None),
                                     payload.title, payload.note), job_id)


@app.delete("/history/{job_id}")
def history_delete(job_id: str, request: Request):
    if not jobs.delete(job_id, getattr(request.state, "user", None)):
        raise HTTPException(status_code=404, detail={"error": "not_found",
                                                     "message": f"No analysis {job_id} of yours."})
    return {"deleted": job_id}


# ------------------------------------------------------------- report pictures

def _picture_layers(request_id):
    """(stored result, layers_for(period), whole-area bbox) for a flood result.

    layers_for('post') rebuilds the flood period's layers, ('pre') the
    comparison period's - with the same rule, as the GeoTIFF download does.
    """
    stored, payload, geometry, sensor_used, _plan = _flood_rebuild(request_id)
    cloud = (payload.cloud_limit if payload.cloud_limit is not None
             else analysis.optical.DEFAULT_CLOUD_LIMIT)
    built = {}

    def layers_for(period):
        if period not in built:
            start, end = ((payload.pre_start, payload.pre_end) if period == "pre"
                          else (payload.post_start, payload.post_end))
            if not start:
                raise LookupError("This analysis has no comparison period.")
            built[period] = analysis.flood_layers(
                geometry, start, end,
                payload.pre_start if period == "post" else None,
                payload.pre_end if period == "post" else None,
                sensor=sensor_used, scale=payload.scale, cloud_limit=cloud,
                method=(payload.method or "threshold") if period == "post" else "threshold",
                terrain_check=payload.terrain_check)
        return built[period]

    whole = export_gis.region_bounds(geometry)
    stored.setdefault("region", {})
    if not stored["region"].get("bbox"):
        stored["region"]["bbox"] = list(whole)
    return stored, layers_for


def _picture_error(exc):
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail={"error": "no_picture", "message": str(exc)})
    if isinstance(exc, earth_engine.EarthEngineUnavailable):
        return exc
    return HTTPException(status_code=502, detail={
        "error": "picture_failed", "message": f"Earth Engine could not draw the picture: {str(exc)[:160]}"})


@app.get("/analyze/{request_id}/pictures")
def analysis_pictures(request_id: str, request: Request):
    """The report pictures of this result: automatic ones (drawn on first
    view) and the ones you captured."""
    from core import pictures as report_pictures
    stored = _stored_flood_or_404(request_id)
    if not stored.get("region", {}).get("bbox"):
        # Overview needs an area; worked out from the zones when not stored.
        outlines, _ = report_pictures.zone_features(stored)
        boxes = [report_pictures.bbox_of(f["geometry"]) for f in outlines]
        boxes = [b for b in boxes if b]
        if boxes:
            stored["region"]["bbox"] = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                                        max(b[2] for b in boxes), max(b[3] for b in boxes)]
    return report_pictures.listing(stored, request_id, _owner(request))


class CaptureRequest(BaseModel):
    bbox: list[float] | None = None
    centre: list[float] | None = None      # [lon, lat]
    radius_km: float | None = None
    caption: str | None = None
    job_id: str | None = None


@app.post("/analyze/{request_id}/pictures")
def capture_picture(request_id: str, payload: CaptureRequest, request: Request):
    """Draw a circle or box on the map; it becomes one of your report pictures."""
    from core import pictures as report_pictures
    if payload.centre and payload.radius_km:
        lon, lat = payload.centre[:2]
        if not (0.2 <= payload.radius_km <= 150):
            raise HTTPException(status_code=422, detail={"error": "bad_shape",
                                                         "message": "Draw a circle between 0.2 and 150 km across."})
        bbox = report_pictures.circle_bbox(lon, lat, payload.radius_km)
    elif payload.bbox and len(payload.bbox) == 4:
        bbox = report_pictures.pad_bbox(payload.bbox, 0.02, 0.01)
    else:
        raise HTTPException(status_code=422, detail={"error": "bad_shape",
                                                     "message": "Draw a circle or a box on the map."})
    try:
        stored, layers_for = _picture_layers(request_id)
        return report_pictures.capture(request_id, stored, layers_for, _owner(request), bbox,
                                       payload.caption, payload.job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "too_many", "message": str(exc)}) from exc
    except Exception as exc:                     # noqa: BLE001 - turned into words
        raise _picture_error(exc) from exc


@app.get("/pictures/{picture_id}.png")
def picture_png(picture_id: str, request: Request):
    """One report picture. An automatic one is drawn now if it has not been yet."""
    from core import pictures as report_pictures
    doc = report_pictures.get_png(picture_id)
    if doc is None and "--" in picture_id:
        request_id, kind = picture_id.split("--", 1)
        try:
            stored, layers_for = _picture_layers(request_id)
            if kind not in dict(report_pictures.auto_kinds(stored)):
                raise LookupError(f"No picture {kind!r} for this result.")
            doc = report_pictures.ensure_auto(request_id, kind, stored, layers_for)
        except Exception as exc:                 # noqa: BLE001
            raise _picture_error(exc) from exc
    if doc is None or doc.get("_blob") is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "No such picture."})
    if doc.get("owner") not in (None, _owner(request)) and (getattr(request.state, "user", None) or {}).get("role") != "admin":
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "No such picture."})
    return Response(content=doc["_blob"], media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


class PictureChange(BaseModel):
    caption: str | None = None
    position: int | None = None


@app.patch("/pictures/{picture_id}")
def picture_edit(picture_id: str, payload: PictureChange, request: Request):
    from core import pictures as report_pictures
    found = report_pictures.update(picture_id, _owner(request), payload.caption, payload.position)
    if found is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "No such picture of yours."})
    return found


@app.delete("/pictures/{picture_id}")
def picture_delete(picture_id: str, request: Request):
    from core import pictures as report_pictures
    if not report_pictures.delete(picture_id, _owner(request)):
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "No such picture of yours."})
    return {"deleted": picture_id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    user = getattr(request.state, "user", None)
    job = jobs.get(job_id, user if settings.auth_required() else None)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id}.")
    return job


# ------------------------------------------------------------- the web app
#
# Mounted last, so every API route above wins. Present only once the
# frontend has been built (npm run build); in development Vite serves it.
if (FRONTEND_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="web")
