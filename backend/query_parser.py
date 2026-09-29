from datetime import datetime


MODE_KEYWORDS = {
    "disaster": ["flood", "water", "disaster", "inundation", "flooded"],
    "agriculture": ["crop", "agriculture", "vegetation", "ndvi", "farm"],
    "urban": ["urban", "city", "land use", "expansion", "built-up"],
}


def detect_mode(question, preferred_mode=None):
    if preferred_mode in MODE_KEYWORDS:
        return preferred_mode

    query = (question or "").lower()
    for mode, keywords in MODE_KEYWORDS.items():
        if any(keyword in query for keyword in keywords):
            return mode

    return "disaster"


def detect_analysis(question, mode):
    query = (question or "").lower()
    if "compare" in query or "change" in query or "before" in query or "after" in query:
        return "change-detection"
    if mode == "agriculture":
        return "vegetation-water-screening"
    if mode == "urban":
        return "urban-water-screening"
    return "flood-screening"


def parse_query(question, preferred_mode=None):
    mode = detect_mode(question, preferred_mode)
    analysis = detect_analysis(question, mode)
    return {
        "intent": "satellite-analysis",
        "mode": mode,
        "analysis": analysis,
        "question": question.strip() if question else "",
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
