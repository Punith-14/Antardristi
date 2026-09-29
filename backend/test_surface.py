"""
All six surface analyses, on regions where each one should show something.

Server must be running:  uvicorn main:app --reload
Then:                    python test_surface.py

Each case is chosen so the answer is checkable against common knowledge - if
Punjab shows no vegetation in kharif season, something is wrong.
"""

import json

import requests

BASE = "http://127.0.0.1:8000"

CASES = [
    {
        "name": "Punjab kharif vegetation",
        "expect": "high vegetated fraction - this is the rice bowl in growing season",
        "body": {
            "region": "punjab",
            "analysis_type": "vegetation_health",
            "post_start": "2023-09-01",
            "post_end": "2023-09-30",
        },
    },
    {
        "name": "Punjab moisture stress, pre-monsoon",
        "expect": "more stress in May than September",
        "body": {
            "region": "punjab",
            "analysis_type": "crop_stress",
            "post_start": "2023-05-01",
            "post_end": "2023-05-31",
        },
    },
    {
        "name": "Kerala surface water",
        "expect": "backwaters and Vembanad should register",
        "body": {
            "region": "kerala",
            "analysis_type": "water_extent",
            "post_start": "2023-01-01",
            "post_end": "2023-03-31",
        },
    },
    {
        "name": "Bengaluru built-up",
        "expect": "high built-up fraction for a major city district",
        "body": {
            "region": "bangalore urban",
            "analysis_type": "built_up",
            "post_start": "2023-01-01",
            "post_end": "2023-03-31",
        },
    },
    {
        "name": "Bengaluru built-up change, 2019 vs 2023",
        "expect": "net gain - the city grew",
        "body": {
            "region": "bangalore urban",
            "analysis_type": "built_up",
            "post_start": "2023-01-01",
            "post_end": "2023-03-31",
            "pre_start": "2019-01-01",
            "pre_end": "2019-03-31",
        },
    },
    {
        "name": "Kerala green cover",
        "expect": "very high - one of India's most forested states",
        "body": {
            "region": "kerala",
            "analysis_type": "green_cover",
            "post_start": "2023-01-01",
            "post_end": "2023-03-31",
        },
    },
]


def run(case):
    print("\n" + "=" * 70)
    print(case["name"])
    print(f"  expect: {case['expect']}")
    print("=" * 70)

    response = requests.post(
        f"{BASE}/analyze/surface", json={**case["body"], "scale": 200}, timeout=900
    )
    if response.status_code != 200:
        print(f"  HTTP {response.status_code}: {response.text[:300]}")
        return None

    payload = response.json()

    if not payload.get("evidence"):
        print("  no evidence -", (payload.get("unobserved") or {}).get("reason"))
        for note in (payload.get("unobserved") or {}).get("notes", []):
            print("   ", note[:120])
        return payload

    observation = payload.get("observation") or {}
    print(f"  coverage : {observation.get('coverage_fraction', 0) * 100:.1f}%"
          f"  ({observation.get('scenes_used')}/{observation.get('scenes_available')} scenes)")

    for item in payload["evidence"]:
        print(f"  [{item['id']}] {item['quantity']:<32} {item['value']:>10} {item.get('unit','')}")

    verification = payload.get("verification") or {}
    report = payload.get("report") or {}
    print(f"\n  {report.get('text','')[:600]}")
    print(
        f"\n  model {report.get('generator_model') or 'template'}"
        f" | faithfulness {verification.get('faithfulness_rate', 0):.0%}"
        f" | completeness {(verification.get('caveats') or {}).get('completeness', 0):.0%}"
    )
    return payload


def main():
    print("Available analyses:")
    catalogue = requests.get(f"{BASE}/analyses", timeout=30).json()
    for entry in catalogue["surface"]:
        flag = "validated" if entry["validated"] else "UNVALIDATED threshold"
        print(f"  {entry['type']:<20} {entry['index']:<6} {flag}")
    print(f"  {'flood_extent':<20} {'VV':<6} validated: IoU "
          f"{catalogue['flood']['validation']['iou']}")

    results = {}
    for case in CASES:
        results[case["name"]] = run(case)

    with open("surface_results.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print("\n\nSaved surface_results.json")


if __name__ == "__main__":
    main()
