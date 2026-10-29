from datetime import date, timedelta

import pytest

from app.ai.nlu import (
    Intent,
    TimeRequest,
    detect_intent,
    extract_name,
    find_code,
    match_doctor,
    parse_date,
    parse_time,
)
from app.core.database import DEMO_DOCTORS
from app.domain.models import Doctor
from tests.support import TODAY

DOCTORS = [
    Doctor(id=i, name=n, specialty=s, work_days=frozenset(), start_time=a, end_time=b, slot_minutes=m, fee=f)
    for i, (n, s, _, a, b, m, f) in enumerate(DEMO_DOCTORS, 1)
]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tomorrow please", TODAY + timedelta(days=1)),
        ("రేపు రావచ్చా", TODAY + timedelta(days=1)),
        ("kal aa sakte hai", TODAY + timedelta(days=1)),
        ("day after tomorrow", TODAY + timedelta(days=2)),
        ("ఎల్లుండి", TODAY + timedelta(days=2)),
        ("today", TODAY),
        ("on 5/11", date(2026, 11, 5)),
        ("2026-11-20", date(2026, 11, 20)),
        ("10 nov", date(2026, 11, 10)),
        ("nov 10th", date(2026, 11, 10)),
        ("friday", date(2026, 11, 6)),
        ("this monday", TODAY),
        ("monday", TODAY + timedelta(days=7)),
        ("5 jan", date(2027, 1, 5)),  # past month rolls into next year
        ("31/02", None),  # impossible date
        ("hello", None),
    ],
)
def test_parse_date(text: str, expected: date | None) -> None:
    assert parse_date(text, TODAY) == expected


@pytest.mark.parametrize(
    ("text", "loose", "expected"),
    [
        ("11am", False, TimeRequest(hhmm="11:00")),
        ("4:30 pm", False, TimeRequest(hhmm="16:30")),
        ("12 pm", False, TimeRequest(hhmm="12:00")),
        ("at 4", False, TimeRequest(hhmm="16:00")),
        ("14:15", False, TimeRequest(hhmm="14:15")),
        ("11", True, TimeRequest(hhmm="11:00")),
        ("11", False, None),
        ("evening", False, TimeRequest(hhmm="16:30", approximate=True)),
        ("25:00", False, None),
    ],
)
def test_parse_time(text: str, loose: bool, expected: TimeRequest | None) -> None:
    assert parse_time(text, loose=loose) == expected


@pytest.mark.parametrize(
    ("text", "specialty"),
    [
        ("I have tooth pain", "Dentist"),
        ("పంటి నొప్పి", "Dentist"),
        ("skin rash", "Dermatologist"),
        ("fever and cough", "General Physician"),
        ("my son has fever", "Pediatrician"),  # child takes priority over the symptom
        ("Dr. Sneha please", "Dermatologist"),
        ("gynecologist", "Gynecologist"),
    ],
)
def test_match_doctor(text: str, specialty: str) -> None:
    doctor = match_doctor(text, DOCTORS)
    assert doctor is not None and doctor.specialty == specialty


def test_match_doctor_uses_word_boundaries() -> None:
    # "son" must not match inside "person" or "reason"
    assert match_doctor("a person with no reason", DOCTORS) is None


def test_match_doctor_by_number_only_when_allowed() -> None:
    assert match_doctor("4", DOCTORS, allow_number=True).specialty == "Dentist"  # type: ignore[union-attr]
    assert match_doctor("4", DOCTORS) is None
    assert match_doctor("9", DOCTORS, allow_number=True) is None


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("cancel CB1A2B3C", Intent.CANCEL),
        ("CB1A2B3C", Intent.CANCEL),
        ("my bookings", Intent.MY_BOOKINGS),
        ("show doctors", Intent.DOCTORS),
        ("book a doctor", Intent.BOOK),
        ("help", Intent.HELP),
        ("hi!", Intent.GREET),
        ("నమస్తే", Intent.GREET),
        ("tooth pain", None),
    ],
)
def test_detect_intent(text: str, intent: Intent | None) -> None:
    assert detect_intent(text) is intent


def test_entities() -> None:
    assert find_code("please cancel cb1a2b3c") == "CB1A2B3C"
    assert extract_name("my name is ravi teja") == "Ravi Teja"
    assert extract_name("నా పేరు సీత") == "సీత"
    assert extract_name("a") is None
