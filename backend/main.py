import json
from pathlib import Path
from typing import ClassVar

from dotenv import load_dotenv

# Load .env before importing modules that read environment variables.
load_dotenv()

from fastapi import FastAPI
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import analysis
from core import alignment
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


BASE_DIR = paths.PROJECT_ROOT

app = FastAPI(title="Antardrishti Prototype API")


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/outputs", StaticFiles(directory=BASE_DIR / "outputs"), name="outputs")
app.mount("/data", StaticFiles(directory=BASE_DIR / "data"), name="data")


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


@app.get("/")
def home():
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
    LATE_FIELDS: ClassVar[tuple[str, ...]] = ("cloud_limit", "method", "latest", "terrain_check")

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

    cached = cache.get(cache_key)
    if cached:
        cached["request_id"] = request_id
        return cached

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
    cache_key = {"kind": "surface", **payload.model_dump()}
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


@app.get("/analyze/{request_id}/report.pdf")
def analysis_pdf(request_id: str, ask_id: str | None = None):
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
