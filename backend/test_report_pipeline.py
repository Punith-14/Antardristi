"""
End-to-end check of the report pipeline against the contract fixture.

Run:  python test_report_pipeline.py

Uses no Earth Engine quota. The LLM step is skipped unless GROQ_API_KEY is set.
"""

import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from report import (
    build_report,
    collect_extra_values,
    generate_llm_report,
    render_template_report,
    LLMUnavailable,
)
from verification import summarise, verify_report

FIXTURE = (
    Path(__file__).resolve().parent.parent / "contracts" / "analysis_response.json"
)


def line(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def main():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    evidence = payload["evidence"]
    # Same helper the production path uses, so the test measures what ships.
    extra = collect_extra_values(payload)

    # ------------------------------------------------ 1. template generator
    line("1. TEMPLATE GENERATOR (no model)")
    template_text = render_template_report(payload)
    print(template_text)
    result = verify_report(template_text, evidence, extra_values=extra)
    print(f"\n  faithfulness: {result['faithfulness_rate']:.2%}  ->  {summarise(result)}")

    # ------------------------------- 2. the contract's own example report
    line("2. CONTRACT EXAMPLE REPORT")
    example = payload["report"]["text"]
    print(example)
    result = verify_report(example, evidence, extra_values=extra)
    print(f"\n  faithfulness: {result['faithfulness_rate']:.2%}  ->  {summarise(result)}")

    # ------------------------------------------ 3. deliberate hallucination
    line("3. HALLUCINATED REPORT (the verifier must catch this)")
    bad = (
        "Flooding affected 187.4 km2 of Kendrapara district [E1], displacing "
        "approximately 45000 residents and submerging 312 villages. Water depth "
        "reached 3.5 metres in the worst areas. The district recorded 22 "
        "casualties [E9]."
    )
    print(bad)
    result = verify_report(bad, evidence, extra_values=extra)
    print(f"\n  faithfulness: {result['faithfulness_rate']:.2%}")
    print(f"  {summarise(result)}")
    for claim in result["unsupported_claims"]:
        print(f"    FLAGGED: '{claim['claim']}' has no supporting evidence")
    if result["invalid_citations"]:
        print(f"    FLAGGED: citations do not exist: {result['invalid_citations']}")

    assert not result["passed"], "verifier failed to catch a hallucinated report"

    # ------------------------------------------------ 4. no-imagery fallback
    line("4. NO USABLE IMAGERY")
    empty = {
        "region": {"name": "Kendrapara"},
        "evidence": [],
        "observation": {},
        "unobserved": {"reason": "no_usable_imagery"},
        "zones": [],
    }
    print(render_template_report(empty))

    # ---------------------------------------------------- 5. llm generator
    line("5. LLM GENERATOR (Groq)")
    try:
        text, model = generate_llm_report(payload)
        print(f"model: {model}\n")
        print(text)
        result = verify_report(text, evidence, extra_values=extra)
        print(f"\n  faithfulness: {result['faithfulness_rate']:.2%}  ->  {summarise(result)}")
        if result["unsupported_claims"]:
            for claim in result["unsupported_claims"]:
                print(f"    UNSUPPORTED: '{claim['claim']}'")
    except LLMUnavailable as exc:
        print(f"skipped: {exc}")

    # -------------------------------------------------- 6. full facade path
    line("6. build_report() FACADE")
    report, verification = build_report(payload)
    print(f"fallback_used : {report['fallback_used']}")
    print(f"reason        : {report['fallback_reason']}")
    print(f"model         : {report['generator_model']}")
    print(f"faithfulness  : {verification['faithfulness_rate']:.2%}")
    print(f"\n{report['text']}")

    print("\n" + "=" * 68)
    print("All checks completed.")


if __name__ == "__main__":
    main()
