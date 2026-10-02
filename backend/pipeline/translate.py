"""
The verified report, in Hindi - and verified again after translation.

The problem: everything that makes a report trustworthy here - every number
traced to the evidence record, every caveat kept - is checked on English
text. A Hindi report written fresh by a model could change a number or drop a
caveat, and nothing would notice.

So Hindi is never written fresh. The English report is generated and
verified as before; THEN it is translated sentence by sentence, and the
translation is checked against the verified English:

    sentences   every English sentence comes back, once, in order (each is
                sent with a marker <<S1>>, <<S2>>... that must survive). The
                caveats are sentences of the verified English, so keeping
                every sentence keeps every caveat.
    numbers     each Hindi sentence holds exactly the numbers of its English
                sentence - Devanagari digits (०-९) read as digits, Indian
                digit grouping (1,25,100) read the same as 125,100.
    citations   each sentence keeps exactly its [E#] citations.
    terms       where the English names a core idea (flood, permanent water,
                observable area...), the Hindi uses the fixed term from the
                glossary - one word per idea, every time.

Fail any check and the Hindi is not shown; the reader gets the English and
the reason. A wrong Hindi report is worse than none. One retry is allowed,
told what failed.

Uses the Groq API, like the report itself. Not groq/compound: its web search
would put text in the translation that the English never said.
"""

import os
import re

from pipeline.report import LLMUnavailable, _strip_thinking

DEFAULT_MODEL = "openai/gpt-oss-120b"
MARKER = re.compile(r"<<S(\d+)>>")

# The fixed term list. Each English phrase, where it appears in a sentence,
# must come out as this Hindi term. A subset of the glossary in
# frontend/src/lib/glossary.js - a test holds the two to the same words.
TERMS = {
    "flood": "बाढ़",
    "permanent water": "स्थायी जल",
    "observable area": "अवलोकित क्षेत्र",
    "baseline": "आधार अवधि",
    "population model": "जनसंख्या मॉडल",
    "precision": "परिशुद्धता",
    "recall": "रिकॉल",
}

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
NUMBER = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")
CITATION = re.compile(r"\[(E\d+(?:\s*,\s*E\d+)*)\]")

SYSTEM_PROMPT = """You translate verified English reports about satellite flood mapping into clear, formal Hindi (Devanagari) for Indian district officials.

RULES - each is checked automatically, and a translation that breaks one is discarded:
1. Each input line starts with a marker like <<S3>>. Output exactly one line per input line, starting with the SAME marker, in the same order. Never merge, split, add or drop sentences.
2. Copy every number exactly as written in the English, in Western digits (0-9), with the same decimal point. Do not round, convert units, or turn words into digits or digits into words.
3. Keep every citation such as [E1] or [E2, E3] exactly as written, in the same sentence.
4. Keep units such as km2 and % as written.
5. Use these fixed Hindi terms for these ideas, every time:
{terms}
6. Translate only. Add nothing, explain nothing, omit nothing."""


# ----------------------------------------------------------- pure checking

def split_sentences(text):
    """Sentences of the English report. Splits after . ! ? followed by a space
    and an upper-case letter or a bracket, so decimals (0.609), coordinates
    (10.05 N) and citations stay inside their sentence."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\[(\"'])", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def numbers_in(text):
    """Sorted numbers in a text, Devanagari digits and digit grouping normalised."""
    plain = CITATION.sub(" ", (text or "").translate(DEVANAGARI_DIGITS))
    found = []
    for token in NUMBER.findall(plain):
        token = token.replace(",", "").lstrip("+")
        try:
            found.append(float(token))
        except ValueError:
            continue
    return sorted(found)


def citations_in(text):
    ids = []
    for group in CITATION.findall(text or ""):
        ids.extend(i.strip() for i in group.split(","))
    return sorted(ids)


def marked_source(sentences):
    return "\n".join(f"<<S{i}>> {s}" for i, s in enumerate(sentences, 1))


def parse_marked(text):
    """{index: sentence} from the model's marked lines; repeats are kept as a
    list so a duplicated marker is caught rather than overwritten."""
    out = {}
    pieces = MARKER.split(text or "")
    # pieces: [before, idx, body, idx, body, ...]
    for i in range(1, len(pieces) - 1, 2):
        out.setdefault(int(pieces[i]), []).append(pieces[i + 1].strip())
    return out


def check(english_sentences, hindi_by_index, terms=TERMS):
    """Every failure, in words. An empty list means the translation passes."""
    failures = []
    expected = set(range(1, len(english_sentences) + 1))
    got = set(hindi_by_index)
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    if missing:
        failures.append(f"sentences missing from the translation: {missing}")
    if extra:
        failures.append(f"sentences that were not in the English: {extra}")
    for index in sorted(expected & got):
        versions = hindi_by_index[index]
        if len(versions) > 1:
            failures.append(f"sentence {index} appears {len(versions)} times")
            continue
        english, hindi = english_sentences[index - 1], versions[0]
        if not re.search(r"[ऀ-ॿ]", hindi):
            failures.append(f"sentence {index} is not in Hindi")
        if numbers_in(english) != numbers_in(hindi):
            failures.append(f"sentence {index}: numbers {numbers_in(english)} became "
                            f"{numbers_in(hindi)}")
        if citations_in(english) != citations_in(hindi):
            failures.append(f"sentence {index}: citations {citations_in(english)} became "
                            f"{citations_in(hindi)}")
        lowered = english.lower()
        for phrase, hindi_term in terms.items():
            if re.search(rf"\b{re.escape(phrase)}", lowered) and hindi_term not in hindi:
                failures.append(f"sentence {index}: '{phrase}' should be '{hindi_term}'")
    return failures


def assemble(hindi_by_index, count):
    return " ".join(hindi_by_index[i][0] for i in range(1, count + 1))


# ------------------------------------------------------------------- model

def _call(client, model, system, user):
    request = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.1,
        "max_tokens": 3000,
    }
    if "gpt-oss" in model:
        request["reasoning_effort"] = "low"
    try:
        response = client.chat.completions.create(**request)
    except Exception as exc:                     # noqa: BLE001 - surfaced as LLMUnavailable
        if "reasoning_effort" in request and "reasoning_effort" in str(exc):
            request.pop("reasoning_effort")
            try:
                response = client.chat.completions.create(**request)
            except Exception as retry_exc:       # noqa: BLE001
                raise LLMUnavailable(f"Groq request failed: {retry_exc}") from retry_exc
        else:
            raise LLMUnavailable(f"Groq request failed: {exc}") from exc
    message = response.choices[0].message
    text = (getattr(message, "content", None) or "").strip()
    return _strip_thinking(text)


def _client(timeout):
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise LLMUnavailable("GROQ_API_KEY is not set.")
    try:
        from groq import Groq
    except ImportError as exc:
        raise LLMUnavailable("groq package not installed.") from exc
    return Groq(api_key=api_key, timeout=timeout)


def to_hindi(english_text, client=None, model=None, timeout=40, attempts=2):
    """{"available", "text" | "reason", "checks", "model", "attempts"}."""
    model = model or os.environ.get("TRANSLATE_MODEL", DEFAULT_MODEL)
    if "compound" in model:
        raise ValueError("groq/compound searches the web; it cannot be used for a "
                         "translation that must say only what the English says.")
    sentences = split_sentences(english_text)
    if not sentences:
        return {"available": False, "reason": "There is no English report to translate.",
                "checks": [], "model": None, "attempts": 0}

    terms = "\n".join(f"   {en} = {hi}" for en, hi in TERMS.items())
    system = SYSTEM_PROMPT.format(terms=terms)
    user = marked_source(sentences)
    client = client or _client(timeout)

    failures = []
    for attempt in range(1, attempts + 1):
        prompt = user if not failures else (
            user + "\n\nYour previous translation was rejected because:\n- "
            + "\n- ".join(failures[:8]) + "\nTranslate again, following every rule.")
        raw = _call(client, model, system, prompt)
        parsed = parse_marked(raw)
        failures = check(sentences, parsed)
        if not failures:
            return {"available": True, "text": assemble(parsed, len(sentences)),
                    "sentences": len(sentences), "checks": [], "model": model,
                    "attempts": attempt}
    return {"available": False, "checks": failures, "model": model, "attempts": attempts,
            "reason": ("The Hindi translation did not pass verification, so it is not "
                       "shown: " + "; ".join(failures[:3]))}
