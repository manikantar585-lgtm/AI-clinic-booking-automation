"""Slot arithmetic. Times are zero-padded "HH:MM" strings, so they sort correctly as text."""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, datetime, timedelta

from app.domain.models import Doctor


def to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def from_minutes(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def slot_grid(doctor: Doctor) -> list[str]:
    """Every slot start in a doctor's working day."""
    start, end, step = to_minutes(doctor.start_time), to_minutes(doctor.end_time), doctor.slot_minutes
    return [from_minutes(t) for t in range(start, end - step + 1, step)]


def free_slots(doctor: Doctor, day: date, taken: Collection[str], now: datetime, lead_minutes: int = 15) -> list[str]:
    """Slots on `day` that are open, not taken, and at least `lead_minutes` in the future."""
    if not doctor.active or day < now.date() or day.weekday() not in doctor.work_days:
        return []
    slots = [s for s in slot_grid(doctor) if s not in taken]
    if day == now.date():
        cutoff = (now + timedelta(minutes=lead_minutes)).strftime("%H:%M")
        slots = [s for s in slots if s >= cutoff]
    return slots


def nearest_slots(slots: list[str], target: str, k: int = 4) -> list[str]:
    """The `k` slots closest to `target`, returned in time order."""
    goal = to_minutes(target)
    return sorted(sorted(slots, key=lambda s: abs(to_minutes(s) - goal))[:k])
