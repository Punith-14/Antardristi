"""
Fetch terrain - height above drainage and slope - for every Sen1Floods11 chip.

Notebook 09 asks whether a terrain check removes false water: dark radar
pixels on hill slopes (radar shadow) or high above any river, where a flood
cannot physically sit. Scoring that needs, for each hand-labelled chip, the
terrain on exactly the chip's pixel grid. This fetches it from Earth Engine.

Run from backend/:

    python -m scripts.fetch_terrain --sen1floods11 C:\\sen1floods11

RESUMABLE like fetch_pre_event.py: chips already fetched are skipped, files
are written under a temporary name and renamed when complete, and every chip
is recorded in manifest.jsonl - including failures and why. No dates are
involved, so it is much faster than the baseline fetch.

Bands written to <sen1floods11>/Terrain/<chip>_Terrain.tif (float32):

    1  hand_merit   height above nearest drainage, metres - MERIT Hydro `hnd`
                    (~90 m). The catalogue dataset; what the service would use.
    2  hand_30m     the same from GlobalHAND 30 m (Donchyts et al., stream
                    head threshold 1000 cells) - a community asset, fetched for
                    comparison. NaN everywhere if it cannot be read.
    3  slope_deg    slope in degrees from the Copernicus GLO-30 elevation model.

Resampled onto the chip's own 10 m grid (same CRS, same affine transform),
bilinearly: HAND and slope are continuous surfaces, and nearest-neighbour
from 90 m would print 9 x 9 blocks onto every chip.
"""

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scripts.fetch_pre_event import ee_grid, with_retries  # noqa: E402

MERIT_HYDRO = "MERIT/Hydro/v1_0_1"
GLOBAL_HAND_30M = "users/gena/GlobalHAND/30m/hand-1000"
COPERNICUS_DEM = "COPERNICUS/DEM/GLO30"
BANDS = ("hand_merit", "hand_30m", "slope_deg")
NODATA = -9999


def output_path(out_dir, chip_key):
    return Path(out_dir) / f"{chip_key}_Terrain.tif"


def chip_key(path):
    return Path(path).name.replace("_S1Hand.tif", "")


# ------------------------------------------------------------ earth engine

def terrain_image(ee, include_30m=True):
    """One image with the three bands, in the order of BANDS."""
    hand = ee.Image(MERIT_HYDRO).select("hnd").rename("hand_merit")

    dem_collection = ee.ImageCollection(COPERNICUS_DEM).select("DEM")
    # A mosaic forgets its projection; slope needs one to know what a metre
    # is. The GLO-30 tiles share one, so the first tile's is the right one.
    dem = dem_collection.mosaic().setDefaultProjection(
        dem_collection.first().projection())
    slope = ee.Terrain.slope(dem).rename("slope_deg")

    if include_30m:
        hand_30 = ee.Image(GLOBAL_HAND_30M).rename("hand_30m")
    else:
        hand_30 = ee.Image.constant(NODATA).rename("hand_30m")

    return (hand.addBands(hand_30).addBands(slope)
            .resample("bilinear").unmask(NODATA).toFloat())


def fetch_terrain(ee, grid, include_30m=True):
    """array[3, H, W] float32 with NaN for no data."""
    import numpy as np

    raw = ee.data.computePixels({
        "expression": terrain_image(ee, include_30m),
        "fileFormat": "NUMPY_NDARRAY",
        "grid": grid,
    })
    bands = []
    for name in BANDS:
        band = raw[name].astype("float32")
        band[band <= NODATA + 1] = np.nan
        bands.append(band)
    return np.stack(bands)


def write_tif(path, template_profile, array):
    import rasterio

    profile = dict(template_profile)
    profile.update(count=len(BANDS), dtype="float32", nodata=float("nan"))
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array)
        for index, name in enumerate(BANDS, 1):
            dst.set_band_description(index, name)


# ------------------------------------------------------------------ driver

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sen1floods11", required=True,
                        help="folder the Sen1Floods11 archive was extracted to")
    parser.add_argument("--out", default=None,
                        help="where to write *_Terrain.tif (default: <sen1floods11>/Terrain)")
    parser.add_argument("--limit", type=int, default=None, help="stop after N chips")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--no-30m", action="store_true",
                        help="skip the community GlobalHAND 30 m asset")
    args = parser.parse_args(argv)

    import rasterio

    root = Path(args.sen1floods11)
    out_dir = Path(args.out) if args.out else root / "Terrain"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "manifest.jsonl"

    chips = sorted(root.rglob("*_S1Hand.tif"))
    if args.limit:
        chips = chips[: args.limit]
    if not chips:
        sys.exit(f"No *_S1Hand.tif under {root}. Is this the Sen1Floods11 folder?")

    from core import earth_engine
    earth_engine.initialize()
    import ee

    # The community 30 m asset may be unreadable from some accounts. Find out
    # once, up front, rather than failing 446 times.
    include_30m = not args.no_30m
    if include_30m:
        try:
            ee.Image(GLOBAL_HAND_30M).bandNames().getInfo()
        except Exception as exc:          # noqa: BLE001 - reported, then skipped
            print(f"GlobalHAND 30 m not readable ({str(exc)[:120]}); "
                  "fetching MERIT HAND and slope only")
            include_30m = False

    for partial in out_dir.glob("*.part.tif"):
        partial.unlink()

    todo = [c for c in chips if not output_path(out_dir, chip_key(c)).exists()]
    skipped = len(chips) - len(todo)
    print(f"{len(chips)} chips, {skipped} already fetched, {len(todo)} to go -> {out_dir}")

    def fetch_one(chip):
        key = chip_key(chip)
        record = {"chip": key, "hand_30m": include_30m}
        try:
            with rasterio.open(chip) as src:
                profile = src.profile
                grid = ee_grid(src.transform, src.width, src.height, src.crs)
            array = with_retries(lambda: fetch_terrain(ee, grid, include_30m))
            target = output_path(out_dir, key)
            partial = target.with_name(target.stem + ".part.tif")
            write_tif(partial, profile, array)
            os.replace(partial, target)
            record["status"] = "ok"
        except Exception as exc:          # noqa: BLE001 - recorded per chip
            record["status"] = "failed"
            record["reason"] = str(exc)[:300]
        return record

    done = failed = 0
    lock = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=max(1, args.workers))
    try:
        futures = [executor.submit(fetch_one, chip) for chip in todo]
        for index, future in enumerate(as_completed(futures), 1):
            record = future.result()
            with lock:
                done += record["status"] == "ok"
                failed += record["status"] != "ok"
                with manifest.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
                print(f"[{skipped + index}/{len(chips)}] {record['chip']}: {record['status']}"
                      + (f" - {record.get('reason')}" if record["status"] != "ok" else ""),
                      flush=True)
    except KeyboardInterrupt:
        print("\nstopping - finished chips are kept; run the same command to resume")
        executor.shutdown(wait=False, cancel_futures=True)
        raise SystemExit(1)
    executor.shutdown(wait=True)
    print(f"\nfetched {done}, already present {skipped}, failed {failed}")


if __name__ == "__main__":
    main()
