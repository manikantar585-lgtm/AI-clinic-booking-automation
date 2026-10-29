from datetime import datetime, timedelta

import pytest

from app.core.errors import InvalidInputError, InvalidTransitionError
from app.domain.models import AppointmentStatus, Doctor, check_transition, normalize_phone
from app.domain.scheduling import free_slots, nearest_slots, slot_grid
from tests.support import NOW, TODAY

DENTIST = Doctor(
    id=4,
    name="Dr. K",
    specialty="Dentist",
    work_days=frozenset({0, 1, 2, 3, 4, 5}),
    start_time="10:00",
    end_time="12:00",
    slot_minutes=30,
    fee=300,
)


def test_slot_grid_excludes_slot_that_would_overrun_closing() -> None:
    assert slot_grid(DENTIST) == ["10:00", "10:30", "11:00", "11:30"]


def test_free_slots_respects_taken_closed_days_and_past() -> None:
    assert free_slots(DENTIST, TODAY, {"10:30"}, NOW) == ["10:00", "11:00", "11:30"]
    assert free_slots(DENTIST, TODAY + timedelta(days=6), set(), NOW) == []  # Sunday
    assert free_slots(DENTIST, TODAY - timedelta(days=1), set(), NOW) == []


def test_free_slots_today_applies_lead_time() -> None:
    assert free_slots(DENTIST, TODAY, set(), datetime(2026, 11, 2, 10, 20), lead_minutes=15) == ["11:00", "11:30"]


def test_nearest_slots_sorted_by_time() -> None:
    assert nearest_slots(["10:00", "10:30", "11:00", "15:00"], "10:40", k=2) == ["10:30", "11:00"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9876543210", "+919876543210"),
        ("98765 43210", "+919876543210"),
        ("+91 98765-43210", "+919876543210"),
        ("+14155550100", "+14155550100"),
    ],
)
def test_normalize_phone(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["123", "", "abcdefghij", "+1"])
def test_normalize_phone_rejects_invalid(raw: str) -> None:
    with pytest.raises(InvalidInputError):
        normalize_phone(raw)


def test_status_transitions() -> None:
    check_transition(AppointmentStatus.BOOKED, AppointmentStatus.COMPLETED)
    check_transition(AppointmentStatus.NO_SHOW, AppointmentStatus.COMPLETED)  # fixing a mis-click
    with pytest.raises(InvalidTransitionError):
        check_transition(AppointmentStatus.CANCELLED, AppointmentStatus.BOOKED)
    with pytest.raises(InvalidTransitionError):
        check_transition(AppointmentStatus.COMPLETED, AppointmentStatus.BOOKED)
