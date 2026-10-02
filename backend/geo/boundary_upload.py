"""
An official boundary, uploaded as a file, instead of a name or a drawing.

Why: our named regions are FAO GAUL 2015. Thirty districts created since
then cannot be named, and a district office tracing its own boundary by hand
on a web map gets a rough copy of a line it already has as a file. This reads
that file - GeoJSON, KML or a zipped shapefile - checks it, and turns it into
an area the pipeline measures exactly like any other.

What is checked, before anything reaches Earth Engine:

    coordinates   longitude/latitude, inside India. A file in a projected
                  system (UTM metres) is refused and the message says so -
                  reading 500,000 as a longitude would be absurd, and quietly
                  reprojecting a file whose system we are guessing is worse.
    validity      every outline closes and none crosses itself (shapely).
    size          within the same area limit as a drawn shape.
    detail        an official outline can carry 50,000 vertices. Earth Engine
                  requests have a size limit, so it is simplified until it
                  fits - and the change in area that caused is REPORTED. The
                  analysis then runs on that simplified outline, so the stated
                  area change is how far it can differ from the official line.

Several shapes in one file (a state's districts) are listed by name, and the
user picks one. The parsed file is kept server-side under its SHA-256, so
the pick does not mean uploading it again.

The result never claims the boundary is official: it is labelled as
uploaded by the user, with the file name, its SHA-256 and the shape chosen.
"""

import hashlib
import io
import json
import math
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from core import paths
from geo.footprint import INDIA_BOUNDS, MAX_AREA_KM2, InvalidFootprint

# Parsed files, by SHA-256, so a shape can be picked without uploading again.
# Gitignored: a boundary file is the uploader's data.
STORE = paths.DATA / "boundaries"

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FEATURES = 300
# Vertices sent to Earth Engine for one boundary. Well under its request
# size limit, and plenty for a district outline at 100-200 m analysis scale.
MAX_VERTICES = 3000
# Name fields tried in order when a file's features need a label.
NAME_FIELDS = ("name", "NAME", "Name", "district", "DISTRICT", "District", "dtname",
               "DTNAME", "ADM2_NAME", "ADM1_NAME", "NAME_2", "NAME_1", "st_nm", "STATE")

R_EARTH_KM = 6371.0088


class UnreadableBoundary(InvalidFootprint):
    """The file cannot be read as a boundary. The message says why."""


# ------------------------------------------------------------------ parsing

def sha256(data):
    return hashlib.sha256(data).hexdigest()


def detect_format(filename, data):
    name = (filename or "").lower()
    if name.endswith((".geojson", ".json")):
        return "geojson"
    if name.endswith(".kml"):
        return "kml"
    if name.endswith(".kmz"):
        return "kmz"
    if name.endswith(".zip"):
        return "shapefile"
    if name.endswith(".shp"):
        raise UnreadableBoundary(
            "A shapefile is several files (.shp, .shx, .dbf, .prj). Zip them "
            "together and upload the .zip.")
    head = data[:200].lstrip()
    if head.startswith(b"{"):
        return "geojson"
    if head.startswith(b"<"):
        return "kml"
    if data[:2] == b"PK":
        return "shapefile"
    raise UnreadableBoundary(
        f"{filename!r} is not GeoJSON, KML or a zipped shapefile.")


def _name_of(properties, fallback):
    for field in NAME_FIELDS:
        value = (properties or {}).get(field)
        if value not in (None, ""):
            return str(value)
    return fallback


def _polygonal(geometry):
    """A GeoJSON geometry reduced to Polygon/MultiPolygon, or None."""
    if not geometry:
        return None
    kind = geometry.get("type")
    if kind in ("Polygon", "MultiPolygon"):
        return {"type": kind, "coordinates": geometry["coordinates"]}
    if kind == "GeometryCollection":
        polys = []
        for part in geometry.get("geometries") or []:
            p = _polygonal(part)
            if p and p["type"] == "Polygon":
                polys.append(p["coordinates"])
            elif p:
                polys.extend(p["coordinates"])
        return {"type": "MultiPolygon", "coordinates": polys} if polys else None
    return None


def parse_geojson(data):
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UnreadableBoundary(f"Not valid GeoJSON: {exc}") from exc
    crs = ((doc.get("crs") or {}).get("properties") or {}).get("name", "")
    if crs and not any(t in crs.upper() for t in ("4326", "CRS84", "WGS84", "WGS 84")):
        raise UnreadableBoundary(
            f"This GeoJSON declares the coordinate system {crs!r}. Boundaries must "
            "be in longitude/latitude (WGS 84, EPSG:4326). Re-export it in WGS 84 "
            "from QGIS (Export > Save Features As > CRS EPSG:4326).")
    if doc.get("type") == "FeatureCollection":
        items = [(f.get("geometry"), f.get("properties") or {}) for f in doc.get("features") or []]
    elif doc.get("type") == "Feature":
        items = [(doc.get("geometry"), doc.get("properties") or {})]
    else:
        items = [(doc, {})]
    return [(_polygonal(g), p) for g, p in items]


def _kml_coords(text):
    ring = []
    for token in (text or "").split():
        parts = token.split(",")
        if len(parts) >= 2:
            ring.append([float(parts[0]), float(parts[1])])
    return ring


def parse_kml(data):
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise UnreadableBoundary(f"Not valid KML: {exc}") from exc
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    items = []
    for placemark in root.iter(f"{ns}Placemark"):
        polys = []
        for polygon in placemark.iter(f"{ns}Polygon"):
            outer = polygon.find(f"{ns}outerBoundaryIs/{ns}LinearRing/{ns}coordinates")
            if outer is None:
                continue
            rings = [_kml_coords(outer.text)]
            for inner in polygon.findall(f"{ns}innerBoundaryIs/{ns}LinearRing/{ns}coordinates"):
                rings.append(_kml_coords(inner.text))
            polys.append(rings)
        name = placemark.find(f"{ns}name")
        props = {"name": name.text.strip()} if name is not None and name.text else {}
        if polys:
            geometry = ({"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1
                        else {"type": "MultiPolygon", "coordinates": polys})
            items.append((geometry, props))
    return items


def parse_kmz(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            kml = next((n for n in archive.namelist() if n.lower().endswith(".kml")), None)
            if not kml:
                raise UnreadableBoundary("The KMZ contains no .kml file.")
            return parse_kml(archive.read(kml))
    except zipfile.BadZipFile as exc:
        raise UnreadableBoundary(f"Not a valid KMZ: {exc}") from exc


def parse_shapefile_zip(data):
    import shapefile

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnreadableBoundary(f"Not a valid zip file: {exc}") from exc
    names = archive.namelist()
    shp = next((n for n in names if n.lower().endswith(".shp")), None)
    if not shp:
        raise UnreadableBoundary("The zip contains no .shp file.")
    stem = shp[:-4]

    def member(ext):
        found = next((n for n in names if n.lower() == (stem + ext).lower()), None)
        return io.BytesIO(archive.read(found)) if found else None

    prj = member(".prj")
    if prj is not None:
        wkt = prj.read().decode("utf-8", "replace").upper()
        if wkt.startswith("PROJCS") or "PROJCRS" in wkt[:20]:
            raise UnreadableBoundary(
                "This shapefile is in a projected coordinate system (its .prj starts "
                "with PROJCS - UTM or similar, in metres). Re-save it in WGS 84 "
                "(EPSG:4326) longitude/latitude, then upload again.")
    dbf = member(".dbf")
    reader = shapefile.Reader(shp=member(".shp"), shx=member(".shx"), dbf=dbf)
    items = []
    for index, shape_record in enumerate(reader.iterShapeRecords()):
        geometry = _polygonal(shape_record.shape.__geo_interface__)
        try:
            props = shape_record.record.as_dict() if dbf is not None else {}
        except Exception:                 # noqa: BLE001 - a bad .dbf row loses only its name
            props = {}
        items.append((geometry, props))
    return items


PARSERS = {"geojson": parse_geojson, "kml": parse_kml, "kmz": parse_kmz,
           "shapefile": parse_shapefile_zip}


# --------------------------------------------------------------- geometry

def _rings(geometry):
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    return list(geometry["coordinates"])


def vertex_count(geometry):
    return sum(len(ring) for poly in _rings(geometry) for ring in poly)


def ring_area_km2(ring):
    """Area of a lon/lat ring on a sphere (no projection needed), km2."""
    if len(ring) < 3:
        return 0.0
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(ring, ring[1:] + ring[:1]):
        total += math.radians(lon2 - lon1) * (
            2 + math.sin(math.radians(lat1)) + math.sin(math.radians(lat2)))
    return abs(total) * R_EARTH_KM ** 2 / 2


def area_km2(geometry):
    """Outer rings minus holes."""
    total = 0.0
    for poly in _rings(geometry):
        outer, *holes = poly
        total += ring_area_km2([p[:2] for p in outer]) - sum(
            ring_area_km2([p[:2] for p in h]) for h in holes)
    return total


def bounds(geometry):
    points = [p for poly in _rings(geometry) for ring in poly for p in ring]
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return [min(lons), min(lats), max(lons), max(lats)]


def check_coordinates(geometry):
    """Longitude/latitude inside India, or a refusal that names the cause."""
    west, south, east, north = bounds(geometry)
    if max(abs(west), abs(east)) > 180 or max(abs(south), abs(north)) > 90:
        raise UnreadableBoundary(
            f"Coordinates such as ({west:.0f}, {south:.0f}) are not longitude/"
            "latitude - they look like metres from a projected system such as UTM. "
            "Re-export the boundary in WGS 84 (EPSG:4326).")
    iw, is_, ie, in_ = INDIA_BOUNDS
    if west < iw or east > ie or south < is_ or north > in_:
        if iw <= south <= ie and is_ <= west <= in_:
            hint = " The longitude and latitude appear to be swapped."
        else:
            hint = ""
        raise UnreadableBoundary(
            f"The boundary spans {west:.2f} to {east:.2f} E, {south:.2f} to "
            f"{north:.2f} N, which is outside India.{hint}")


def _shapely(geometry):
    from shapely.geometry import shape
    return shape(geometry)


def check_valid(geometry):
    from shapely.validation import explain_validity

    shp = _shapely(geometry)
    if shp.is_empty:
        raise UnreadableBoundary("The boundary has no area.")
    if not shp.is_valid:
        reason = explain_validity(shp)
        raise UnreadableBoundary(
            f"The boundary outline is not valid ({reason}): an outline that "
            "crosses itself does not enclose a single area. Fix it in QGIS "
            "(Vector > Geometry Tools > Fix Geometries) and upload again.")


def simplify(geometry, max_vertices=MAX_VERTICES):
    """Fewer vertices until it fits, with the area change it cost.

    Tolerance doubles from about 10 m until the outline fits. Returns
    (geometry, info) where info says how many vertices and how much area
    moved - so the result can state it rather than hide it.
    """
    from shapely.geometry import mapping

    before = vertex_count(geometry)
    original_area = area_km2(geometry)
    info = {"original_vertices": before, "vertices": before, "tolerance_deg": 0.0,
            "original_area_km2": round(original_area, 2), "area_km2": round(original_area, 2),
            "area_change_pct": 0.0, "simplified": False}
    if before <= max_vertices:
        return geometry, info

    shp = _shapely(geometry)
    tolerance = 0.0001                      # about 10 m
    while True:
        simpler = shp.simplify(tolerance, preserve_topology=True)
        candidate = json.loads(json.dumps(mapping(simpler)))
        candidate = _polygonal(candidate)
        if candidate and vertex_count(candidate) <= max_vertices:
            break
        tolerance *= 2
        if tolerance > 0.05:                # about 5 km: no longer the same boundary
            raise UnreadableBoundary(
                f"The boundary has {before:,} vertices and cannot be simplified to "
                f"{max_vertices:,} without moving it more than about 5 km. Upload a "
                "smaller area.")
    new_area = area_km2(candidate)
    info.update(vertices=vertex_count(candidate), tolerance_deg=tolerance,
                area_km2=round(new_area, 2), simplified=True,
                area_change_pct=round(100 * (new_area - original_area) / original_area, 3)
                if original_area else 0.0)
    return candidate, info


# ------------------------------------------------------------------- driver

def prepare(geometry):
    """Checked and simplified geometry plus what was done to it."""
    if not geometry:
        raise UnreadableBoundary("This feature has no polygon (points and lines "
                                 "cannot be analysed as an area).")
    geometry = {"type": geometry["type"],
                "coordinates": json.loads(json.dumps(geometry["coordinates"]))}
    # Drop any third (height) coordinate a KML or shapefile carries.
    for poly in _rings(geometry):
        for ring in poly:
            ring[:] = [p[:2] for p in ring]
    check_coordinates(geometry)
    check_valid(geometry)
    geometry, info = simplify(geometry)
    if info["area_km2"] > MAX_AREA_KM2:
        raise UnreadableBoundary(
            f"The boundary covers {info['area_km2']:,.0f} km2, above the "
            f"{MAX_AREA_KM2:,.0f} km2 limit. Upload a smaller area or name the state.")
    info["bbox"] = [round(v, 6) for v in bounds(geometry)]
    return geometry, info


def parse(filename, data):
    """{"file": {...}, "features": [{index, name, ok, error | geometry, info}]}."""
    if len(data) > MAX_FILE_BYTES:
        raise UnreadableBoundary(
            f"The file is {len(data) / 1e6:.1f} MB; the limit is "
            f"{MAX_FILE_BYTES / 1e6:.0f} MB.")
    fmt = detect_format(filename, data)
    items = PARSERS[fmt](data)
    if not items:
        raise UnreadableBoundary("The file contains no shapes.")
    if len(items) > MAX_FEATURES:
        raise UnreadableBoundary(
            f"The file has {len(items)} shapes; the limit is {MAX_FEATURES}. "
            "Export only the districts you need.")

    features = []
    for index, (geometry, props) in enumerate(items):
        name = _name_of(props, f"shape {index + 1}")
        try:
            prepared, info = prepare(geometry)
            features.append({"index": index, "name": name, "ok": True,
                             "geometry": prepared, "info": info})
        except InvalidFootprint as exc:
            features.append({"index": index, "name": name, "ok": False, "error": str(exc)})
    if not any(f["ok"] for f in features):
        raise UnreadableBoundary(
            "No shape in the file can be analysed: " + features[0]["error"])
    return {
        "file": {"name": Path(filename or "boundary").name, "sha256": sha256(data),
                 "format": fmt, "bytes": len(data)},
        "features": features,
    }


def summary(parsed):
    """The parse result without geometries: what the picker list needs."""
    return {
        "file": parsed["file"],
        "features": [{k: v for k, v in f.items() if k != "geometry"} for f in parsed["features"]],
    }


def request_boundary(parsed, index):
    """The `boundary` field of a flood request for one chosen feature."""
    feature = next((f for f in parsed["features"] if f["index"] == index), None)
    if feature is None:
        raise UnreadableBoundary(f"No shape {index} in this file.")
    if not feature["ok"]:
        raise UnreadableBoundary(f"Shape {feature['name']!r} cannot be analysed: {feature['error']}")
    return {
        **feature["geometry"],
        "source": {
            "file": parsed["file"]["name"],
            "sha256": parsed["file"]["sha256"],
            "format": parsed["file"]["format"],
            "feature": feature["name"],
            "feature_index": index,
            **{k: feature["info"][k] for k in ("original_vertices", "vertices",
                                                "original_area_km2", "area_km2",
                                                "area_change_pct", "simplified")},
        },
    }


# -------------------------------------------------------------------- store

def save(parsed):
    STORE.mkdir(parents=True, exist_ok=True)
    path = STORE / f"{parsed['file']['sha256']}.json"
    path.write_text(json.dumps(parsed), encoding="utf-8")
    return path


def load(file_sha):
    if not (isinstance(file_sha, str) and len(file_sha) == 64
            and all(c in "0123456789abcdef" for c in file_sha)):
        raise UnreadableBoundary("Not a boundary file id.")
    path = STORE / f"{file_sha}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
