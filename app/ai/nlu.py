"""Deterministic natural-language understanding.

This is the primary parser: it's fast, free, offline and fully testable. The LLM
(see `llm.py`) is only consulted when these rules can't understand a message.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.ai.lexicon import (
    CODE_RE,
    MONTHS,
    NAME_PREFIX_RE,
    PHONE_RE,
    SPECIALTY_KEYWORDS,
    TIME_OF_DAY,
    WEEKDAYS,
)
from app.domain.models import Doctor


class Intent(StrEnum):
    BOOK = "book"
    RESCHEDULE = "reschedule"
    CANCEL = "cancel"
    MY_BOOKINGS = "my_bookings"
    DOCTORS = "doctors"
    HELP = "help"
    GREET = "greet"


class TimeRequest(BaseModel):
    """A requested time. `approximate` means "morning"-style: pick the nearest free slot."""

    model_config = ConfigDict(frozen=True)

    hhmm: str
    approximate: bool = False


# ------------------------------------------------------------------ intent


def detect_intent(text: str) -> Intent | None:
    t = text.lower()
    if re.search(
        r"\b(re-?schedul\w*|postpone|prepone|change (?:my |the )?(?:appointment|booking|time|date|slot)"
        r"|move (?:my |the )?(?:appointment|booking))\b|మార్చాలి|మార్చండి|మార్చుకోవాలి",
        t,
    ):
        return Intent.RESCHEDULE
    if CODE_RE.search(text) or re.search(r"\bcancel\b|క్యాన్సిల్|రద్దు", t):
        return Intent.CANCEL
    if re.search(r"my (booking|appointment)s?|\bstatus\b|నా బుకింగ్", t):
        return Intent.MY_BOOKINGS
    if re.search(r"\b(doctors?|list)\b|డాక్టర్లు", t) and not re.search(r"\bbook\b", t):
        return Intent.DOCTORS
    if re.search(r"\bhelp\b|సహాయం", t):
        return Intent.HELP
    if re.search(r"\b(book|appointment|consult|visit|see a|slot)\b|బుక్|అపాయింట్", t):
        return Intent.BOOK
    if re.fullmatch(r"\s*(hi|hello|hey|namaste|నమస్తే|హాయ్)\W*", t):
        return Intent.GREET
    return None


# -------------------------------------------------------------------- date


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _roll_forward(d: date | None, today: date, explicit_year: bool) -> date | None:
    """'5 Jan' said in December means next January."""
    if d and not explicit_year and d < today:
        return _safe_date(d.year + 1, d.month, d.day)
    return d


def parse_date(text: str, today: date) -> date | None:
    t = text.lower()
    if "day after tomorrow" in t or "ఎల్లుండి" in t or re.search(r"\b(parso|ellundi)\b", t):
        return today + timedelta(days=2)
    if "tomorrow" in t or "రేపు" in t or re.search(r"\b(kal|repu|reypu)\b", t):
        return today + timedelta(days=1)
    if "today" in t or "ఈరోజు" in t or "ఈ రోజు" in t or re.search(r"\baaj\b", t):
        return today

    if m := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))

    if m := re.search(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b", t):  # day-first, Indian style
        year = int(m[3]) if m[3] else today.year
        year += 2000 if year < 100 else 0
        return _roll_forward(_safe_date(year, int(m[2]), int(m[1])), today, bool(m[3]))

    mon = "|".join(MONTHS)
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({mon})[a-z]*\b", t) or re.search(
        rf"\b({mon})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", t
    )
    if m:
        a, b = m.groups()
        day_n, mon_s = (int(a), b) if a.isdigit() else (int(b), a)
        return _roll_forward(_safe_date(today.year, MONTHS.index(mon_s[:3]) + 1, day_n), today, False)

    for i, wd in enumerate(WEEKDAYS):
        if re.search(rf"\b({wd}|{wd[:3]})\b", t):
            ahead = (i - today.weekday()) % 7 or 7
            if "this" in t and i == today.weekday():
                ahead = 0
            return today + timedelta(days=ahead)
    return None


# -------------------------------------------------------------------- time


def parse_time(text: str, *, loose: bool = False) -> TimeRequest | None:
    """Parse a time of day. `loose` accepts a bare number ("11") when we've just asked for a time."""
    t = text.lower().replace(".", ":")
    if m := re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a m|p m)\b", t):
        h, mi = int(m[1]) % 12, int(m[2] or 0)
        if m[3].replace(" ", "") == "pm":
            h += 12
        return TimeRequest(hhmm=f"{h:02d}:{mi:02d}") if h < 24 and mi < 60 else None

    m = re.search(r"\b(\d{1,2}):(\d{2})\b", t)
    if m and int(m[1]) < 24 and int(m[2]) < 60:
        return TimeRequest(hhmm=f"{int(m[1]):02d}:{m[2]}")

    m = (
        re.search(r"\b(?:at|@)\s*(\d{1,2})\b", t)
        or re.search(r"\b(\d{1,2})\s*(?:baje|gantalaki)\b", t)  # Hindi / Telugu "o'clock"
        or (re.fullmatch(r"\s*(\d{1,2})\s*", t) if loose else None)
    )
    if m:
        h = int(m[1])
        if 1 <= h <= 7:  # within clinic hours, "at 4" means 4 pm
            h += 12
        return TimeRequest(hhmm=f"{h:02d}:00") if h < 24 else None

    for word, hhmm in TIME_OF_DAY.items():
        if word in t:
            return TimeRequest(hhmm=hhmm, approximate=True)
    return None


# ------------------------------------------------------------------ doctor


def _has_keyword(text: str, word: str) -> bool:
    # Word-boundary prefix match for Latin keywords ("son" must not match "person");
    # plain substring for Telugu, where \b is unreliable around combining marks.
    if word.isascii():
        return re.search(rf"\b{re.escape(word)}", text) is not None
    return word in text


def match_doctor(text: str, doctors: Sequence[Doctor], *, allow_number: bool = False) -> Doctor | None:
    t = text.lower().strip()
    if allow_number and re.fullmatch(r"\d{1,2}", t):
        i = int(t) - 1
        return doctors[i] if 0 <= i < len(doctors) else None
    for d in doctors:
        parts = [p for p in d.name.lower().replace("dr.", "").split() if len(p) > 2]
        if any(re.search(rf"\b{re.escape(p)}\b", t) for p in parts):
            return d
    for d in doctors:
        if d.specialty.lower() in t:
            return d
    for specialty, words in SPECIALTY_KEYWORDS.items():
        if any(_has_keyword(t, w) for w in words):
            for d in doctors:
                if d.specialty == specialty:
                    return d
    return None


# ----------------------------------------------------------------- entities


def find_code(text: str) -> str | None:
    m = CODE_RE.search(text)
    return m[0].upper() if m else None


def find_phone(text: str) -> str | None:
    m = PHONE_RE.search(text)
    return m[0] if m else None


def extract_name(text: str) -> str | None:
    name = NAME_PREFIX_RE.sub("", PHONE_RE.sub("", text)).strip(" .,")
    if len(name) < 2:
        return None
    return name[:60].title() if name.isascii() else name[:60]
