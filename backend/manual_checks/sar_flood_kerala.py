"""
Sentinel-1 flood detection against the Kerala August 2018 floods.

This is the event our optical pipeline could not analyse at all: Sentinel-2
Level-2A has zero scenes over Kerala for that window. Sentinel-1 has flown since
2014 and does not care about cloud.

Run:  python test_sar_flood.py
"""

import json

from dotenv import load_dotenv

load_dotenv()

import ee

from pipeline import analysis
from pipeline.report import build_report, collect_extra_values
from core.verification import summarise

# Kerala's peak flooding was 15-19 August 2018.
PRE = ("2018-02-01", "2018-04-30")
POST = ("2018-08-15", "2018-08-25")


def kerala():
    analysis._initialize()
    geometry = (
        ee.FeatureCollection("FAO/GAUL/2015/level1")
        .filter(ee.Filter.eq("ADM0_NAME", "India"))
        .filter(ee.Filter.eq("ADM1_NAME", "Kerala"))
        .geometry()
    )
    meta = {
        "slug": "kerala",
        "name": "Kerala",
        "admin_level": "state",
        "state": "Kerala",
        "boundary_source": "FAO/GAUL/2015/level1",
        "boundary_vintage": "2015",
    }
    return geometry, meta


def main():
    geometry, meta = kerala()

    print("Running Sentinel-1 flood analysis for Kerala, Aug 2018.")
    print("(Optical returned zero scenes for this window.)\n")

    payload = analysis.analyse_flood(
        region_geometry=geometry,
        region_meta=meta,
        post_start=POST[0],
        post_end=POST[1],
        pre_start=PRE[0],
        pre_end=PRE[1],
        force_sensor="sentinel-1",
        scale=200,          # coarse: this is a smoke test, not the final number
    )
    payload.pop("_internal", None)

    print("--- OBSERVATION ---")
    print(json.dumps(payload["observation"], indent=2))

    print("\n--- EVIDENCE ---")
    for item in payload["evidence"]:
        print(f"  [{item['id']}] {item['quantity']:<26} {item['value']:>10} {item.get('unit','')}")
        if item.get("method"):
            print(f"        {item['method']}")

    zones = payload.get("zones") or []
    print(f"\n--- ZONES ({len(zones)}) ---")
    for zone in zones[:10]:
        print(
            f"  {zone['id']:<4} {zone['area_km2']:>8.2f} km2  "
            f"{zone['centroid'][1]:>7.3f} N {zone['centroid'][0]:>7.3f} E  "
            f"{zone['severity']}"
        )
    summary = payload.get("zones_summary")
    if summary:
        extent = next(
            (e["value"] for e in payload["evidence"] if e["quantity"] == "flood_extent"),
            0,
        )
        print(f"\n  zones found  : {summary['count']} (listed {summary['listed']})")
        print(f"  truncated    : {summary['truncated']}")
        print(f"  all zones    : {summary['total_area_km2']:,.1f} km2")
        print(f"  flood extent : {extent:,.1f} km2")
        if extent:
            print(
                f"  zoned        : {summary['total_area_km2'] / extent * 100:.0f}%"
                " of extent is contiguous enough to form a zone"
            )

    print("\n--- REPORT ---")
    report, verification = build_report(payload)
    print(report["text"])
    caveats = verification.get("caveats", {})
    print(
        f"\n  model        : {report['generator_model'] or 'template'}"
        f"\n  fallback     : {report['fallback_used']}"
        f"\n  reason       : {report.get('fallback_reason')}"
        f"\n  faithfulness : {verification['faithfulness_rate']:.2%}"
        f"\n  completeness : {caveats.get('completeness', 1):.2%}"
        f"\n  {summarise(verification)}"
    )

    rejected = report.get("rejected_attempt")
    if rejected:
        print("\n--- REJECTED LLM ATTEMPT ---")
        print(rejected["text"])
        print(
            f"\n  faithfulness : {rejected['faithfulness_rate']:.2%}"
            f"\n  completeness : {rejected['completeness']:.2%}"
        )
        for claim in rejected["unsupported_claims"]:
            print(f"    UNSUPPORTED: '{claim['claim']}'")
        for citation in rejected["invalid_citations"]:
            print(f"    INVALID CITATION: {citation}")
        for omitted in rejected["omitted_caveats"]:
            print(f"    OMITTED: {omitted['note'][:80]}...")

    out = "kerala_2018_sar.json"
    payload["report"] = report
    payload["verification"] = verification
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
