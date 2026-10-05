"""
The password rules, in one place, so the sign-up form, the change-password
form and the reset form all refuse the same things for the same reasons.

    at least MIN_LENGTH characters
    a capital letter, a small letter, a number and a symbol
    not a commonly used password - checked on the password itself AND on its
    "base word" (letters only, lower-cased), so "Password@123" and
    "India@2026" are refused even though they satisfy every character rule
    not containing the person's name, username or the name part of their email

The character rules are what people expect; the common-password check is
what actually stops guessable passwords (it is what current guidance, NIST
SP 800-63B, recommends). Existing accounts keep working; the rules apply
whenever a password is set.
"""

import re

MIN_LENGTH = 8
MAX_LENGTH = 128

RULES = (
    ("length", f"At least {MIN_LENGTH} characters"),
    ("upper", "A capital letter (A-Z)"),
    ("lower", "A small letter (a-z)"),
    ("digit", "A number (0-9)"),
    ("symbol", "A symbol, such as ! @ # $ %"),
)

# The most-used passwords and base words from public breach lists, plus the
# local favourites (Indian place names, cricket, festivals). Checked after
# lower-casing and after stripping digits and symbols.
COMMON = {
    "password", "passw0rd", "pass", "admin", "administrator", "welcome", "letmein", "qwerty",
    "qwertyuiop", "asdfgh", "asdfghjkl", "zxcvbnm", "iloveyou", "love", "lover", "monkey", "dragon",
    "master", "login", "abc", "abcd", "abcdef", "abcdefg", "abcdefgh", "abc123", "test", "tester",
    "testing", "user", "guest", "secret", "sunshine", "princess", "football", "baseball", "soccer",
    "superman", "batman", "starwars", "shadow", "michael", "jordan", "trustno", "hello", "freedom",
    "whatever", "computer", "internet", "samsung", "google", "apple", "iphone", "android", "nokia",
    "changeme", "default", "root", "toor", "system", "server", "database", "flower", "summer",
    "winter", "spring", "autumn", "monday", "friday", "sunday", "january", "december", "angel",
    "baby", "family", "friends", "forever", "happy", "lucky", "money", "number", "pokemon",
    "naruto", "killer", "hunter", "ranger", "tigger", "chocolate", "cookie", "cheese", "pepper",
    "ginger", "orange", "banana", "purple", "silver", "golden", "diamond", "india", "bharat",
    "hindustan", "jaihind", "mumbai", "delhi", "newdelhi", "kolkata", "chennai", "bangalore",
    "bengaluru", "hyderabad", "pune", "ahmedabad", "kerala", "assam", "bihar", "gujarat", "punjab",
    "cricket", "sachin", "tendulkar", "dhoni", "virat", "kohli", "rohit", "ipl", "bollywood",
    "krishna", "ganesh", "shiva", "jaishreeram", "omnamahshivaya", "radhe", "radha", "sairam",
    "diwali", "holi", "rahul", "priya", "pooja", "amit", "sanjay", "deepak", "sunil", "anil",
    "rajesh", "ramesh", "suresh", "mahesh", "kumar", "singh", "sharma", "gupta", "patel",
    "flood", "floods", "satellite", "antardrishti", "sentinel", "earth", "nogixx", "parul",
    "university", "college", "student", "project", "india123", "qwerty123", "password123",
    "welcome123", "admin123", "iloveindia", "mylove", "mypassword", "letmein123",
}

_SEQUENCES = ("0123456789", "abcdefghijklmnopqrstuvwxyz", "qwertyuiop", "asdfghjkl", "zxcvbnm")


def checks(password):
    """{rule: passed} for the live checklist."""
    p = password or ""
    return {
        "length": len(p) >= MIN_LENGTH,
        "upper": bool(re.search(r"[A-Z]", p)),
        "lower": bool(re.search(r"[a-z]", p)),
        "digit": bool(re.search(r"\d", p)),
        "symbol": bool(re.search(r"[^A-Za-z0-9\s]", p)),
    }


def base_word(password):
    return re.sub(r"[^a-z]", "", (password or "").lower())


def is_common(password):
    lowered = (password or "").lower()
    base = base_word(password)
    if lowered in COMMON or base in COMMON:
        return True
    if len(set(lowered)) <= 2:
        return True                      # "aaaaaaaa", "abababab"
    return any(len(base) >= 5 and base in seq for seq in _SEQUENCES)


def problem(password, personal=()):
    """The first reason this password is refused, in words, or None.

    `personal` holds the person's name, username and email: a password made
    from them is the first thing someone who knows them would try.
    """
    if not isinstance(password, str) or not password:
        return "Choose a password."
    if len(password) > MAX_LENGTH:
        return f"Keep the password under {MAX_LENGTH} characters."
    passed = checks(password)
    for key, label in RULES:
        if not passed[key]:
            return f"The password needs {label[0].lower() + label[1:]}."
    if is_common(password):
        return "That password is too common and easy to guess. Try a short sentence with a number and a symbol."
    lowered = password.lower()
    for item in personal:
        for part in re.split(r"[\s@._-]+", str(item or "").lower()):
            if len(part) >= 3 and part in lowered:
                return "The password must not contain your name, username or email."
    return None
