"""Vocabulary for the rule-based parser (English, Telugu, and common Hinglish/Tenglish)."""

from __future__ import annotations

import re

# Order matters: the first specialty with a matching keyword wins. Pediatrics comes
# before general medicine so "my son has fever" goes to the child specialist.
SPECIALTY_KEYWORDS: dict[str, list[str]] = {
    "Pediatrician": [
        "child",
        "children",
        "kid",
        "baby",
        "son",
        "daughter",
        "pediatric",
        "paediatric",
        "పిల్ల",
        "బాబు",
        "పాప",
    ],
    "Gynecologist": ["pregnan", "gyn", "period", "lady doctor", "women", "గర్భ", "పీరియడ్"],
    "Dentist": [
        "tooth",
        "teeth",
        "dental",
        "dentist",
        "gum",
        "cavity",
        "root canal",
        "pannu",
        "pantlu",
        "panti",
        "పన్ను",
        "పంటి",
        "పళ్ళ",
    ],
    "Dermatologist": ["skin", "acne", "pimple", "hair", "rash", "derma", "చర్మ", "జుట్టు", "మొటిమ"],
    "General Physician": [
        "general",
        "physician",
        "fever",
        "cold",
        "cough",
        "bp",
        "sugar",
        "diabetes",
        "headache",
        "body pain",
        "జ్వరం",
        "జలుబు",
        "దగ్గు",
        "తలనొప్పి",
    ],
}

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

YES = {"yes", "y", "ok", "okay", "confirm", "sure", "haan", "ha", "avunu", "sare", "సరే", "అవును", "హా", "book it"}
NO = {"no", "n", "nope", "nahi", "vaddu", "వద్దు", "stop"}

TELUGU_RE = re.compile(r"[ఀ-౿]")
CODE_RE = re.compile(r"\bCB[0-9A-F]{6}\b", re.I)
PHONE_RE = re.compile(r"(\+?\d[\d\s-]{8,14}\d)")
NAME_PREFIX_RE = re.compile(r"^(my name is|i am|i'm|name is|naa peru|నా పేరు)\s*", re.I)

TIME_OF_DAY = {"morning": "10:00", "afternoon": "14:00", "evening": "16:30"}
