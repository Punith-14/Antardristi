from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

# Load .env before importing modules that read environment variables.
load_dotenv()

from fastapi import FastAPI
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import analysis
from geo import regions
from core import cache
from geo import footprint
from pipeline import routing
from detection import sar
from detection import surface
from legacy.gee_fetch import NoUsableImagery, analyze_region_water
from pipeline.report import build_report
from legacy.ndwi import detect_water_rgb
from legacy.query_parser import parse_query
from geo.regions import REGIONS, get_region_geometry, resolve_region

from core import paths


BASE_DIR = paths.PROJECT_ROOT
UPLOAD_DIR = BASE_DIR / "data" / "raw" / "uploads"

app = FastAPI(title="Antardrishti Prototype API")

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


def build_upload_response(image_path, filename, question="", mode=None):
    parsed = parse_query(question or f"Analyse uploaded image {filename}", mode)
    output_name = f"upload_{uuid4().hex[:12]}_result.png"
    _, water_percentage, explanation, analysis_metadata = detect_water_rgb(
        image_path,
        output_name=output_name,
    )

    image_name = Path(image_path).name

    return {
        "question": question or "Analyse uploaded image",
        "intent": parsed["intent"],
        "mode": parsed["mode"],
        "analysis": "uploaded-image-screening",
        "region": {
            "slug": "uploaded-image",
            "name": "Uploaded Image",
            "bbox": None,
            "summary": "User-provided image analysis.",
        },
        "water_percentage": water_percentage,
        "explanation": explanation,
        "source_image": f"/data/raw/uploads/{image_name}",
        "output_image": analysis_metadata["output_image_url"],
        "metadata": {
            "source": "User uploaded image",
            "date_range": None,
            "scene_count": 1,
            "cloud_limit": None,
            "method": analysis_metadata["method"],
            "threshold": analysis_metadata["threshold"],
            "severity": analysis_metadata["severity"],
            "index_type": analysis_metadata["index_type"],
            "generated_at": parsed["generated_at"],
            "uploaded_filename": filename,
            "prototype_note": (
                "Uploaded-image analysis uses an RGB heuristic because ordinary "
                "uploads do not contain NIR or SWIR bands."
            ),
        },
        "suggested_next_queries": [
            "Analyse another uploaded flood image",
            "Show flooded areas in Kerala",
            "Analyse water presence in Assam",
        ],
    }


@app.get("/")
def home():
    return {"message": "Antardrishti backend is running"}


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

    post_start: str
    post_end: str
    pre_start: str | None = None
    pre_end: str | None = None
    sensor: str | None = "sentinel-1"
    scale: int = 100
    generate_report: bool = True
    use_llm: bool = True


@app.post("/analyze")
def analyze(payload: FloodRequest):
    """Flood analysis returning the full evidence-bound response.

    Shape is defined by contracts/analysis_response.json. Every quantitative
    claim in `report.text` is checkable against `evidence` via `verification`.
    """
    cache_key = payload.model_dump()
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
        )
    except sar.NoSarImagery as exc:
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
                    f"No Sentinel-1 imagery was available for {meta['name']} in "
                    "the requested period, so no measurement could be made. This "
                    "is not a finding of 'no flooding' - the ground was not "
                    "observed."
                ),
                "fallback_used": True,
                "generator_model": None,
            },
        }
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))

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

    if payload.dry_run:
        return {"routing": decision, "understood": routing.describe(decision)}

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
        result = analyze(FloodRequest(**common, sensor="sentinel-1"))
    else:
        result = analyze_surface(
            SurfaceRequest(**common, analysis_type=decision["analysis_type"])
        )

    result["routing"] = decision
    result["understood"] = routing.describe(decision)
    result["question"] = payload.question
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
            # frontend shows users as our accuracy claim.
            "validation": dict(sar.VALIDATION),
        },
        "surface": surface.catalogue(),
    }


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
    mode: str | None = Form(None),
):
    suffix = Path(image.filename or "upload.png").suffix or ".png"
    if suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return {"error": "Unsupported file type. Please upload PNG, JPG, JPEG, or WEBP."}

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved_name = f"upload_{uuid4().hex[:12]}{suffix.lower()}"
    saved_path = UPLOAD_DIR / saved_name

    content = await image.read()
    saved_path.write_bytes(content)

    return build_upload_response(saved_path, image.filename or saved_name, question, mode)
