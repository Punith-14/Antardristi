"""
Results as GIS files: GeoJSON, KML, CSV and a GeoTIFF of the flood map.

An analyst at a state disaster authority works in QGIS or ArcGIS, and a
district office opens Google Earth. They need the result in their tool, not
a screenshot of ours. Every file carries the request ID, the method and the
data sources, so a map made from it can be traced back to the evidence
record it came from.

    zones GeoJSON   zone outlines, with area, severity, rank and people
    zones KML       the same, coloured by severity, for Google Earth
    zones CSV       the zone table
    districts CSV   the district table
    flood GeoTIFF   the flood map itself, one byte per pixel:

                        1   flooded
                        0   observed and not flooded
                        2   permanent water (excluded from the flood)
                        3   dark, but excluded by the terrain check (only
                            when notebook 09's check is in force)
                      255   not observed (no radar, or cloud)

The GeoTIFF codes are the point of it. A plain flooded/not-flooded raster
would turn every pixel the satellite never saw into "dry" - the exact
failure the evidence record exists to prevent. The codes go into a GDAL
sidecar (.aux.xml, read automatically by QGIS and ArcGIS: 255 as no-data,
the class names, the source) and a README, zipped with the TIFF.

Everything except the GeoTIFF is built from the stored result, with no
Earth Engine call. The GeoTIFF is rebuilt from the stored request (Earth
Engine images cannot be cached and download links expire), and only the
flood map is rebuilt - no areas, people or districts.
"""

import csv
import io
import json
import math
import zipfile
from xml.sax.saxutils import escape

# ------------------------------------------------------------------ codes

CODES = {
    1: "flooded",
    0: "observed, not flooded",
    2: "permanent water (excluded from the flood)",
    3: "dark, but too high above drainage or too steep to be flood (terrain check)",
    255: "not observed (no radar coverage, or cloud)",
}
NODATA = 255

# Earth Engine refuses a direct download above 32 MiB of pixels, or wider or
# taller than 32,768 pixels. One byte per pixel; a margin for the TIFF itself.
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
DOWNLOAD_MARGIN = 0.85
MAX_GRID_SIDE = 32768

# Coarser scales tried, as multiples of the analysis scale, when the area is
# too big to download at the scale it was analysed at.
SCALE_STEPS = (1, 2, 3, 4, 5, 8, 10, 16, 20, 32, 50)


# ------------------------------------------------------------ attribution

def attribution(result):
    """What every exported file says about where it came from."""
    result = result or {}
    extent = next((e for e in result.get("evidence") or []
                   if e.get("quantity") == "flood_extent"), {})
    period = result.get("period") or {}
    post = period.get("post") or {}
    pre = period.get("pre") or {}
    provenance = result.get("provenance") or {}
    return {
        "request_id": result.get("request_id"),
        "region": (result.get("region") or {}).get("name"),
        "period": f"{post.get('start')} to {post.get('end')}" if post else None,
        "baseline": f"{pre.get('start')} to {pre.get('end')}" if pre else None,
        "sensor": (result.get("observation") or {}).get("sensor_used"),
        "method": extent.get("method"),
        "source": extent.get("source"),
        "analysis_scale_m": provenance.get("stats_scale_m"),
        "pipeline_version": provenance.get("pipeline_version"),
        "generated_at": result.get("generated_at"),
        "datasets": [d.get("id") for d in provenance.get("datasets") or []],
        "evidence_url": (f"/analyze/{result['request_id']}"
                         if result.get("request_id") else None),
    }


def file_stem(result, what):
    """'antardrishti_kerala_20180815_ab12cd34_zones' - findable again."""
    region = ((result or {}).get("region") or {}).get("slug") or "area"
    post = ((result or {}).get("period") or {}).get("post") or {}
    start = (post.get("start") or "").replace("-", "")
    rid = ((result or {}).get("request_id") or "")[:8]
    name = "_".join(p for p in ("antardrishti", region, start, rid, what) if p)
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name)


def _people(range_):
    if not range_:
        return None, None
    return range_.get("low"), range_.get("high", range_.get("low"))


# ---------------------------------------------------------------- GeoJSON

def zones_geojson(result):
    """Zone outlines with every zone attribute and the attribution.

    Outlines where a zone has one; its centre point otherwise, so no zone is
    silently missing from the file. The attribution is a top-level member,
    which GeoJSON allows (RFC 7946 section 6.1) and GIS tools ignore safely.
    """
    result = result or {}
    zones = {z.get("id"): z for z in result.get("zones") or []}
    features = (result.get("zones_geojson") or {}).get("features") or []
    outlined = {f["properties"].get("id") for f in features
                if (f.get("properties") or {}).get("kind") == "outline"}
    meta = attribution(result)

    out = []
    for feature in features:
        props = feature.get("properties") or {}
        kind = props.get("kind")
        if kind == "marker" and props.get("id") in outlined:
            continue                       # the outline already carries it
        zone = zones.get(props.get("id"), {})
        low, high = _people(zone.get("population"))
        out.append({
            "type": "Feature",
            "geometry": feature.get("geometry"),
            "properties": {
                "id": props.get("id"),
                "rank": props.get("rank"),
                "area_km2": props.get("area_km2"),
                "severity": props.get("severity"),
                "people_low": low,
                "people_high": high,
                "geometry_kind": "outline" if kind == "outline" else "centre point",
                "request_id": meta["request_id"],
            },
        })
    out.sort(key=lambda f: (f["properties"]["rank"] or 10**6))
    summary = result.get("zones_summary") or {}
    return {
        "type": "FeatureCollection",
        "features": out,
        "antardrishti": {
            **meta,
            "zones_found": summary.get("count", len(zones)),
            "zones_listed": len(zones),
            "note": ("Only the listed zones are included; the rest are counted in "
                     "the flood extent but not outlined.") if summary.get("truncated") else None,
        },
    }


# -------------------------------------------------------------------- KML

# KML colours are aabbggrr.
KML_STYLES = {
    "high": "b31f30d7",       # #d7301f
    "moderate": "b3598dfc",   # #fc8d59
    "low": "b38accfd",        # #fdcc8a
}


def _kml_coords(ring):
    return " ".join(f"{float(x):.6f},{float(y):.6f},0" for x, y, *_ in ring)


def _kml_polygon(rings):
    outer, *holes = rings
    parts = [f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{_kml_coords(outer)}"
             "</coordinates></LinearRing></outerBoundaryIs>"]
    for hole in holes:
        parts.append(f"<innerBoundaryIs><LinearRing><coordinates>{_kml_coords(hole)}"
                     "</coordinates></LinearRing></innerBoundaryIs>")
    parts.append("</Polygon>")
    return "".join(parts)


def _kml_geometry(geometry):
    kind = (geometry or {}).get("type")
    coords = (geometry or {}).get("coordinates")
    if kind == "Polygon":
        return _kml_polygon(coords)
    if kind == "MultiPolygon":
        return "<MultiGeometry>" + "".join(_kml_polygon(p) for p in coords) + "</MultiGeometry>"
    if kind == "Point":
        return f"<Point><coordinates>{float(coords[0]):.6f},{float(coords[1]):.6f},0</coordinates></Point>"
    return None


def zones_kml(result):
    """The zones as KML for Google Earth: coloured by severity, attributes in
    each placemark, attribution in the document description."""
    collection = zones_geojson(result)
    meta = collection["antardrishti"]
    styles = "".join(
        f'<Style id="{name}"><LineStyle><color>ff333333</color><width>1</width></LineStyle>'
        f"<PolyStyle><color>{colour}</color></PolyStyle>"
        f"<IconStyle><color>{colour}</color></IconStyle></Style>"
        for name, colour in KML_STYLES.items()
    )
    description = "\n".join(f"{k}: {v}" for k, v in meta.items()
                            if v not in (None, [], "") and k != "datasets")
    placemarks = []
    for feature in collection["features"]:
        geometry = _kml_geometry(feature["geometry"])
        if geometry is None:
            continue
        p = feature["properties"]
        people = ("" if p["people_low"] is None else
                  f"{p['people_low']:,} to {p['people_high']:,} people" if p["people_low"] != p["people_high"]
                  else f"{p['people_low']:,} people")
        text = f"Rank {p['rank']}, {p['area_km2']} km2, {p['severity']} severity" + (
            f", {people}" if people else "")
        data = "".join(
            f'<Data name="{escape(str(k))}"><value>{escape("" if v is None else str(v))}</value></Data>'
            for k, v in p.items()
        )
        placemarks.append(
            f"<Placemark><name>{escape(str(p['id']))}</name>"
            f"<description>{escape(text)}</description>"
            f"<styleUrl>#{escape(str(p['severity'] or 'low'))}</styleUrl>"
            f"<ExtendedData>{data}</ExtendedData>{geometry}</Placemark>"
        )
    name = f"Flood zones - {meta.get('region') or 'area'}, {meta.get('period') or ''}".strip(", ")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{escape(name)}</name>"
        f"<description>{escape(description)}</description>"
        f"{styles}{''.join(placemarks)}</Document></kml>\n"
    )


# -------------------------------------------------------------------- CSV

def _cell(value):
    """Text that a spreadsheet would run as a formula is quoted as text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return "" if value is None else value


def _csv(header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return buffer.getvalue()


ZONE_COLUMNS = ("rank", "id", "area_km2", "severity", "people_low", "people_high",
                "centre_lat", "centre_lon", "request_id")


def zones_csv(result):
    result = result or {}
    rows = []
    for z in sorted(result.get("zones") or [], key=lambda z: z.get("rank") or 10**6):
        low, high = _people(z.get("population"))
        centre = z.get("centroid") or [None, None]
        rows.append((z.get("rank"), z.get("id"), z.get("area_km2"), z.get("severity"),
                     low, high, centre[1], centre[0], result.get("request_id")))
    return _csv(ZONE_COLUMNS, rows)


DISTRICT_COLUMNS = ("rank", "district", "state", "flooded_km2", "flooded_pct",
                    "observed_pct", "partly_seen", "share_of_area_pct",
                    "people_low", "people_high", "request_id")


def districts_csv(result):
    """None when the result has no district table (a single district, say)."""
    result = result or {}
    districts = result.get("districts") or {}
    if not districts.get("rows"):
        return None
    rows = []
    for r in districts["rows"]:
        low, high = _people(r.get("people"))
        rows.append((r.get("rank"), r.get("name"), r.get("state"), r.get("flooded_km2"),
                     r.get("flooded_pct"), r.get("observed_pct"),
                     "yes" if r.get("low_coverage") else "no", r.get("share_of_area_pct"),
                     low, high, result.get("request_id")))
    return _csv(DISTRICT_COLUMNS, rows)


PLACE_COLUMNS = ("zone", "zone_rank", "kind", "name", "local_name", "type_or_class",
                 "inside_zone", "km_in_zone", "lat", "lon", "osm_id", "request_id")


def places_csv(result, places):
    """One row per village and per road, per zone, with the OSM attribution."""
    rows = []
    for zone in places.get("zones") or []:
        for p in zone["places"]:
            rows.append((zone["zone"], zone["rank"], "place", p["name"], p.get("name_local"),
                         p["type"], "yes" if p["inside"] else f"within {places.get('buffer_m')} m",
                         None, p["lat"], p["lon"], p.get("osm_id"), result.get("request_id")))
        for r in zone["roads"]:
            rows.append((zone["zone"], zone["rank"], "road", r.get("ref") or r.get("name"),
                         r.get("name"), r["class_label"], "crosses zone", r["km"], None, None,
                         None, result.get("request_id")))
    text = _csv(PLACE_COLUMNS, rows)
    return text + f"# {places.get('attribution', '')}; roads cross zones, not 'flooded roads'\n"


# ---------------------------------------------------------------- GeoTIFF

def grid_size(bounds, scale_m):
    """(width, height) in pixels of a lon/lat box, as Earth Engine delivers it.

    `bounds` is (west, south, east, north). The download is in EPSG:4326, and
    there Earth Engine turns a scale in metres into degrees AT THE EQUATOR
    (scale / 111,320 m per degree) for both axes: the pixels are square in
    degrees, so a degree of longitude holds as many pixels as a degree of
    latitude whatever the latitude. The first version shortened longitude by
    cos(latitude) - right for ground distance, wrong for this grid - and
    under-predicted Kerala's width by 1.7% (1385 against the 1409 delivered).
    """
    west, south, east, north = bounds
    per_degree = 111_320 / scale_m
    return (max(1, math.ceil(abs(east - west) * per_degree)),
            max(1, math.ceil(abs(north - south) * per_degree)))


def download_plan(bounds, analysis_scale):
    """The scale the GeoTIFF can be delivered at, and why.

    The analysis scale when it fits Earth Engine's download limit; otherwise
    the first coarser step that does, said in words. The map itself is not
    re-detected at the coarser scale - each delivered pixel is the most common
    class of the analysis-scale pixels inside it (see coded_image).
    """
    budget = MAX_DOWNLOAD_BYTES * DOWNLOAD_MARGIN
    analysis_scale = int(analysis_scale)
    for step in SCALE_STEPS:
        scale = analysis_scale * step
        width, height = grid_size(bounds, scale)
        if width * height <= budget and max(width, height) <= MAX_GRID_SIDE:
            reduced = step > 1
            return {
                "scale_m": scale,
                "analysis_scale_m": analysis_scale,
                "reduced": reduced,
                "width": width,
                "height": height,
                "bounds": [round(v, 6) for v in bounds],
                "estimated_mb": round(width * height / 1024 / 1024, 1),
                "note": (
                    f"Delivered at {scale} m; {analysis_scale} m would exceed Earth "
                    "Engine's download limit. Each pixel is the most common class of "
                    f"the {analysis_scale} m pixels inside it - the same flood map, "
                    "not a new detection at the coarser scale."
                ) if reduced else f"Delivered at the analysis scale, {scale} m.",
            }
    return {
        "scale_m": None, "analysis_scale_m": analysis_scale, "reduced": True,
        "note": "This area is too large to download as one GeoTIFF even at "
                f"{analysis_scale * SCALE_STEPS[-1]} m. Draw a smaller area.",
    }


def coded_image(flood, valid, permanent, constant, terrain_excluded=None):
    """One band of codes (see CODES), from the analysis's own masks.

    `constant(value)` makes a constant image - ee.Image.constant in
    production, a fake in tests. Order matters: start everywhere as "not
    observed", mark what was seen as dry, then permanent water, then flood.
    A pixel only leaves 255 where `valid` says it was seen, so nothing
    unobserved can ever come out as dry.
    """
    seen = valid.unmask(0)
    coded = (
        constant(NODATA)
        .where(seen, 0)
        .where(seen.And(permanent.unmask(0)), 2)
    )
    if terrain_excluded is not None:
        coded = coded.where(seen.And(terrain_excluded.unmask(0)), 3)
    return coded.where(seen.And(flood.unmask(0)), 1).rename("flood_class")


def pam_sidecar(meta, plan):
    """GDAL's .aux.xml: no-data, class names and provenance, read by QGIS/ArcGIS."""
    categories = "".join(
        f"<Category>{escape(CODES.get(i, ''))}</Category>" for i in range(4)
    )
    items = {
        "CODES": "; ".join(f"{k}={v}" for k, v in CODES.items()),
        "REQUEST_ID": meta.get("request_id"),
        "REGION": meta.get("region"),
        "PERIOD": meta.get("period"),
        "BASELINE": meta.get("baseline"),
        "SENSOR": meta.get("sensor"),
        "METHOD": meta.get("method"),
        "SOURCE": meta.get("source"),
        "ANALYSIS_SCALE_M": plan.get("analysis_scale_m"),
        "DELIVERED_SCALE_M": plan.get("scale_m"),
        "SCALE_NOTE": plan.get("note"),
    }
    metadata = "".join(f'<MDI key="{k}">{escape(str(v))}</MDI>'
                       for k, v in items.items() if v not in (None, ""))
    return (
        "<PAMDataset>"
        f"<Metadata>{metadata}</Metadata>"
        '<PAMRasterBand band="1">'
        "<Description>flood_class</Description>"
        f"<NoDataValue>{NODATA}</NoDataValue>"
        f"<CategoryNames>{categories}</CategoryNames>"
        "</PAMRasterBand>"
        "</PAMDataset>\n"
    )


def readme(meta, plan):
    lines = [
        "Antardrishti flood map (GeoTIFF)",
        "",
        "One band, one byte per pixel:",
        *[f"  {code:>3}  {label}" for code, label in CODES.items()],
        "",
        "255 is set as no-data in the .aux.xml sidecar. Do not read it as dry:",
        "the satellite did not see that ground.",
        "",
        "Areas counted from this file differ by a few percent from the evidence",
        "record (measured live for Kerala, August 2018: 683.7 km2 counted from",
        "the file against 663.1 km2 in E1, 3%). The file is cut on a regular",
        "lon/lat pixel grid, so pixels on flood edges fall in or out differently.",
        "Quote the evidence record's figures; use this file for where.",
        "",
        plan.get("note") or "",
        "",
        *[f"{k}: {v}" for k, v in meta.items() if v not in (None, [], "")],
        "",
        "Every figure in the result is in its evidence record at "
        f"GET {meta.get('evidence_url') or '/analyze/<request_id>'}.",
    ]
    return "\n".join(lines) + "\n"


def geotiff_zip(tif_bytes, result, plan):
    """The TIFF, its sidecar and a README, zipped so they travel together."""
    meta = attribution(result)
    stem = file_stem(result, f"flood_{plan.get('scale_m')}m")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{stem}.tif", tif_bytes)
        archive.writestr(f"{stem}.tif.aux.xml", pam_sidecar(meta, plan))
        archive.writestr("README.txt", readme(meta, plan))
        archive.writestr("attribution.json", json.dumps({**meta, "download": plan}, indent=2))
    return buffer.getvalue(), f"{stem}.zip"


# ------------------------------------------------------------ earth engine

def region_bounds(region):
    """(west, south, east, north) of an ee.Geometry. One round trip."""
    ring = region.bounds(maxError=100).coordinates().getInfo()[0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


def download_url(layers, region, plan):
    """A signed Earth Engine download link for the coded flood map."""
    import ee

    coded = coded_image(layers["flood"], layers["valid"], layers["permanent"],
                        ee.Image.constant, layers.get("terrain_excluded")).toByte()
    analysis_scale = plan["analysis_scale_m"]
    # Fix the map at the analysis scale first. Without this Earth Engine
    # would compute the whole detection afresh at the coarser scale, on
    # block-mean backscatter the threshold was not measured for.
    coded = coded.reproject(crs="EPSG:4326", scale=analysis_scale)
    if plan["reduced"]:
        ratio = plan["scale_m"] / analysis_scale
        coded = coded.reduceResolution(ee.Reducer.mode(),
                                       maxPixels=int(math.ceil(ratio) ** 2) + 16)
    return coded.getDownloadURL({
        "name": "flood_class",
        "region": region.bounds(maxError=100),
        "scale": plan["scale_m"],
        "crs": "EPSG:4326",
        "format": "GEO_TIFF",
    })


def fetch(url, timeout=300):
    """The bytes behind a download link. Raises on an HTTP error."""
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()
