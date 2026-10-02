"""
Should named regions move from FAO GAUL 2015 to GAUL 2025? Measured.

Earth Engine marks GAUL 2015 deprecated. The newer set is only worth
adopting if it actually does what the 2015 set cannot - name India's
post-2015 districts - without losing anything the platform relies on. This
runs the same probes against both sets and applies a rule written before
the run:

    SWITCH only if all three hold:
      1. more of the post-2015 districts resolve under 2025 than 2015, and at
         least half of them do;
      2. no state or union territory that resolves under 2015 is lost under
         2025 (renames such as Orissa -> Odisha count as kept);
      3. Kerala - where every live measurement was made - keeps its district
         count and its area within 2%, so measured results stay comparable.

Run from backend/ (one Earth Engine call per level per set):

    python -m scripts.compare_boundaries

Writes evaluation/boundary_comparison.json. If it says SWITCH, set
BOUNDARY_SET=2025 in backend/.env and restart; nothing else changes.
"""

import json
from datetime import datetime, timezone

from core import paths
from scripts.measure_boundary_coverage import POST_2015, RENAMED

OUT = paths.EVALUATION / "boundary_comparison.json"
MIN_SHARE_RESOLVED = 0.5
KERALA_AREA_TOLERANCE = 0.02
SETS = ("2015", "2025")


# ------------------------------------------------------------ pure decision

def decide(comparison):
    """The pre-stated rule. Returns {"switch": bool, "reasons": [...]}."""
    reasons, ok = [], True
    old, new = comparison["post_2015"]["2015"], comparison["post_2015"]["2025"]
    total = comparison["post_2015"]["tested"]
    if new > old and new >= MIN_SHARE_RESOLVED * total:
        reasons.append(f"post-2015 districts resolved: {old} -> {new} of {total}")
    else:
        ok = False
        reasons.append(f"post-2015 districts resolved: {old} -> {new} of {total} "
                       f"(needs more than {old} and at least {MIN_SHARE_RESOLVED:.0%})")
    lost = comparison["states_lost"]
    if lost:
        ok = False
        reasons.append(f"states or UTs lost under 2025: {', '.join(lost)}")
    else:
        reasons.append("no state or union territory lost")
    kerala = comparison["kerala"]
    counts = kerala["districts"]
    area_change = kerala["area_change"]
    if counts["2015"] != counts["2025"] or area_change is None or abs(area_change) > KERALA_AREA_TOLERANCE:
        ok = False
        reasons.append(f"Kerala changed: districts {counts['2015']} -> {counts['2025']}, area "
                       f"{'unknown' if area_change is None else f'{area_change:+.1%}'}")
    else:
        reasons.append(f"Kerala unchanged: {counts['2025']} districts, area {area_change:+.2%}")
    return {"switch": ok, "reasons": reasons}


def states_lost(old_states, new_states, aliases):
    """2015 states with no 2025 counterpart, by name or known rename."""
    from geo.regions import _normalize
    new = {_normalize(s) for s in new_states}
    reverse = {v: k for k, v in aliases.items()}
    lost = []
    for state in old_states:
        n = _normalize(state)
        if n in new or reverse.get(n) in new or aliases.get(n) in new:
            continue
        lost.append(state)
    return sorted(lost)


# ------------------------------------------------------------ measurement

def main():
    import ee

    from core import earth_engine
    from geo import boundaries, regions

    earth_engine.initialize()
    rows, resolved, states, district_counts, kerala_area = [], {}, {}, {}, {}
    for key in SETS:
        index = regions.gaul_name_index(key)
        states[key] = sorted({e["name"] for v in index.values() for e in v if e["level"] == "state"})
        district_counts[key] = sum(1 for v in index.values() for e in v if e["level"] == "district")
        print(f"GAUL {key}: {len(states[key])} states/UTs, {district_counts[key]} districts")

    kerala_districts = {}
    for key in SETS:
        a = boundaries.active(key)
        kerala = boundaries.india(a["state_dataset"], key).filter(
            ee.Filter.eq(a["state_field"], "Kerala"))
        kerala_area[key] = kerala.geometry().area(maxError=100).getInfo() / 1e6
        kerala_districts[key] = boundaries.india(a["district_dataset"], key).filter(
            ee.Filter.eq(a["state_field"], "Kerala")).size().getInfo()

    print("\nPost-2015 districts")
    for name, parent, year in POST_2015:
        row = {"name": name, "parent": parent, "created": year}
        for key in SETS:
            try:
                entry = regions.lookup(name, key)
                row[key] = f"{entry['name']} ({entry['state']})"
            except regions.RegionAmbiguous as exc:
                row[key] = f"ambiguous: {', '.join(exc.matches[:3])}"
            except regions.RegionNotFound:
                row[key] = None
        rows.append(row)
        print(f"  {name:<26}{year}   2015: {row['2015'] or '-':<28} 2025: {row['2025'] or '-'}")
    for key in SETS:
        resolved[key] = sum(1 for r in rows if r[key] and not r[key].startswith("ambiguous"))

    renamed = []
    for name, year in RENAMED:
        row = {"name": name, "renamed": year}
        for key in SETS:
            try:
                row[key] = regions.lookup(name, key)["name"]
            except (regions.RegionNotFound, regions.RegionAmbiguous):
                row[key] = None
        renamed.append(row)

    comparison = {
        "post_2015": {"tested": len(rows), "2015": resolved["2015"], "2025": resolved["2025"], "rows": rows},
        "states": states,
        "states_lost": states_lost(states["2015"], states["2025"], regions.NAME_ALIASES),
        "states_new": sorted(set(states["2025"]) - set(states["2015"])),
        "district_counts": district_counts,
        "kerala": {
            "districts": kerala_districts,
            "area_km2": {k: round(v, 1) for k, v in kerala_area.items()},
            "area_change": ((kerala_area["2025"] - kerala_area["2015"]) / kerala_area["2015"]
                            if kerala_area.get("2015") else None),
        },
        "renamed_places": renamed,
    }
    decision = decide(comparison)
    print(f"\nNew states/UTs in 2025: {', '.join(comparison['states_new']) or 'none'}")
    print(f"Lost from 2015: {', '.join(comparison['states_lost']) or 'none'}")
    print("\n" + ("SWITCH to GAUL 2025" if decision["switch"] else "STAY on GAUL 2015"))
    for reason in decision["reasons"]:
        print("  -", reason)

    OUT.write_text(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rule": {"min_share_resolved": MIN_SHARE_RESOLVED,
                 "kerala_area_tolerance": KERALA_AREA_TOLERANCE},
        **comparison, "decision": decision}, indent=2), encoding="utf-8")
    print(f"\nWritten to {OUT}")
    if decision["switch"]:
        print("Set BOUNDARY_SET=2025 in backend/.env and restart uvicorn.")


if __name__ == "__main__":
    main()
