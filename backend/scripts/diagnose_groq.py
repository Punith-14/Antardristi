"""
Why did the model return nothing? Prints the raw response structure.

Run:  python -m scripts.diagnose_groq
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()

from groq import Groq

CANDIDATES = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
]

PROMPT = "Say exactly: The measured area is 187.4 km2 [E1]."


def main():
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    print("Available models:")
    for m in sorted(x.id for x in client.models.list().data):
        print("   ", m)

    for model in CANDIDATES:
        print("\n" + "=" * 60)
        print(model)
        print("=" * 60)

        for label, extra in [
            ("plain", {}),
            ("reasoning_effort=low", {"reasoning_effort": "low"}),
        ]:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": PROMPT}],
                    temperature=0.2,
                    max_tokens=1200,
                    **extra,
                )
            except Exception as exc:
                print(f"  [{label}] request failed: {type(exc).__name__}: {exc}")
                continue

            choice = response.choices[0]
            message = choice.message
            content = getattr(message, "content", None)
            reasoning = getattr(message, "reasoning", None)

            print(f"  [{label}]")
            print(f"     finish_reason : {choice.finish_reason}")
            print(f"     content       : {content!r}")
            if reasoning:
                print(f"     reasoning     : {reasoning[:200]!r}")
            usage = getattr(response, "usage", None)
            if usage:
                print(
                    f"     tokens        : prompt={usage.prompt_tokens} "
                    f"completion={usage.completion_tokens}"
                )
            print(f"     message keys  : {list(message.model_dump().keys())}")


if __name__ == "__main__":
    main()
