"""Checks every Gemma draft before the creator sees it. Used by the app and by evaluate_messages.py.

A draft fails if it:
  length    isn't 2 to 4 sentences
  emoji     uses more than one emoji
  hashtags  has hashtags (they belong in posts, not WhatsApp messages)
  price     mentions a ₹ amount that wasn't given
  time      mentions a clock time that wasn't given
  private   copies 4+ words in a row from the private session notes
  language  is in the wrong language or script
"""
import re

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐❤]")
PRICE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+)", re.I)
CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(?:am|pm|baje|बजे)", re.I)
HINDI_WORDS = {"hai", "hain", "ho", "hoon", "ka", "ki", "ke", "ko", "se", "mein", "aap", "tum", "kar", "karo", "kya",
               "bhi", "toh", "phir", "milte", "aapka", "aapki", "tumhara", "accha", "acha", "zaroor", "shukriya",
               "dhanyavaad", "baje", "aur", "yaad", "rakhna", "kuch", "ji", "na", "pe", "par", "bas"}
STOP = {"the", "a", "an", "and", "of", "to", "in", "on", "at", "for", "about", "with", "is", "was", "her", "his", "my"}

RULE_TEXT = {
    "length": "Use 2 to 4 short sentences.",
    "emoji": "Use at most one emoji.",
    "hashtags": "No hashtags.",
    "price": "Don't mention any price that wasn't given.",
    "time": "Don't mention any time that wasn't given.",
    "private": "Don't repeat the session notes word for word; refer to them gently.",
    "language": "Write in the requested language and script.",
}


def sentences(t: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?।])\s+|\n+", t.strip()) if re.search(r"\w", s)])


def copies_notes(draft: str, notes: str, n: int = 4) -> bool:
    """True if the draft repeats n+ consecutive words from the private notes (ignoring tiny filler-only runs)."""
    w = re.findall(r"[a-z']+", (notes or "").lower())
    d = " " + " ".join(re.findall(r"[a-z']+", draft.lower())) + " "
    for i in range(len(w) - n + 1):
        gram = w[i:i + n]
        if len([x for x in gram if x not in STOP]) >= 2 and f" {' '.join(gram)} " in d:
            return True
    return False


def check(draft: str, language: str, given: str, notes: str = "") -> list[str]:
    """Names of the rules this draft breaks (empty list = good)."""
    fails = []
    n = sentences(draft)
    if not 2 <= n <= 4:
        fails.append("length")
    if len(EMOJI.findall(draft)) > 1:
        fails.append("emoji")
    if "#" in draft:
        fails.append("hashtags")
    allowed = {p.replace(",", "") for p in PRICE.findall(given)}
    if any(p.replace(",", "") not in allowed for p in PRICE.findall(draft)):
        fails.append("price")
    hours = {m[0] for m in CLOCK.findall(given)}
    if any(m[0] not in hours for m in CLOCK.findall(draft)):
        fails.append("time")
    if notes and copies_notes(draft, notes):
        fails.append("private")
    low = draft.lower()
    deva = len(re.findall(r"[ऀ-ॿ]", draft))
    latin = len(re.findall(r"[A-Za-z]", draft))
    words = set(re.findall(r"[a-z]+", low))
    if language == "Hindi" and deva < latin:
        fails.append("language")
    if language == "Hinglish" and (deva > 0 or len(words & HINDI_WORDS) < 2):
        fails.append("language")
    if language == "English" and (deva > 0 or len(words & (HINDI_WORDS - {"na", "pe", "par", "ji", "bas"})) >= 3):
        fails.append("language")
    return fails


def fix_request(fails: list[str]) -> str:
    return "Your draft broke these rules: " + " ".join(RULE_TEXT[f] for f in fails) + " Rewrite it. Output only the message."


def generate_checked(generate, system: str, user: str, language: str, notes: str = ""):
    """Draft, check, and retry once with the broken rules spelled out. Returns (text, fails_after, retried)."""
    text = generate(system, user)
    fails = check(text, language, user, notes)
    if not fails:
        return text, [], False
    retry = generate(system, user + "\n\nPrevious draft:\n" + text + "\n\n" + fix_request(fails))
    fails2 = check(retry, language, user, notes)
    if len(fails2) <= len(fails):
        return retry, fails2, True
    return text, fails, True
