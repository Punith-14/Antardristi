"""
Pictures for the report: the analysed map itself, not a decorative photo.

    overview   the whole area: flood water (blue) and permanent water over the
               radar image of the same dates, zone outlines and numbers
    zone-N     a close-up of each of the largest zones
    before /   the same view in the comparison ("before") period and the
    after      flood period, when the analysis had one
    capture    any circle or box the user draws on the map, with a caption

How one is made: Earth Engine draws the raster part (radar background,
permanent water, flood water) as a PNG thumbnail of exactly the box asked
for; Pillow then draws everything that must be legible on paper - zone
outlines coloured by severity and numbered, a legend, a scale bar, a north
arrow, the dates and the Copernicus credit. The blue is the measured flood
mask, rebuilt from the stored request with the same rule (as the GeoTIFF
download is), so a picture cannot show a different flood from the numbers.

Storage (core/store.py, collection `pictures`): the PNG is the blob. Every
picture expires after AUTO_DAYS unless the analysis it belongs to is saved,
in which case it is kept with it.
"""

import io
import math
import secrets
import threading
from datetime import datetime, timezone

from core import settings, store

AUTO_DAYS = 30
MAX_CAPTURES = 6
WIDTH = 1200
TOP_ZONES = 3
SEVERITY_COLOURS = {"high": (215, 48, 31), "moderate": (252, 141, 89), "low": (253, 204, 138)}
FLOOD_RGB = (0, 183, 255)
PERMANENT_RGB = (44, 66, 80)
NAVY = (11, 37, 69)
CREDIT = "Contains modified Copernicus Sentinel data {year} · Antardrishti"

_render_locks = {}
_locks_lock = threading.Lock()


def _col():
    return store.collection("pictures")


# ------------------------------------------------------------ geometry

def pad_bbox(bbox, fraction=0.15, minimum_deg=0.02):
    west, south, east, north = bbox
    dx = max(east - west, minimum_deg)
    dy = max(north - south, minimum_deg)
    cx, cy = (west + east) / 2, (south + north) / 2
    dx, dy = dx * (1 + 2 * fraction), dy * (1 + 2 * fraction)
    return [cx - dx / 2, cy - dy / 2, cx + dx / 2, cy + dy / 2]


def circle_bbox(lon, lat, radius_km):
    dlat = radius_km / 110.57
    dlon = radius_km / (111.32 * max(0.1, math.cos(math.radians(lat))))
    return [lon - dlon, lat - dlat, lon + dlon, lat + dlat]


def dimensions(bbox, width=WIDTH):
    """(width, height) in pixels with square ground pixels, height kept sane."""
    west, south, east, north = bbox
    mid = math.radians((south + north) / 2)
    ground_w = (east - west) * math.cos(mid)
    ground_h = north - south
    height = int(round(width * ground_h / max(ground_w, 1e-9)))
    return width, max(300, min(1500, height))


def to_pixel(lon, lat, bbox, size):
    west, south, east, north = bbox
    w, h = size
    return ((lon - west) / (east - west) * w, (north - lat) / (north - south) * h)


def km_per_pixel(bbox, width):
    west, south, east, north = bbox
    return (east - west) * 111.32 * math.cos(math.radians((south + north) / 2)) / width


def nice_km(target):
    for step in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500):
        if step >= target:
            return step
    return 1000


def _rings(geometry):
    if not geometry:
        return []
    if geometry.get("type") == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry.get("type") == "MultiPolygon":
        return [poly[0] for poly in geometry["coordinates"]]
    return []


def bbox_of(geometry):
    pts = [p for ring in _rings(geometry) for p in ring]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def zone_features(result):
    """(outlines, markers) from a stored result's zones_geojson."""
    features = (result.get("zones_geojson") or {}).get("features") or []
    outlines = [f for f in features if (f.get("properties") or {}).get("kind") == "outline"]
    markers = [f for f in features if (f.get("properties") or {}).get("kind") == "marker"]
    return outlines, markers


def top_zones(result, limit=TOP_ZONES):
    outlines, _ = zone_features(result)
    ranked = sorted(outlines, key=lambda f: -(f["properties"].get("area_km2") or 0))
    return ranked[:limit]


# ------------------------------------------------------------ what a result gets

def auto_kinds(result):
    """[(kind, label)] of the pictures every report of this result gets."""
    if not result or (result.get("observation") or {}).get("sensor_used") is None:
        return []
    kinds = [("overview", "Overview")]
    for f in top_zones(result):
        p = f["properties"]
        kinds.append((f"zone-{p.get('id')}", f"Zone {p.get('rank', p.get('id'))} close-up"))
    if ((result.get("period") or {}).get("pre") or {}).get("start"):
        kinds += [("before", "Before"), ("after", "During the flood")]
    return kinds


def auto_id(request_id, kind):
    return f"{request_id}--{kind}"


def descriptor(doc):
    return {"id": doc["_id"], "kind": doc.get("kind"), "label": doc.get("label"),
            "caption": doc.get("caption"), "position": doc.get("position", 0),
            "request_id": doc.get("request_id"), "job_id": doc.get("job_id"),
            "bbox": doc.get("bbox"), "created_at": doc.get("created_at"),
            "ready": True, "url": f"/pictures/{doc['_id']}.png"}


def listing(result, request_id, owner):
    """Every picture this user's report of this result has, in order."""
    col = _col()
    autos = []
    for kind, label in auto_kinds(result):
        pid = auto_id(request_id, kind)
        found = col.get(pid)
        autos.append(descriptor(found) if found else
                     {"id": pid, "kind": kind, "label": label, "caption": None, "position": 0,
                      "request_id": request_id, "ready": False, "url": f"/pictures/{pid}.png"})
    captures = [descriptor(d) for d in col.find({"request_id": request_id, "kind": "capture",
                                                 "owner": owner}, sort=[("position", 1),
                                                                         ("created_at", 1)])]
    return {"auto": autos, "captures": captures, "max_captures": MAX_CAPTURES}


# ------------------------------------------------------------ drawing

def _font(size):
    from PIL import ImageFont
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def annotate(map_png, bbox, result, title, subtitle, request_id=None, show_zones=True):
    """The finished picture: the Earth Engine map with everything legible
    drawn on top and around it. Returns PNG bytes."""
    from PIL import Image, ImageDraw

    base = Image.open(io.BytesIO(map_png)).convert("RGBA")
    w, h = base.size
    paper = Image.new("RGBA", (w, h), (233, 238, 243, 255))
    paper.alpha_composite(base)
    draw = ImageDraw.Draw(paper)

    if show_zones:
        outlines, markers = zone_features(result)
        for f in outlines:
            colour = SEVERITY_COLOURS.get(f["properties"].get("severity"), (49, 130, 189))
            for ring in _rings(f.get("geometry")):
                pts = [to_pixel(x, y, bbox, (w, h)) for x, y in ring]
                if len(pts) >= 2:
                    draw.line(pts + [pts[0]], fill=colour + (255,), width=3)
        label_font = _font(18)
        for f in markers:
            lon, lat = f["geometry"]["coordinates"][:2]
            x, y = to_pixel(lon, lat, bbox, (w, h))
            if not (0 <= x <= w and 0 <= y <= h):
                continue
            colour = SEVERITY_COLOURS.get(f["properties"].get("severity"), (49, 130, 189))
            r = 13
            draw.ellipse([x - r, y - r, x + r, y + r], fill=colour + (255,), outline=(255, 255, 255, 255), width=2)
            text = str(f["properties"].get("rank", ""))
            tw = draw.textlength(text, font=label_font)
            draw.text((x - tw / 2, y - 11), text, fill=(255, 255, 255, 255), font=label_font)

    # North arrow, top right.
    ax, ay = w - 40, 22
    draw.polygon([(ax, ay), (ax - 11, ay + 30), (ax, ay + 23), (ax + 11, ay + 30)],
                 fill=NAVY + (255,), outline=(255, 255, 255, 255))
    draw.text((ax - 6, ay + 32), "N", fill=NAVY + (255,), font=_font(18))

    # Scale bar, bottom left.
    km = nice_km(km_per_pixel(bbox, w) * w / 5)
    length = km / km_per_pixel(bbox, w)
    sx, sy = 20, h - 26
    draw.rectangle([sx - 8, sy - 26, sx + length + 70, sy + 12], fill=(255, 255, 255, 220))
    draw.rectangle([sx, sy - 5, sx + length, sy + 3], fill=NAVY + (255,))
    draw.text((sx + length + 8, sy - 14), f"{km:g} km", fill=NAVY + (255,), font=_font(17))

    # Frame: title above, legend and credit below.
    head, foot = 64, 92
    out = Image.new("RGB", (w, h + head + foot), (255, 255, 255))
    out.paste(paper.convert("RGB"), (0, head))
    d = ImageDraw.Draw(out)
    d.text((18, 10), title, fill=NAVY, font=_font(26))
    d.text((18, 40), subtitle, fill=(95, 107, 122), font=_font(16))
    y0 = head + h + 12
    x = 18
    small = _font(16)
    for colour, label in ((FLOOD_RGB, "Flood water"), (PERMANENT_RGB, "Permanent water")):
        d.rectangle([x, y0 + 3, x + 18, y0 + 19], fill=colour)
        d.text((x + 26, y0 + 1), label, fill=(31, 41, 55), font=small)
        x += 40 + int(d.textlength(label, font=small))
    if show_zones:
        for name, colour in SEVERITY_COLOURS.items():
            d.rectangle([x, y0 + 3, x + 18, y0 + 19], outline=colour, width=3)
            label = f"{name.capitalize()} severity zone"
            d.text((x + 26, y0 + 1), label, fill=(31, 41, 55), font=small)
            x += 40 + int(d.textlength(label, font=small))
    optical = (result.get("observation") or {}).get("sensor_used") == "sentinel-2"
    background = ("Sentinel-2 true colour" if optical else "Sentinel-1 radar (grey)")
    d.text((18, y0 + 34), f"Background: {background} for the same dates. Blue is the measured flood mask.",
           fill=(95, 107, 122), font=_font(14))
    credit = CREDIT.format(year=datetime.now(timezone.utc).year)
    if request_id:
        credit += f" · request {request_id}"
    d.text((18, y0 + 56), credit, fill=(95, 107, 122), font=_font(14))

    buffer = io.BytesIO()
    out.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# ------------------------------------------------------------ Earth Engine part

def map_png(layers, bbox, size, fetch=None):
    """Earth Engine draws radar background + permanent + flood water as a PNG."""
    import ee
    import urllib.request

    flood = layers["flood"]
    permanent = layers["permanent"]
    composite = layers.get("composite")
    if composite is not None:
        background = composite.visualize(min=-25, max=0, palette=["000000", "ffffff"])
    elif layers.get("true_colour") is not None:
        background = layers["true_colour"].visualize(bands=["B4", "B3", "B2"], min=0, max=3000)
    else:
        background = ee.Image.constant([233, 238, 243]).visualize(min=0, max=255)
    stack = ee.ImageCollection([
        background,
        permanent.selfMask().visualize(palette=["2c4250"]),
        flood.selfMask().visualize(palette=["00b7ff"], opacity=0.85),
    ]).mosaic()
    url = stack.getThumbURL({"region": ee.Geometry.Rectangle(bbox, None, False),
                             "dimensions": f"{size[0]}x{size[1]}", "format": "png",
                             "crs": "EPSG:4326"})
    if fetch:
        return fetch(url)
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


# ------------------------------------------------------------ making one

def _period(p):
    if not p or not p.get("start"):
        return ""
    return f"{p['start']} to {p.get('end') or p['start']}"


def plan(kind, result, bbox=None):
    """(bbox, title, subtitle, period) for a picture kind, from the result."""
    region = (result.get("region") or {}).get("name") or "Area"
    period = result.get("period") or {}
    post, pre = period.get("post"), period.get("pre")
    whole = (result.get("region") or {}).get("bbox")
    if kind == "overview":
        return pad_bbox(whole, 0.03), f"{region}: flood map", _period(post), "post"
    if kind == "after":
        return pad_bbox(whole, 0.03), f"{region}: during the flood", _period(post), "post"
    if kind == "before":
        return pad_bbox(whole, 0.03), f"{region}: before (comparison period)", _period(pre), "pre"
    if kind.startswith("zone-"):
        zid = kind[5:]
        outlines, _ = zone_features(result)
        f = next((o for o in outlines if str(o["properties"].get("id")) == zid), None)
        if f is None:
            raise LookupError(f"No zone {zid} in this result.")
        p = f["properties"]
        sev = p.get("severity") or ""
        return (pad_bbox(bbox_of(f["geometry"]), 0.35),
                f"Zone {p.get('rank', zid)}: {p.get('area_km2', 0):,.1f} km², {sev} severity",
                f"{region} · {_period(post)}", "post")
    if kind == "capture":
        return bbox, f"{region}: captured view", _period(post), "post"
    raise LookupError(f"Unknown picture kind {kind!r}.")


def render(kind, request_id, result, layers_for, bbox=None, fetch=None):
    """PNG bytes and metadata for one picture. `layers_for(period)` returns
    the rebuilt Earth Engine layers for 'post' or 'pre' (see main.py)."""
    area, title, subtitle, period = plan(kind, result, bbox)
    if not area:
        raise LookupError("This result has no area to draw.")
    size = dimensions(area)
    raw = map_png(layers_for(period), area, size, fetch=fetch)
    png = annotate(raw, area, result, title, subtitle, request_id=request_id,
                   show_zones=period == "post")
    return png, {"bbox": [round(v, 5) for v in area], "title": title, "subtitle": subtitle,
                 "km_per_px": round(km_per_pixel(area, size[0]), 4)}


def _lock_for(pid):
    with _locks_lock:
        return _render_locks.setdefault(pid, threading.Lock())


def ensure_auto(request_id, kind, result, layers_for, fetch=None):
    """The stored auto picture, rendering it first if needed (one render per
    picture at a time, however many tabs ask)."""
    pid = auto_id(request_id, kind)
    with _lock_for(pid):
        found = _col().get(pid, blob=True)
        if found and found.get("_blob") is not None:
            return found
        label = dict(auto_kinds(result)).get(kind, kind)
        png, meta = render(kind, request_id, result, layers_for, fetch=fetch)
        _col().delete(pid)
        doc = {"_id": pid, "request_id": request_id, "kind": kind, "label": label, "caption": None,
               "position": 0, "owner": None, "job_id": None, "created_at": store.now_iso(),
               "expire_at": store.iso_in(days=AUTO_DAYS), **meta}
        _col().insert(doc, blob=png)
        doc["_blob"] = png
        return doc


def capture(request_id, result, layers_for, owner, bbox, caption=None, job_id=None, fetch=None):
    """A user-drawn view, stored as one of their report pictures."""
    existing = _col().count({"request_id": request_id, "kind": "capture", "owner": owner})
    if existing >= MAX_CAPTURES:
        raise ValueError(f"A report can have up to {MAX_CAPTURES} captured pictures. Remove one first.")
    png, meta = render("capture", request_id, result, layers_for, bbox=bbox, fetch=fetch)
    keep = job_id and _job_saved(job_id)
    doc = {"_id": secrets.token_hex(10), "request_id": request_id, "kind": "capture",
           "label": "Captured view", "caption": (caption or "").strip()[:200] or None,
           "position": existing, "owner": owner, "job_id": job_id, "created_at": store.now_iso(),
           **meta}
    if not keep:
        doc["expire_at"] = store.iso_in(days=settings.recent_days())
    _col().insert(doc, blob=png)
    return descriptor(doc)


def _job_saved(job_id):
    job = store.collection("jobs").get(job_id)
    return bool(job and job.get("saved"))


def get_png(picture_id):
    doc = _col().get(picture_id, blob=True)
    return doc


def update(picture_id, owner, caption=None, position=None):
    doc = _col().get(picture_id)
    if doc is None or doc.get("owner") != owner:
        return None
    changes = {}
    if caption is not None:
        changes["caption"] = caption.strip()[:200] or None
    if position is not None:
        changes["position"] = int(position)
    if changes:
        _col().update(picture_id, set=changes)
    return descriptor(_col().get(picture_id))


def delete(picture_id, owner):
    doc = _col().get(picture_id)
    if doc is None or doc.get("owner") != owner:
        return False
    return _col().delete(picture_id)


# ------------------------------------------------------------ lifetime

def keep_for(job_id, request_id=None):
    """The analysis was saved: keep its pictures with it."""
    col = _col()
    for doc in col.find({"job_id": job_id}):
        col.update(doc["_id"], unset=["expire_at"])
    if request_id:
        for doc in col.find({"request_id": request_id, "owner": None}):
            col.update(doc["_id"], unset=["expire_at"])


def expire_for(job_id):
    col = _col()
    for doc in col.find({"job_id": job_id}):
        col.update(doc["_id"], set={"expire_at": store.iso_in(days=settings.recent_days())})


def delete_for_job(job_id):
    return _col().delete_many({"job_id": job_id})


def purge_expired():
    return _col().delete_many({"expire_at": {"$lt": store.now_iso()}})


def for_report(result, request_id, owner, layers_for, budget_s=60, fetch=None):
    """[(title, caption, png)] for the PDF: the auto pictures (rendering any
    that are missing, within a time budget) then the user's captures."""
    import time
    from concurrent.futures import ThreadPoolExecutor, wait

    out = []
    started = time.monotonic()
    kinds = auto_kinds(result)
    docs = {}
    missing = []
    for kind, _ in kinds:
        found = _col().get(auto_id(request_id, kind), blob=True)
        if found and found.get("_blob") is not None:
            docs[kind] = found
        else:
            missing.append(kind)
    if missing and layers_for is not None:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(ensure_auto, request_id, k, result, layers_for, fetch): k for k in missing}
            done, _ = wait(futures, timeout=max(1, budget_s - (time.monotonic() - started)))
            for future in done:
                try:
                    docs[futures[future]] = future.result()
                except Exception:                # noqa: BLE001 - a missing picture is said, not fatal
                    pass
    for kind, label in kinds:
        doc = docs.get(kind)
        if doc is not None:
            out.append((doc.get("title") or label, doc.get("subtitle"), doc["_blob"]))
    for doc in _col().find({"request_id": request_id, "kind": "capture", "owner": owner},
                           sort=[("position", 1), ("created_at", 1)], blob=True):
        out.append((doc.get("title") or "Captured view", doc.get("caption"), doc["_blob"]))
    missing_after = [label for kind, label in kinds if kind not in docs]
    return out, missing_after
