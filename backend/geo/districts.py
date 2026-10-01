"""
Flooding per district: which districts of a state are worst hit, and which
districts a drawn area falls in.

A State Disaster Management Authority asking about Assam needs the
districts ranked, not one number for the state. A district official who drew
a box around a new district needs to know which (2015) districts that box
overlaps. Both are the same computation: sum areas per district over the
part of the district inside the analysed region.

Per district:

    flooded_km2          flooded area inside the district (and the region)
    flooded_pct          of the part of the district the satellite SAW
    observed_pct         how much of the district the satellite saw at all
    people               the population range, as for the whole region
    low_coverage         True when under 90% of it was observed

The last two columns are the point. One radar pass often covers half a
state; a district on the edge of the swath can show little flooding because
little of it was seen. Ranking it without saying so would tell an official
that district is fine.

Areas are pixel-area sums, which are correct at any scale - unlike counts of
people, which population.py has to sum on its own grid.
"""

import ee

from core import earth_engine
from geo import population as population_model

GAUL_DISTRICTS = "FAO/GAUL/2015/level2"
LOW_COVERAGE = 0.9

# District boundaries and the state boundary are separate GAUL layers and do
# not align exactly. Within this share the districts are taken to add up to
# the region; beyond it the difference is reported as a warning.
SUM_TOLERANCE = 0.02


# ------------------------------------------------------------- pure helpers

def build_rows(raw, people=None):
    """Rows for the table, worst first.

    `raw`: [{"name", "state", "flooded_m2", "observed_m2", "in_region_m2"}].
    `people`: {district_key(name, state): range dict}, or None. Keyed on name AND
    state because India reuses district names across states - Aurangabad is
    in Maharashtra and in Bihar - and a drawn area can touch both.
    """
    rows = []
    for item in raw:
        in_region = (item.get("in_region_m2") or 0) / 1e6
        if in_region <= 0:
            continue                       # touches the region only at its edge
        observed = (item.get("observed_m2") or 0) / 1e6
        flooded = (item.get("flooded_m2") or 0) / 1e6
        observed_fraction = observed / in_region
        rows.append({
            "name": item["name"],
            "state": item.get("state"),
            "flooded_km2": round(flooded, 1),
            "flooded_pct": round(100 * flooded / observed, 2) if observed else None,
            "observed_pct": round(100 * min(observed_fraction, 1.0), 1),
            "in_region_km2": round(in_region, 1),
            "low_coverage": observed_fraction < LOW_COVERAGE,
            "people": (people or {}).get(district_key(item["name"], item.get("state"))),
        })
    rows.sort(key=lambda r: (-r["flooded_km2"], r["name"]))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows


def district_key(name, state):
    return f"{name}|{state}"


def add_shares(rows):
    """For a drawn area: each district's share of the area that was drawn."""
    total = sum(r["in_region_km2"] for r in rows)
    for row in rows:
        row["share_of_area_pct"] = round(100 * row["in_region_km2"] / total, 1) if total else None
    return rows


def sum_check(rows, region_flood_km2):
    """Do the districts add up to the region's own total? Reported either way."""
    districts_total = round(sum(r["flooded_km2"] for r in rows), 1)
    difference = round(districts_total - (region_flood_km2 or 0), 1)
    share = abs(difference) / region_flood_km2 if region_flood_km2 else 0.0
    return {
        "districts_total_km2": districts_total,
        "region_total_km2": round(region_flood_km2 or 0, 1),
        "difference_km2": difference,
        "within_tolerance": share <= SUM_TOLERANCE,
    }


def coverage_note(rows):
    """One sentence when districts were only partly seen, else None."""
    partial = [r["name"] for r in rows if r["low_coverage"]]
    if not partial:
        return None
    named = ", ".join(partial[:4]) + (f" and {len(partial) - 4} more" if len(partial) > 4 else "")
    verb = "was" if len(partial) == 1 else "were"
    return (
        f"{len(partial)} of {len(rows)} districts {verb} less than "
        f"{LOW_COVERAGE:.0%} observed ({named}), so their flooded area covers only "
        "the part the satellite saw and may understate the flooding there."
    )


def top_districts(rows, n=3):
    return [r for r in rows if r["flooded_km2"] > 0][:n]


# ------------------------------------------------------------ earth engine

def districts_for(region_meta, region):
    """The district features to break down by, or None.

    A state: its own districts. A drawn area: every district it touches. A
    single district: nothing to break down.
    """
    collection = ee.FeatureCollection(GAUL_DISTRICTS).filter(
        ee.Filter.eq("ADM0_NAME", "India"))
    level = region_meta.get("admin_level")
    if level == "state":
        return collection.filter(ee.Filter.eq("ADM1_NAME", region_meta["name"])), "state_breakdown"
    if level == "custom":
        return collection.filterBounds(region), "drawn_area_overlap"
    return None, None


def breakdown(flood_mask, valid_mask, region, region_meta, scale, region_flood_km2,
              event_date=None):
    """The per-district table, or None when there is nothing to break down.

    Returns {"kind", "rows", "sum_check", "note", "boundary_source"} or
    {"error": reason}. A failure here never costs the flood result.
    """
    earth_engine.initialize()
    try:
        features, kind = districts_for(region_meta, region)
        if features is None:
            return None

        area = ee.Image.pixelArea()
        stack = (
            area.updateMask(flood_mask).rename("flooded_m2")
            .addBands(area.updateMask(valid_mask).rename("observed_m2"))
            .addBands(area.clip(region).rename("in_region_m2"))
            .clip(region)
        )
        stats = stack.reduceRegions(collection=features, reducer=ee.Reducer.sum(),
                                    scale=scale).getInfo()
        raw = [{
            "name": f["properties"].get("ADM2_NAME"),
            "state": f["properties"].get("ADM1_NAME"),
            "flooded_m2": f["properties"].get("flooded_m2"),
            "observed_m2": f["properties"].get("observed_m2"),
            "in_region_m2": f["properties"].get("in_region_m2"),
        } for f in stats["features"]]

        people = None
        if event_date and region_flood_km2:
            people = district_people(flood_mask, features, event_date, scale)

        rows = build_rows(raw, people)
        if kind == "drawn_area_overlap":
            add_shares(rows)
        return {
            "kind": kind,
            "rows": rows,
            "sum_check": sum_check(rows, region_flood_km2),
            "note": coverage_note(rows),
            "boundary_source": f"{GAUL_DISTRICTS} (2015)",
        }
    except Exception as exc:
        return {"error": f"district breakdown could not be computed: {str(exc)[:160]}"}


def district_people(flood_mask, features, event_date, scale):
    """{district_key: range} from both population models, on their own grids."""
    by_district = {}
    for key, label, year, image in population_model.sources_for(int(str(event_date)[:4])):
        flooded = population_model.flooded_population(image, flood_mask, scale)
        rows = population_model.sum_per_feature(
            flooded, features, image.projection()).getInfo()["features"]
        for row in rows:
            name = district_key(row["properties"].get("ADM2_NAME"),
                                row["properties"].get("ADM1_NAME"))
            by_district.setdefault(name, {})[key] = population_model.round_people(
                row["properties"].get("sum") or 0)
    return {name: population_model.as_range(v) for name, v in by_district.items()}
