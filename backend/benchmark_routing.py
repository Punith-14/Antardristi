"""
Score the router against the India EO query benchmark.

Run:
    python benchmark_routing.py            # model routing
    python benchmark_routing.py --rules    # deterministic only, for comparison
    python benchmark_routing.py --model openai/gpt-oss-120b

The rules-only run is the ablation: it shows what the language model is
actually adding, rather than assuming it helps.
"""

import argparse
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import routing

EVALUATION = Path(__file__).resolve().parent.parent / "evaluation"
BENCHMARK = EVALUATION / "query_benchmark.json"
HELDOUT = EVALUATION / "query_heldout.json"


def matches_region(expected, actual):
    """Region names vary in spelling and case; compare loosely but not blindly."""
    if expected is None:
        return actual is None
    if actual is None:
        return False
    return expected.lower().strip() in actual.lower().strip() or \
        actual.lower().strip() in expected.lower().strip()


def period_covers(route, required):
    if required is None:
        return True
    start, end = route.get("post_start"), route.get("post_end")
    if not start or not end:
        return False
    return start <= required <= end


def score_case(case, route):
    """Score extraction, not resolution.

    `region` asks what the router read out of the question. Whether that place
    exists in FAO GAUL is the resolver's problem, and is carried separately as
    `region_resolvable` so a correct reading of an unresolvable place is not
    counted as a failure.
    """
    expected_analysis = case["analysis_type"]
    actual_analysis = route.get("analysis_type")

    return {
        "analysis": actual_analysis == expected_analysis,
        "region": matches_region(case["region"], route.get("region")),
        "comparison": bool(route.get("pre_start")) == case["comparison"],
        "period": period_covers(route, case["period_contains"]),
        # A question the satellite cannot answer must be refused, not routed.
        "refusal": (expected_analysis is None) == (actual_analysis is None),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules", action="store_true", help="no model, ablation run")
    parser.add_argument("--model", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--heldout", action="store_true",
        help="score the held-out set. Do not tune against it.",
    )
    args = parser.parse_args()

    source = HELDOUT if args.heldout else BENCHMARK
    benchmark = json.loads(source.read_text(encoding="utf-8"))

    if args.heldout:
        print("=" * 68)
        print("HELD-OUT SET")
        print(benchmark["protocol"])
        print("=" * 68 + "\n")
    cases = benchmark["cases"][: args.limit]
    today = date.today()

    print(f"{benchmark['name']}  ·  {len(cases)} cases")
    print(f"Mode: {'rules only' if args.rules else args.model or routing.ROUTER_MODEL}\n")

    results = []
    by_category = defaultdict(lambda: {"total": 0, "correct": 0})
    failures = []

    for index, case in enumerate(cases, 1):
        try:
            route = routing.route(
                case["question"],
                model=args.model,
                today=today,
                prefer_model=not args.rules,
            )
        except Exception as exc:
            print(f"  {index:>2}. ERROR {case['question'][:50]}: {exc}")
            continue

        scores = score_case(case, route)
        results.append({"case": case, "route": route, "scores": scores})

        fully_correct = all(scores.values())
        by_category[case["category"]]["total"] += 1
        by_category[case["category"]]["correct"] += int(fully_correct)

        mark = "ok  " if fully_correct else "FAIL"
        print(f"  {index:>2}. [{mark}] {case['question'][:58]}")

        if not fully_correct:
            wrong = [k for k, v in scores.items() if not v]
            failures.append((case, route, wrong))
            print(
                f"           expected {case['analysis_type']} / {case['region']}"
                f"  ->  got {route.get('analysis_type')} / {route.get('region')}"
                f"   [{', '.join(wrong)}]"
            )

    if not results:
        print("\nNo results.")
        return

    print("\n" + "=" * 68)
    print("ACCURACY BY FIELD")
    print("=" * 68)
    for field in ("analysis", "region", "comparison", "period", "refusal"):
        correct = sum(r["scores"][field] for r in results)
        print(f"  {field:<14} {correct:>3}/{len(results)}   {correct / len(results):.1%}")

    exact = sum(all(r["scores"].values()) for r in results)
    print(f"\n  {'all fields':<14} {exact:>3}/{len(results)}   {exact / len(results):.1%}")

    print("\n" + "=" * 68)
    print("ACCURACY BY CATEGORY")
    print("=" * 68)
    for category, counts in sorted(
        by_category.items(), key=lambda kv: kv[1]["correct"] / max(kv[1]["total"], 1)
    ):
        rate = counts["correct"] / counts["total"]
        print(f"  {category:<34} {counts['correct']:>2}/{counts['total']}  {rate:.0%}")

    unresolvable = [
        r for r in results if r["case"].get("region_resolvable") is False
    ]
    if unresolvable:
        extracted = sum(r["scores"]["region"] for r in unresolvable)
        print(
            f"\n  places named but not in FAO GAUL 2015: {extracted}/{len(unresolvable)}"
            " extracted correctly"
        )
        print(
            "  (these should be read out of the question and then fail at the"
            " resolver, which is where the gazetteer lives)"
        )

    fallbacks = sum(r["route"].get("fallback_used", False) for r in results)
    disagreements = sum(
        r["route"].get("rules_agreed") is False
        for r in results
        if "rules_agreed" in r["route"]
    )
    print(f"\n  fell back to rules     {fallbacks}/{len(results)}")
    print(f"  rules/model disagreed  {disagreements}/{len(results)}")

    output = {
        "benchmark": benchmark["name"],
        "mode": "rules" if args.rules else (args.model or routing.ROUTER_MODEL),
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "cases_scored": len(results),
        "accuracy": {
            field: sum(r["scores"][field] for r in results) / len(results)
            for field in ("analysis", "region", "comparison", "period", "refusal")
        },
        "exact_match": exact / len(results),
        "fallbacks": fallbacks,
        "by_category": {
            k: v["correct"] / v["total"] for k, v in by_category.items()
        },
        "failures": [
            {
                "question": case["question"],
                "expected": {"analysis": case["analysis_type"], "region": case["region"]},
                "got": {
                    "analysis": route.get("analysis_type"),
                    "region": route.get("region"),
                },
                "wrong_fields": wrong,
            }
            for case, route, wrong in failures
        ],
    }

    if args.heldout:
        name = "routing_results_heldout.json"
    elif args.rules:
        name = "routing_results_rules.json"
    else:
        name = "routing_results.json"
    Path(name).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nSaved {name}")


if __name__ == "__main__":
    main()
