"""Red-flag detection: messages that need emergency care, not an appointment.

This runs before anything else on every turn. It is deliberately keyword-based:
it has to work offline, in milliseconds, and be auditable line by line. A missed
emergency is far worse than a false alarm, so the patterns lean towards recall, and
the eval set (`evals/datasets/safety.jsonl`) gates both directions in CI.
"""

from __future__ import annotations

import re
from enum import StrEnum


class RedFlag(StrEnum):
    EMERGENCY = "emergency"
    SELF_HARM = "self_harm"


# (category, patterns). Latin patterns match on word boundaries; Telugu as substrings.
_PATTERNS: list[tuple[RedFlag, list[str]]] = [
    (
        RedFlag.SELF_HARM,
        [
            r"suicid\w*",
            r"kill (?:my ?self|myself)",
            r"end (?:my|this) life",
            r"want to die",
            r"don'?t want to live",
            r"self[- ]?harm",
            r"hurt(?:ing)? myself",
            r"cut(?:ting)? myself",
            "ఆత్మహత్య",
            "చనిపోవాలని",
        ],
    ),
    (
        RedFlag.EMERGENCY,
        [
            # heart
            r"chest pain",
            r"pain in (?:my |the )?chest",
            r"heart attack",
            r"chest (?:tightness|pressure)",
            r"gunde ?noppi",
            "ఛాతీ నొప్పి",
            "గుండె నొప్పి",
            "గుండెపోటు",
            # breathing
            r"can'?t breathe?",
            r"cannot breathe?",
            r"unable to breathe?",
            r"not able to breathe?",
            r"(?:difficulty|trouble|struggling) breathing",
            r"short(?:ness)? of breath",
            r"breathless",
            r"choking",
            "ఊపిరి ఆడటం లేదు",
            "ఊపిరి ఆడట్లేదు",
            "శ్వాస ఆడటం లేదు",
            # stroke / consciousness / seizure
            r"stroke",
            r"face (?:is )?drooping",
            r"slurred speech",
            r"sudden(?:ly)? (?:numb|weak|paraly)\w*",
            r"paraly[sz]\w*",
            r"unconscious",
            r"not responding",
            r"unresponsive",
            r"passed out",
            r"fainted",
            r"collapsed",
            r"seizures?",
            r"convuls\w*",
            r"having fits",
            r"fits attack",
            "స్పృహ కోల్పో",
            "మూర్ఛ",
            # bleeding / trauma / poisoning
            r"(?:heavy|severe|lot of|lots of) bleeding",
            r"bleeding (?:heavily|a lot|badly|non ?stop)",
            r"won'?t stop bleeding",
            r"vomiting blood",
            r"cough(?:ing)? (?:up )?blood",
            r"blood in vomit",
            r"head injury",
            r"(?:serious|bad|road) accident",
            r"severe burns?",
            r"poison\w*",
            r"overdose",
            r"drank (?:pesticide|poison|acid|phenyl\w*)",
            r"snake ?bite",
            r"dog bite.*bleeding",
            "రక్తస్రావం",
            "విషం",
            "పాము కాటు",
            "ప్రమాదం జరిగింది",
            # pregnancy
            r"bleeding (?:in|during) pregnancy",
            r"pregnan\w* (?:and|with) (?:heavy )?bleeding",
            r"water broke",
            r"baby (?:is )?not moving",
        ],
    ),
]

_COMPILED = [
    (flag, [re.compile(rf"\b{p}" if p.isascii() else re.escape(p), re.I) for p in patterns])
    for flag, patterns in _PATTERNS
]

# "no chest pain", "not unconscious", "without breathlessness"
_NEGATION = re.compile(r"\b(?:no|not|never|without|denies|nothing like)\b[\w\s']{0,12}$", re.I)


def detect_red_flag(text: str) -> RedFlag | None:
    for flag, patterns in _COMPILED:
        for pattern in patterns:
            for m in pattern.finditer(text):
                if not _NEGATION.search(text[max(0, m.start() - 25) : m.start()]):
                    return flag
    return None
