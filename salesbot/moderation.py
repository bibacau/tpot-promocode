"""Deterministic moderation (no AI involved).

Design adapted from teddytravolta's moderation.js (MIT): the same filter is
applied to incoming comments, to the AI's output and to usernames, because
all three can end up being spoken aloud on stream. Word lists rewritten for
Malaysian English / Bahasa Melayu, plus personal-data checks.
"""
from __future__ import annotations

import re
import unicodedata

URL_RE = re.compile(
    r"(https?://|www\.|\b[\w-]+\.(com|net|org|io|my|xyz|shop|link|me|tv|app|gg|site|online|store|ly)\b)",
    re.I,
)
PHONE_RE = re.compile(r"(\+?6?0\s?1\d[\s-]?\d{3,4}[\s-]?\d{3,4})|(\b\d{9,12}\b)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

SPAM_RE = re.compile(
    "|".join([
        r"\bfollow\s+(me|back|my)\b",
        r"\bcheck\s+(out\s+)?my\s+(profile|page|bio)",
        r"\blink\s+(in|kat|dalam)\s+bio",
        r"\bwh?ats\s*app\b", r"\bwasap\b", r"\btelegram\b",
        r"\bfree\s+(followers|likes|coins)",
        r"\b(buy|beli|jual)\s+(followers|likes|views)",
        r"\bsub\s*4\s*sub\b", r"\bf4f\b",
        r"\bdm\s+(me|saya)\b",
        r"\b(crypto|forex|bitcoin)\s+(signal|invest)",
        r"\bjudi\b", r"\bslot\s*online\b",
    ]),
    re.I,
)

# Run against normalised text (lowercase, no accents, leetspeak undone).
PROFANITY_RE = re.compile(
    "|".join([
        # English
        r"\bfuck\w*", r"\bshit\w*", r"\bbitch\w*", r"\bcunt\w*", r"\basshole\w*",
        r"\bbastard\w*", r"\bdick(head)?s?\b", r"\bslut\w*", r"\bwhore\w*",
        r"\bretard\w*", r"\bnigg\w*", r"\bwtf\b", r"\bstfu\b",
        # Bahasa Melayu
        r"\bbabi\b", r"\bbodoh\w*", r"\bsial\b", r"\bpuki\w*", r"\bpukimak\b",
        r"\blancau\b", r"\bcelaka\w*", r"\bbangang\b", r"\bpantat\b", r"\bsundal\b",
        r"\bkeparat\b", r"\bkimak\b", r"\bbarua\b", r"\bjubur\b", r"\bkote\b",
        r"\bpundek\b", r"\bbabun?\b",
        # Common Hokkien/Cantonese slang used in Malaysia
        r"\bkanina\w*", r"\bknn\b", r"\bcibai\b", r"\bchibai\b", r"\bpok\s*kai\b",
    ]),
    re.I,
)

LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().translate(LEET)
    text = re.sub(r"[*_.\-]+(?=\w)", "", text)        # f*u*c*k, s.h.i.t
    text = re.sub(r"(\w)\1{2,}", r"\1\1", text)        # fuuuuck -> fuuck
    return text


def check(text: str) -> str | None:
    """Return a block reason, or None if the text is OK to speak."""
    if not text or not text.strip():
        return "empty"
    if EMAIL_RE.search(text) or PHONE_RE.search(text):
        return "personal_data"
    if URL_RE.search(text):
        return "link"
    norm = normalise(text)
    if SPAM_RE.search(text) or SPAM_RE.search(norm):
        return "spam"
    if PROFANITY_RE.search(norm):
        return "profanity"
    if re.search(r"(.)\1{9,}", text):
        return "spam"
    return None


def safe_username(name: str, fallback: str = "friend") -> str:
    """Usernames are free text — never read out an offensive or link-like one."""
    name = re.sub(r"[\r\n\t]", " ", (name or "")).strip()[:24]
    if not name or check(name) is not None or check(name.replace("_", " ")) is not None:
        return fallback
    return name


def clean_comment(text: str, max_len: int = 200) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:max_len]
