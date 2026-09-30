"""
Fetch a dry pre-event Sentinel-1 baseline for every Sen1Floods11 chip.

Change detection compares each pixel against its own dry self, and
Sen1Floods11 ships only the flood-date radar. So before notebook 06 can score
the change rule, every hand-labelled chip needs a baseline from the same
place, the same relative orbit and an earlier, drier window. This fetches
them from Earth Engine.

Run from backend/:

    python -m scripts.fetch_pre_event --sen1floods11 C:\\sen1floods11

It is slow - a few seconds per chip, 446 chips - and it is RESUMABLE: chips
whose baseline already exists are skipped, so interrupt it whenever you like
and run it again. Each fetched chip is recorded in manifest.jsonl beside the
output, including the ones that could not be fetched and why.

What it fetches, per chip:

    window   the `--window-days` days ending `--gap-days` before the flood
             image's date. The gap keeps the rising limb of the flood out of
             the baseline; the window gives the median enough scenes to be a
             typical dry state rather than one noisy pass.
    orbit    the relative orbit of the flood-date scene over the chip, found
             by querying Sentinel-1 on that date. A baseline from another
             orbit differs by viewing geometry, and that difference would be
             scored as water.
    pixels   VV and VH in dB, median over the window, on EXACTLY the chip's
             pixel grid (same CRS, same affine transform). The comparison is
             pixel by pixel, so a half-pixel offset would put land and water
             edges in the wrong place in every chip.

Not speckle filtered, deliberately: the S1Hand chips it will be subtracted
from are not speckle filtered either, and the median over several passes
already suppresses most speckle in the baseline.
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

DEFAULT_WINDOW_DAYS = 60
DEFAULT_GAP_DAYS = 15
S1 = "COPERNICUS/S1_GRD"


# ------------------------------------------------------------ pure helpers

def event_of(chip_key):
    """'Sri-Lanka_249079' -> 'sri-lanka'. The event is everything before the
    last underscore, which is how Sen1Floods11 names its chips."""
    return chip_key.rsplit("_", 1)[0].strip().lower()


def event_dates_from_metadata(geojson):
    """{event: flood-image date} from Sen1Floods11_Metadata.geojson.

    Tolerant about key names, because they have differed between releases:
    it looks for a location-like key and an S1 date-like key rather than
    assuming exact spellings. Raises with the keys it DID find if it cannot
    work them out, so a changed format fails loudly instead of fetching the
    wrong dates.
    """
    features = geojson.get("features") or []
    if not features:
        raise ValueError("metadata has no features")

    sample = features[0].get("properties") or {}
    location_key = next(
        (k for k in sample if k.lower() in ("location", "country", "event", "region")),
        None,
    )
    date_key = next(
        (k for k in sample if "s1" in k.lower() and "date" in k.lower()), None
    )
    if not location_key or not date_key:
        raise ValueError(
            "could not find a location key and an S1 date key in the metadata; "
            f"keys present: {sorted(sample)}"
        )

    raw = {}
    for feature in features:
        props = feature.get("properties") or {}
        location = str(props.get(location_key, "")).strip().lower().replace(" ", "-")
        value = props.get(date_key)
        if location and value not in (None, ""):
            raw[location] = value

    parsed = parse_dates(list(raw.values()), key=date_key)
    return {location: parsed[_as_key(value)] for location, value in raw.items()}


def _as_key(value):
    return value if isinstance(value, (int, float)) else str(value).strip()


def parse_dates(values, key="date"):
    """{raw value: date} for a column of dates, whatever format it uses.

    The first version assumed ISO (2018-02-15) and failed on this dataset's
    metadata. Handled here: ISO with or without a time, slashes or dashes in
    either order, month names, and epoch seconds or milliseconds.

    Day-first versus month-first is decided for the WHOLE column, not per
    value: 03/04/2018 is ambiguous on its own, but if another row reads
    25/04/2018 the column is day-first. If nothing in the column settles it,
    this refuses rather than guessing - a guessed month would fetch a
    baseline from the wrong season for every chip, and nothing downstream
    would notice.
    """
    import re
    from datetime import datetime, timezone

    out = {}
    numeric_pairs = {}      # raw -> (a, b, year) for a/b/year forms

    for value in values:
        k = _as_key(value)
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value > 10**11 else value
            out[k] = datetime.fromtimestamp(seconds, tz=timezone.utc).date()
            continue

        text = str(value).strip()

        # Year first: 2018-02-15, 2018/2/15, 2018-02-15T00:00:00, 2018-02-15 00:00
        match = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
        if match:
            y, m, d = map(int, match.groups())
            out[k] = date(y, m, d)
            continue

        # Year last: 15/02/2018 or 02/15/2018 - order settled below.
        match = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", text)
        if match:
            a, b, y = map(int, match.groups())
            numeric_pairs[k] = (a, b, y)
            continue

        # Month names: 15 Feb 2018, Feb 15 2018, February 15, 2018
        for fmt in ("%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y",
                    "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%Y%m%d"):
            try:
                out[k] = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError(
                f"cannot read {text!r} in the metadata's {key!r} column as a date. "
                "Pass the dates in yourself with --dates (see --help)."
            )

    if numeric_pairs:
        day_first = any(a > 12 for a, _, _ in numeric_pairs.values())
        month_first = any(b > 12 for _, b, _ in numeric_pairs.values())
        if day_first and month_first:
            raise ValueError(
                f"the {key!r} column mixes day-first and month-first dates; "
                f"examples: {list(numeric_pairs)[:4]}"
            )
        if not day_first and not month_first:
            raise ValueError(
                f"the {key!r} column is ambiguous - every date could be day-first "
                f"or month-first (e.g. {list(numeric_pairs)[:4]}). Pass the dates "
                "in yourself with --dates rather than risk fetching every baseline "
                "from the wrong month."
            )
        for k, (a, b, y) in numeric_pairs.items():
            out[k] = date(y, b, a) if day_first else date(y, a, b)

    return out


def _coordinates(geometry):
    """Every [lon, lat] pair in a GeoJSON geometry, however deeply nested."""
    stack = [(geometry or {}).get("coordinates")]
    while stack:
        item = stack.pop()
        if not isinstance(item, (list, tuple)) or not item:
            continue
        if isinstance(item[0], (int, float)) and len(item) >= 2:
            yield float(item[0]), float(item[1])
        else:
            stack.extend(item)


def event_footprints(geojson):
    """{event: (west, south, east, north)} from the metadata's geometries."""
    features = geojson.get("features") or []
    if not features:
        return {}
    sample = features[0].get("properties") or {}
    location_key = next(
        (k for k in sample if k.lower() in ("location", "country", "event", "region")),
        None,
    )
    boxes = {}
    for feature in features:
        props = feature.get("properties") or {}
        location = str(props.get(location_key, "")).strip().lower().replace(" ", "-")
        points = list(_coordinates(feature.get("geometry")))
        if location and points:
            lons, lats = zip(*points)
            boxes[location] = (min(lons), min(lats), max(lons), max(lats))
    return boxes


def match_by_location(centres, footprints, candidates):
    """The one candidate event whose footprint contains every chip centre.

    Used only for chips whose event name has no date - 'Mekong' in this
    dataset, whose metadata entry is named after the country instead. The
    name is not trusted to imply the country; where the chips actually are
    decides it. Returns None unless exactly one event contains them all,
    because two matches is a guess and zero is no answer.
    """
    matches = []
    for event in candidates:
        box = footprints.get(event)
        if not box:
            continue
        west, south, east, north = box
        if all(west <= lon <= east and south <= lat <= north for lon, lat in centres):
            matches.append(event)
    return matches[0] if len(matches) == 1 else None


def dates_from_csv(path):
    """{event: date} from a two-column CSV: event,YYYY-MM-DD. The escape hatch
    for when the metadata cannot be read."""
    import csv

    dates = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) < 2 or not row[0].strip() or row[0].strip().lower() == "event":
                continue
            event = row[0].strip().lower().replace(" ", "-")
            dates[event] = parse_dates([row[1].strip()])[row[1].strip()]
    return dates


def pre_window(event_date, window_days=DEFAULT_WINDOW_DAYS, gap_days=DEFAULT_GAP_DAYS):
    """(start, end) ISO dates for the baseline. End is exclusive, as in EE."""
    if isinstance(event_date, str):
        event_date = date.fromisoformat(event_date[:10])
    end = event_date - timedelta(days=gap_days)
    start = end - timedelta(days=window_days)
    return start.isoformat(), end.isoformat()


def ee_grid(transform, width, height, crs):
    """The chip's exact pixel grid, in the shape ee.data.computePixels takes.

    `transform` is a rasterio Affine (a, b, c, d, e, f):
        x = a*col + b*row + c ;  y = d*col + e*row + f
    """
    return {
        "dimensions": {"width": int(width), "height": int(height)},
        "affineTransform": {
            "scaleX": transform.a, "shearX": transform.b, "translateX": transform.c,
            "shearY": transform.d, "scaleY": transform.e, "translateY": transform.f,
        },
        "crsCode": str(crs),
    }


def output_path(out_dir, chip_key):
    return Path(out_dir) / f"{chip_key}_S1Pre.tif"


TRANSIENT = ("too many", "rate", "quota", "429", "503", "timed out", "timeout",
             "deadline", "connection", "temporarily")


def with_retries(call, attempts=5, base_delay=4.0, sleep=time.sleep):
    """call(), retried with backoff on errors that look transient.

    Running several chips at once makes Earth Engine's rate limit likely
    rather than rare, so it has to be survived rather than recorded as a
    failure. Anything that does not look transient fails at once - retrying
    a real error five times only delays the message.
    """
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            text = str(exc).lower()
            if attempt == attempts - 1 or not any(t in text for t in TRANSIENT):
                raise
            sleep(base_delay * (2 ** attempt))


# ------------------------------------------------------------ earth engine

def flood_orbit(ee, bounds_geometry, event_date):
    """Relative orbit of the flood-date Sentinel-1 scene over the chip."""
    day = date.fromisoformat(str(event_date)[:10])
    collection = (
        ee.ImageCollection(S1)
        .filterBounds(bounds_geometry)
        .filterDate(day.isoformat(), (day + timedelta(days=1)).isoformat())
        .filter(ee.Filter.eq("instrumentMode", "IW"))
    )
    orbits = collection.aggregate_array("relativeOrbitNumber_start").getInfo()
    return orbits[0] if orbits else None


def fetch_baseline(ee, bounds_geometry, grid, start, end, orbit):
    """(array[2, H, W] of VV, VH in dB, scene count), or (None, 0)."""
    import numpy as np

    collection = (
        ee.ImageCollection(S1)
        .filterBounds(bounds_geometry)
        .filterDate(start, end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .filter(ee.Filter.eq("relativeOrbitNumber_start", orbit))
        .select(["VV", "VH"])
    )
    count = collection.size().getInfo()
    if count == 0:
        return None, 0

    image = collection.median().unmask(-9999)
    raw = ee.data.computePixels({
        "expression": image,
        "fileFormat": "NUMPY_NDARRAY",
        "grid": grid,
    })
    vv = raw["VV"].astype("float32")
    vh = raw["VH"].astype("float32")
    for band in (vv, vh):
        band[band <= -9998] = np.nan
    return np.stack([vv, vh]), count


def write_tif(path, template_profile, array):
    import rasterio

    profile = dict(template_profile)
    profile.update(count=2, dtype="float32", nodata=float("nan"))
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array)
        dst.set_band_description(1, "VV_dB_pre_median")
        dst.set_band_description(2, "VH_dB_pre_median")


# ------------------------------------------------------------------ driver

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sen1floods11", required=True,
                        help="folder the Sen1Floods11 archive was extracted to")
    parser.add_argument("--out", default=None,
                        help="where to write *_S1Pre.tif (default: <sen1floods11>/S1Pre)")
    parser.add_argument("--metadata", default=None,
                        help="Sen1Floods11_Metadata.geojson (searched for if omitted)")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--gap-days", type=int, default=DEFAULT_GAP_DAYS)
    parser.add_argument("--limit", type=int, default=None, help="stop after N chips")
    parser.add_argument("--workers", type=int, default=8,
                        help="chips fetched at once (default 8). Earth Engine "
                             "accepts concurrent requests; rate limits are retried")
    parser.add_argument("--dates", default=None,
                        help="CSV of event,YYYY-MM-DD to use instead of the metadata "
                             "(e.g. 'Bolivia,2018-02-15'); overrides it entirely")
    parser.add_argument("--show-dates", action="store_true",
                        help="print each event's flood date and baseline window, "
                             "check every chip has one, then stop without fetching")
    args = parser.parse_args(argv)

    import rasterio
    import rasterio.warp

    root = Path(args.sen1floods11)
    out_dir = Path(args.out) if args.out else root / "S1Pre"
    manifest = out_dir / "manifest.jsonl"

    if args.dates:
        dates = dates_from_csv(args.dates)
        source = args.dates
    else:
        metadata_path = Path(args.metadata) if args.metadata else next(
            root.rglob("Sen1Floods11_Metadata.geojson"), None
        )
        if metadata_path is None or not metadata_path.exists():
            sys.exit(
                "Sen1Floods11_Metadata.geojson not found. It ships with the dataset; "
                "pass --metadata if it is somewhere else, or --dates with a CSV."
            )
        geojson = json.loads(metadata_path.read_text(encoding="utf-8"))
        try:
            dates = event_dates_from_metadata(geojson)
        except ValueError as exc:
            sys.exit(f"Could not read event dates from {metadata_path}:\n  {exc}")
        source = metadata_path

    chips = sorted(root.rglob("*_S1Hand.tif"))
    if args.limit:
        chips = chips[: args.limit]

    # Every date printed before anything slow starts. A wrong month here would
    # put every baseline in the wrong season, and nothing later would notice.
    print(f"flood-image dates from {source}:")
    for event in sorted(dates):
        start, end = pre_window(dates[event], args.window_days, args.gap_days)
        n = sum(1 for c in chips if event_of(c.name.replace("_S1Hand.tif", "")) == event)
        print(f"  {event:<14} {dates[event]}   baseline {start} to {end}   {n:>3} chips")
    orphans = sorted({event_of(c.name.replace("_S1Hand.tif", "")) for c in chips} - set(dates))

    # A chip event with no date of its own takes the date of the ONE metadata
    # event whose footprint contains all its chips - decided by location, not
    # by assuming what the name means.
    if orphans and not args.dates:
        footprints = event_footprints(geojson)
        used = {event_of(c.name.replace("_S1Hand.tif", "")) for c in chips}
        spare = [e for e in dates if e not in used]
        for orphan in list(orphans):
            centres = []
            for chip in chips:
                if event_of(chip.name.replace("_S1Hand.tif", "")) != orphan:
                    continue
                with rasterio.open(chip) as src:
                    w, s, e, n = rasterio.warp.transform_bounds(src.crs, "EPSG:4326", *src.bounds)
                centres.append(((w + e) / 2, (s + n) / 2))
            match = match_by_location(centres, footprints, spare)
            if match:
                dates[orphan] = dates[match]
                orphans.remove(orphan)
                start, end = pre_window(dates[orphan], args.window_days, args.gap_days)
                print(f"  {orphan:<14} {dates[orphan]}   baseline {start} to {end}   "
                      f"{len(centres):>3} chips   <- no date of its own; all its chips "
                      f"lie inside the {match} footprint, so {match}'s date is used")

    if orphans:
        print(f"  NO DATE for: {', '.join(orphans)} - those chips will be recorded as failed")

    if args.show_dates:
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{len(chips)} chips; writing to {out_dir}")

    # Imported late so --help works without Earth Engine installed.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core import earth_engine
    earth_engine.initialize()
    import ee

    # Leftovers from an interrupted write. Never counted as done: see below.
    for partial in out_dir.glob("*.part.tif"):
        partial.unlink()

    todo = [c for c in chips
            if not output_path(out_dir, c.name.replace("_S1Hand.tif", "")).exists()]
    skipped = len(chips) - len(todo)
    if skipped:
        print(f"{skipped} already fetched, {len(todo)} to go")

    def fetch_one(chip):
        """Fetch one chip's baseline. Returns its manifest record; never raises."""
        key = chip.name.replace("_S1Hand.tif", "")
        target = output_path(out_dir, key)
        record = {"chip": key}
        try:
            event = event_of(key)
            if event not in dates:
                raise LookupError(f"no date for event {event!r} in the metadata")
            record["event_date"] = dates[event].isoformat()
            start, end = pre_window(dates[event], args.window_days, args.gap_days)
            record["window"] = [start, end]

            with rasterio.open(chip) as src:
                profile = src.profile
                bounds = rasterio.warp.transform_bounds(src.crs, "EPSG:4326", *src.bounds)
                grid = ee_grid(src.transform, src.width, src.height, src.crs)

            region = ee.Geometry.Rectangle(list(bounds), geodesic=False)
            orbit = with_retries(lambda: flood_orbit(ee, region, dates[event]))
            record["relative_orbit"] = orbit
            if orbit is None:
                raise LookupError("no Sentinel-1 scene on the flood date over this chip")

            array, count = with_retries(
                lambda: fetch_baseline(ee, region, grid, start, end, orbit)
            )
            record["scenes"] = count
            if array is None:
                raise LookupError(
                    f"no Sentinel-1 scenes from orbit {orbit} between {start} and {end}"
                )

            # Written under a temporary name and renamed only when complete.
            # A Ctrl+C mid-write used to leave a truncated .tif that the next
            # run saw as "already present" and skipped forever - a corrupt
            # baseline, silently scored.
            partial = target.with_name(target.stem + ".part.tif")
            write_tif(partial, profile, array)
            os.replace(partial, target)
            record["status"] = "ok"
        except Exception as exc:
            record["status"] = "failed"
            record["reason"] = str(exc)[:300]
        return record

    done = failed = 0
    lock = threading.Lock()
    workers = max(1, args.workers)
    if workers > 1:
        print(f"fetching {workers} chips at a time")

    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [executor.submit(fetch_one, chip) for chip in todo]
        for index, future in enumerate(as_completed(futures), 1):
            record = future.result()
            with lock:
                if record["status"] == "ok":
                    done += 1
                else:
                    failed += 1
                with manifest.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
                print(f"[{skipped + index}/{len(chips)}] {record['chip']}: {record['status']}"
                      + (f" - {record.get('reason')}" if record["status"] != "ok" else
                         f" ({record['scenes']} scenes, orbit {record['relative_orbit']})"),
                      flush=True)
    except KeyboardInterrupt:
        print("\nstopping - finished chips are kept; run the same command to resume")
        executor.shutdown(wait=False, cancel_futures=True)
        raise SystemExit(1)
    executor.shutdown(wait=True)

    print(f"\nfetched {done}, already present {skipped}, failed {failed}")
    print("Failures are listed in manifest.jsonl. Notebook 06 scores only chips")
    print("that have a baseline, and reports how many that is.")


if __name__ == "__main__":
    main()
