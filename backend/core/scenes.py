"""
Which satellite images a result came from - each one, by ID.

A researcher checking a flood map, or an analyst who has to defend one,
needs more than "3 Sentinel-1 scenes, 1-31 August". They need the scenes
themselves: which images, taken when, from which orbit and in which
direction, so the same images can be fetched and the result rebuilt.

This is provenance, not evidence. A scene ID is not a measurement, it is
where the measurements came from, so it sits beside the evidence records
rather than among them.

One Earth Engine round trip per window, for everything: the total, the
acquisition time of EVERY scene (the "images taken" days are worked out from
these, so the two can never disagree), and the details of the first
SCENE_CAP scenes. A month over a large state can mean dozens of scenes; the
list is capped, the total never is, and the response says which.

Pure helpers first, so the formatting is tested without Earth Engine.
"""

from datetime import datetime, timezone

import ee

# Scenes listed individually per window. The total is always reported.
SCENE_CAP = 50

# What each sensor's scene carries, in Earth Engine's property names.
PROPERTIES = {
    "sentinel-1": {
        "collection": "COPERNICUS/S1_GRD",
        "fields": {
            "id": "system:index",
            "time": "system:time_start",
            "relative_orbit": "relativeOrbitNumber_start",
            "pass": "orbitProperties_pass",
            "platform": "platform_number",
        },
    },
    "sentinel-2": {
        "collection": "COPERNICUS/S2_SR_HARMONIZED",
        "fields": {
            "id": "system:index",
            "time": "system:time_start",
            "cloud_pct": "CLOUDY_PIXEL_PERCENTAGE",
            "platform": "SPACECRAFT_NAME",
            "tile": "MGRS_TILE",
        },
    },
}


class SceneListError(RuntimeError):
    pass


# ------------------------------------------------------------- pure helpers

def iso_time(ms):
    """Epoch milliseconds -> '2018-08-01T00:52:13Z'."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def platform_name(sensor, value):
    """'A' -> 'Sentinel-1A'. Sentinel-2 already names itself."""
    if value is None:
        return None
    text = str(value)
    if sensor == "sentinel-1" and len(text) == 1:
        return f"Sentinel-1{text.upper()}"
    return text


def rows_from(sensor, columns):
    """Per-scene dicts from parallel property lists, oldest first.

    `columns`: {field: [value per scene]}, as aggregate_array returns them.
    A list shorter than the ids (a property missing on some scene) is padded
    with None rather than shifted - shifting would pin one scene's orbit on
    another.
    """
    ids = columns.get("id") or []
    rows = []
    for i, scene_id in enumerate(ids):
        def at(field):
            values = columns.get(field) or []
            return values[i] if i < len(values) else None

        time = at("time")
        row = {
            "id": scene_id,
            "acquired": iso_time(time) if time is not None else None,
            "date": iso_time(time)[:10] if time is not None else None,
            "platform": platform_name(sensor, at("platform")),
        }
        if sensor == "sentinel-1":
            row["relative_orbit"] = at("relative_orbit")
            row["pass"] = (at("pass") or "").lower() or None
        else:
            cloud = at("cloud_pct")
            row["cloud_pct"] = round(cloud, 1) if cloud is not None else None
            row["tile"] = at("tile")
        rows.append(row)
    rows.sort(key=lambda r: (r["acquired"] or "", r["id"] or ""))
    return rows


def summary(sensor, rows, total, all_times, window=None, cap=SCENE_CAP):
    """The block a result carries for one window."""
    from pipeline.latest import days_from_ms

    collection = PROPERTIES[sensor]["collection"]
    listed = rows[:cap]
    return {
        "sensor": sensor,
        "collection": collection,
        "window": window,
        "total": total,
        "listed": len(listed),
        "truncated": total > len(listed),
        "days": days_from_ms(all_times or []),
        "passes": sorted({r["pass"] for r in listed if r.get("pass")}),
        "scenes": listed,
        "reproduce": reproduce_snippet(collection, [r["id"] for r in listed],
                                       truncated=total > len(listed), window=window),
    }


def reproduce_snippet(collection, ids, truncated=False, window=None):
    """Earth Engine Python that loads exactly these scenes.

    When the list was capped, the snippet loads the listed scenes and says
    how to get the rest - it never claims to be the whole set.
    """
    if not ids:
        return None
    quoted = ",\n    ".join(f'"{i}"' for i in ids)
    lines = [
        "import ee",
        "ee.Initialize()",
        f'scenes = ee.ImageCollection("{collection}").filter(ee.Filter.inList(',
        f'    "system:index", [\n    {quoted},\n]))',
    ]
    if truncated:
        start, end = (window or (None, None))[:2]
        lines.append(
            f"# Only the first {len(ids)} scenes are listed. For all of them, filter "
            f'"{collection}" by the analysed area and {start} to {end} instead.'
        )
    return "\n".join(lines)


def empty(sensor, window=None):
    return summary(sensor, [], 0, [], window)


# ------------------------------------------------------------ earth engine

def _one_call(parts):
    """Every list in one round trip. Tests replace this."""
    return ee.Dictionary(parts).getInfo()


def describe(collection, sensor, window=None, cap=SCENE_CAP):
    """The scene block for a collection, in one Earth Engine call.

    `collection` must be the collection the analysis actually composited -
    after every filter - or the list describes different images from the
    ones that made the map.
    """
    if sensor not in PROPERTIES:
        raise SceneListError(f"no scene properties known for {sensor!r}")
    fields = PROPERTIES[sensor]["fields"]
    limited = collection.limit(cap, "system:time_start")
    parts = {"total": collection.size(),
             "all_times": collection.aggregate_array("system:time_start")}
    for field, prop in fields.items():
        parts[f"col_{field}"] = limited.aggregate_array(prop)

    raw = _one_call(parts)
    columns = {field: raw.get(f"col_{field}") for field in fields}
    rows = rows_from(sensor, columns)
    return summary(sensor, rows, raw.get("total") or 0, raw.get("all_times"), window, cap)


def describe_safely(collection, sensor, window=None):
    """describe(), or {"error": reason}. A missing scene list never costs the result."""
    try:
        return describe(collection, sensor, window)
    except Exception as exc:
        return {"sensor": sensor, "window": window,
                "error": f"scene list could not be read: {str(exc)[:160]}"}
