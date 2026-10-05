"""
People living in the flooded area - per zone, per district and in total.

The question a district disaster official asks is not "how many km2?" but
"how many people, and where?". Two population models answer it here, and the
answer is reported as a range between them rather than a single figure:

    GHSL GHS_POP (JRC)    100 m, five-yearly epochs 1975-2020, projections to 2030
    WorldPop GP 100m      ~93 m, yearly 2000-2021

They disaggregate census counts to cells in different ways (built-up surface
versus machine-learned covariates), so where they disagree, the spread is real
uncertainty that a single number would hide.

THE PITFALL THIS MODULE IS BUILT AROUND. A population raster holds COUNTS -
people per cell. Earth Engine answers a reduction at a coarser scale by
averaging cells (its pyramids), so summing a 100 m count raster at the flood
map's 200 m analysis scale averages four cells and sums the averages:
roughly a four-fold undercount, silently, with a plausible-looking result.
Every sum here therefore runs in the population dataset's OWN projection
(`crs=pop.projection()`, no `scale`). The flood mask is resampled onto the
population grid, never the other way round. A test checks the reductions are
called that way.

What the numbers mean, stated in every result: people LIVING in cells the
flood map marks as flooded. Not people displaced, injured or harmed. And
because the radar rule misses part of the flood water, more likely too low
than too high.
"""

import ee

from core import earth_engine

GHSL = "JRC/GHSL/P2023A/GHS_POP"
GHSL_BAND = "population_count"
GHSL_EPOCHS = tuple(range(1975, 2031, 5))

WORLDPOP = "WorldPop/GP/100m/pop"
WORLDPOP_BAND = "population"
WORLDPOP_YEARS = (2000, 2021)
WORLDPOP_COUNTRY = "IND"

# Below this analysis scale (m) the flood mask is finer than the population
# grid, so each population cell is weighted by the FRACTION of it flooded.
# At or above it, a population cell takes the flooded/not status of the flood
# pixel it falls in - which is what the area figure itself counts.
FRACTION_BELOW_M = 90


# ------------------------------------------------------------- pure helpers

def ghsl_epoch(year):
    """Nearest GHSL epoch to the event year: 2016 -> 2015, 2018 -> 2020."""
    year = int(year)
    return min(GHSL_EPOCHS, key=lambda e: (abs(e - year), -e))


def worldpop_year(year):
    """The event year, clamped to the years WorldPop publishes."""
    return max(WORLDPOP_YEARS[0], min(WORLDPOP_YEARS[1], int(year)))


def round_people(value):
    """A count rounded to the precision a model estimate supports.

    Thousands to the nearest hundred, hundreds to the nearest ten. "12,437
    people" claims a precision two disaggregation models cannot deliver;
    "12,400" does not. Never rounds a non-zero count down to zero.
    """
    if value is None:
        return None
    value = float(value)
    if value <= 0:
        return 0
    if value >= 1000:
        return int(round(value, -2))
    if value >= 100:
        return int(round(value, -1))
    return max(1, int(round(value)))


def as_range(by_source):
    """{"ghsl": a, "worldpop": b} -> {"low", "high", "sources"} over what exists."""
    values = {k: v for k, v in by_source.items() if v is not None}
    if not values:
        return None
    return {"low": min(values.values()), "high": max(values.values()),
            "by_source": values}


def caveat(sources, missed_fraction=None):
    """One sentence: what the figure is, and which way it is likely wrong.

    One sentence, because every note has to survive into the generated report.
    """
    named = " and ".join(f"{s['label']} {s['year']}" for s in sources)
    tail = ""
    if missed_fraction:
        tail = (f", and is more likely too low than too high because about "
                f"{missed_fraction:.0%} of flood water is expected to be missed")
    return (
        f"Population is a model estimate of people living in the flooded area "
        f"({named}), not of people displaced or harmed{tail}."
    )


# ------------------------------------------------------------ earth engine

def pick_year(preferred, available):
    """The preferred year if the catalogue has it, else the nearest it does.

    Ties go to the earlier year: a published estimate beats a projection. With
    no list (the catalogue could not be asked) the preferred year is used.
    """
    if not available:
        return preferred
    if preferred in available:
        return preferred
    return min(available, key=lambda y: (abs(y - preferred), y))


# Which years each catalogue really holds, asked once per process. The fixed
# ranges above are what the datasets document; the catalogue is what exists.
# A 2026 flood asked for an image that was not there, .first() returned null,
# and both the people count and the district table failed with
# "Image.select: Parameter 'input' is required".
_AVAILABLE = {}


def available_years(key):
    if key not in _AVAILABLE:
        try:
            if key == "ghsl":
                stamps = ee.ImageCollection(GHSL).aggregate_array("system:time_start").getInfo()
                years = [int(ee_date_year(t)) for t in stamps]
            else:
                years = (ee.ImageCollection(WORLDPOP)
                         .filter(ee.Filter.eq("country", WORLDPOP_COUNTRY))
                         .aggregate_array("year").getInfo())
            _AVAILABLE[key] = sorted({int(y) for y in years})
        except Exception:
            return []            # unknown: fall back to the documented years, not cached
    return _AVAILABLE[key]


def ee_date_year(millis):
    """Year of an Earth Engine timestamp (milliseconds since 1970, UTC)."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc).year


def sources_for(event_year):
    """[(key, label, year, ee.Image)] for the event year, both models.

    Each model uses the nearest year its catalogue actually has.
    """
    epoch = pick_year(ghsl_epoch(event_year), available_years("ghsl"))
    ghsl = (
        ee.ImageCollection(GHSL)
        .filterDate(f"{epoch}-01-01", f"{epoch}-12-31")
        .first()
        .select(GHSL_BAND)
    )
    wp_year = pick_year(worldpop_year(event_year), available_years("worldpop"))
    worldpop = (
        ee.ImageCollection(WORLDPOP)
        .filter(ee.Filter.eq("country", WORLDPOP_COUNTRY))
        .filter(ee.Filter.eq("year", wp_year))
        .first()
        .select(WORLDPOP_BAND)
    )
    return [
        ("ghsl", "GHSL", epoch, ghsl),
        ("worldpop", "WorldPop", wp_year, worldpop),
    ]


def flooded_population(population, flood_mask, analysis_scale):
    """People per population cell that the flood map marks as flooded.

    The mask is evaluated at the analysis scale (the scale the extent figure
    was measured at) and resampled onto the POPULATION grid. Finer than that
    grid, each cell is weighted by the fraction of it flooded.
    """
    proj = population.projection()
    mask = flood_mask.unmask(0).reproject(crs="EPSG:4326", scale=analysis_scale)
    if analysis_scale < FRACTION_BELOW_M:
        weight = (mask.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024)
                  .reproject(proj))
    else:
        weight = mask.reproject(proj)
    return population.multiply(weight).reproject(proj)


def sum_over(image, geometry, projection, reducer=None):
    """Total of a count image over a geometry, in the image's own projection.

    `crs=projection` and no `scale`: see the module docstring for the
    four-fold undercount this prevents.
    """
    reducer = reducer if reducer is not None else ee.Reducer.sum()
    return image.reduceRegion(
        reducer=reducer, geometry=geometry, crs=projection,
        maxPixels=10_000_000_000, bestEffort=False,
    )


def sum_per_feature(image, features, projection, reducer=None):
    """Totals per feature, in the image's own projection. Same rule as sum_over."""
    reducer = reducer if reducer is not None else ee.Reducer.sum()
    return image.reduceRegions(collection=features, reducer=reducer, crs=projection)


def exposure(flood_mask, region, zones, event_date, analysis_scale, missed_fraction=None):
    """People in the flood extent and in each zone, from both models.

    Returns a dict, or {"error": reason} - a population failure must never
    cost the user the flood result it accompanies.
    """
    earth_engine.initialize()
    try:
        year = int(str(event_date)[:4])
        sources = sources_for(year)

        zone_fc = None
        zone_ids = [z["id"] for z in zones if z.get("geometry")]
        if zone_ids:
            zone_fc = ee.FeatureCollection([
                ee.Feature(ee.Geometry(z["geometry"]), {"zone_id": z["id"]})
                for z in zones if z.get("geometry")
            ])

        totals, per_zone, used, failed = {}, {}, [], []
        for key, label, src_year, population in sources:
            # One model failing must not cost the other: the range then comes
            # from the one that worked, and the sources list says which.
            try:
                flooded = flooded_population(population, flood_mask, analysis_scale)
                proj = population.projection()
                band = population.bandNames().get(0)

                total = sum_over(flooded, region, proj).get(band).getInfo()
                zone_rows = (sum_per_feature(flooded, zone_fc, proj).getInfo()["features"]
                             if zone_fc is not None else [])
            except Exception as exc:
                failed.append(f"{label}: {str(exc)[:80]}")
                continue
            totals[key] = round_people(total or 0)
            used.append({"key": key, "label": label, "year": src_year,
                         "id": GHSL if key == "ghsl" else WORLDPOP})
            for row in zone_rows:
                props = row["properties"]
                per_zone.setdefault(props["zone_id"], {})[key] = round_people(
                    props.get("sum") or 0)

        if not used:
            raise RuntimeError("; ".join(failed) or "no population model available")

        return {
            "people_in_flood": as_range(totals),
            "per_zone": {zid: as_range(v) for zid, v in per_zone.items()},
            "sources": used,
            "caveat": caveat(used, missed_fraction),
            "scale_note": "summed on each population dataset's own grid",
        }
    except Exception as exc:          # never let population cost the flood result
        return {"error": f"population could not be computed: {str(exc)[:160]}"}
