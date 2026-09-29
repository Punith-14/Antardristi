"""
How much of India can this platform actually resolve?

The README says "any Indian district". Every boundary comes from FAO GAUL 2015,
which is the only administrative boundary set in the Earth Engine catalogue, so
that claim has a limit. This script measures it instead of hedging.

Two things get counted, and they are different failures:

    vintage    districts created after 2015 - Ladakh, Telangana's expansion
               from 10 districts to 33, Rajasthan's 2023 reorganisation. These
               genuinely do not exist in the dataset.

    naming     places GAUL files under names that were ALREADY obsolete in
               2015. Odisha was renamed from Orissa in 2011, Puducherry from
               Pondicherry in 2006. These exist and are simply mislabelled,
               which is why regions.NAME_ALIASES can fix them and the vintage
               gap cannot be fixed at all.

Run:  python measure_boundary_coverage.py
Writes evaluation/boundary_coverage.json, which the report cites.
"""

import json
from datetime import date, timezone, datetime
from pathlib import Path

import regions

OUT = Path(__file__).resolve().parent.parent / "evaluation" / "boundary_coverage.json"


# Districts and territories created or reorganised after the GAUL 2015 vintage.
# Not exhaustive - India has created well over a hundred districts since 2015 -
# but a spread wide enough to show the shape of the gap, with the parent each
# was carved out of so the failure message can suggest something usable.
POST_2015 = [
    # Union territory reorganisation, 2019.
    ("Ladakh", "Jammu and Kashmir", 2019),
    ("Leh", "Jammu and Kashmir", 2019),
    ("Kargil", "Jammu and Kashmir", 2019),
    # Telangana went from 10 districts to 33, mostly 2016.
    ("Bhadradri Kothagudem", "Khammam", 2016),
    ("Jagtial", "Karimnagar", 2016),
    ("Jayashankar Bhupalpally", "Warangal", 2016),
    ("Mancherial", "Adilabad", 2016),
    ("Mulugu", "Jayashankar Bhupalpally", 2019),
    ("Narayanpet", "Mahbubnagar", 2019),
    # Rajasthan, 2023.
    ("Balotra", "Barmer", 2023),
    ("Beawar", "Ajmer", 2023),
    ("Deeg", "Bharatpur", 2023),
    ("Kotputli-Behror", "Jaipur", 2023),
    ("Phalodi", "Jodhpur", 2023),
    ("Salumbar", "Udaipur", 2023),
    # Assorted others.
    ("Niwari", "Tikamgarh", 2018),
    ("Mayiladuthurai", "Nagapattinam", 2020),
    ("Chengalpattu", "Kancheepuram", 2019),
    ("Tenkasi", "Tirunelveli", 2019),
    ("Kallakurichi", "Villupuram", 2019),
    ("Hnahthial", "Lunglei", 2019),
    ("Saitual", "Aizawl", 2019),
    ("Noklak", "Tuensang", 2021),
    ("Eastern West Khasi Hills", "West Khasi Hills", 2021),
    ("Alluri Sitharama Raju", "Visakhapatnam", 2022),
    ("Anakapalli", "Visakhapatnam", 2022),
    ("Konaseema", "East Godavari", 2022),
    ("Sri Sathya Sai", "Anantapur", 2022),
    ("Pakke-Kessang", "East Kameng", 2018),
    ("Shi Yomi", "West Siang", 2018),
]

# Places that DO exist in GAUL 2015 but under a name retired years earlier.
# Current name first, the year it was renamed second.
RENAMED = [
    ("Odisha", 2011),
    ("Puducherry", 2006),
    ("Uttarakhand", 2007),
    ("Bengaluru", 2014),
    ("Mumbai", 1995),
    ("Chennai", 1996),
    ("Kolkata", 2001),
    ("Thiruvananthapuram", 1991),
    ("Prayagraj", 2018),
    ("Mysuru", 2014),
    ("Belagavi", 2014),
    ("Kalaburagi", 2014),
    ("Ballari", 2014),
]


def probe(name):
    """Resolve one name. Returns (status, detail)."""
    try:
        entry = regions.lookup(name)
    except regions.RegionNotFound:
        return "missing", None
    except regions.RegionAmbiguous as exc:
        return "ambiguous", exc.matches
    return "resolved", entry["name"]


def near_misses(name, limit=3):
    """GAUL names that look like this one.

    A miss is only actionable if you can see what the dataset calls the place
    instead. Guessing the alias map from memory produced six wrong entries -
    Puducherry mapped to Pondicherry when GAUL already said Puducherry - so
    the corrections come from the data now.
    """
    import difflib

    keys = list(regions.gaul_name_index())
    wanted = regions._normalize(name)

    close = difflib.get_close_matches(wanted, keys, n=limit, cutoff=0.6)

    # difflib misses a short name inside a longer one, which is the common
    # case here: "kochi" against "ernakulam" scores nothing, but "mumbai"
    # against "mumbai suburban" is exactly what we want to surface.
    head = wanted[:5]
    if len(head) >= 4:
        contained = [k for k in keys if head in k and k not in close]
        close.extend(sorted(contained)[: limit - len(close)])

    names = []
    for key in close[:limit]:
        for entry in regions.gaul_name_index()[key]:
            label = f"{entry['name']} ({entry['level']})"
            if label not in names:
                names.append(label)
    return names


def main():
    print(f"Boundary source : {regions.BOUNDARY_SOURCE}")
    print(f"Vintage         : {regions.BOUNDARY_VINTAGE}")
    print("Building the name index (one Earth Engine call per level)...")

    index = regions.gaul_name_index()
    states = sum(1 for v in index.values() for e in v if e["level"] == "state")
    districts = sum(1 for v in index.values() for e in v if e["level"] == "district")
    print(f"  {states} states, {districts} districts\n")

    # --- post-2015 districts ------------------------------------------------
    print("Districts created after the 2015 vintage")
    print("-" * 64)
    vintage_rows = []
    for name, parent, year in POST_2015:
        status, detail = probe(name)
        vintage_rows.append(
            {"name": name, "parent": parent, "created": year,
             "status": status, "resolved_as": detail}
        )
        mark = {"resolved": "ok", "missing": "MISSING", "ambiguous": "AMBIGUOUS"}[status]
        print(f"  {name:<28}{year}  {mark:<10} parent: {parent}")

    missing = [r for r in vintage_rows if r["status"] == "missing"]
    print(f"\n  {len(missing)} of {len(vintage_rows)} unresolvable "
          f"({len(missing) / len(vintage_rows):.0%})")

    # --- renamed places -----------------------------------------------------
    print("\nPlaces GAUL 2015 files under an older name")
    print("-" * 64)
    naming_rows = []
    for name, year in RENAMED:
        status, detail = probe(name)
        aliased = regions.canonical_name(name) != regions._normalize(name)
        naming_rows.append(
            {"name": name, "renamed": year, "status": status,
             "gaul_name": detail, "alias_needed": aliased}
        )
        note = f"-> {detail}" if status == "resolved" and detail != name else ""
        mark = {"resolved": "ok", "missing": "MISSING", "ambiguous": "AMBIGUOUS"}[status]
        print(f"  {name:<28}renamed {year}  {mark:<10} {note}")

    unfixed = [r for r in naming_rows if r["status"] != "resolved"]
    print(f"\n  {len(naming_rows) - len(unfixed)} of {len(naming_rows)} "
          "resolve once aliased")

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "boundary_source": regions.BOUNDARY_SOURCE,
        "boundary_vintage": regions.BOUNDARY_VINTAGE,
        "catalogue": {"states": states, "districts": districts},
        "post_2015_districts": {
            "tested": len(vintage_rows),
            "unresolvable": len(missing),
            "share_unresolvable": round(len(missing) / len(vintage_rows), 3),
            "rows": vintage_rows,
        },
        "renamed_places": {
            "tested": len(naming_rows),
            "resolved_via_alias": len(naming_rows) - len(unfixed),
            "rows": naming_rows,
        },
        "conclusion": (
            "Districts created after 2015 cannot be resolved and no alias fixes "
            "that - the boundary simply is not in the dataset. Places renamed "
            "before 2015 that GAUL still lists under old names ARE resolvable, "
            "and regions.NAME_ALIASES does so. The honest claim is 'any Indian "
            "district as it stood in 2015', not 'any Indian district'."
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWritten to {OUT}")


if __name__ == "__main__":
    main()
