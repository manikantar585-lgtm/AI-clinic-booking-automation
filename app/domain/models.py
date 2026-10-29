"""Entities and value types."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.core.errors import InvalidInputError, InvalidTransitionError


class RowLike(Protocol):
    """A database row: `sqlite3.Row` or the Postgres adapter's dict row."""

    def __getitem__(self, key: str, /) -> Any: ...


class AppointmentStatus(StrEnum):
    BOOKED = "booked"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


# Cancelled is terminal: the slot may already belong to someone else.
# Completed <-> no-show stays editable so staff can fix a mis-click.
_TRANSITIONS: dict[AppointmentStatus, frozenset[AppointmentStatus]] = {
    AppointmentStatus.BOOKED: frozenset(
        {AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED, AppointmentStatus.NO_SHOW}
    ),
    AppointmentStatus.COMPLETED: frozenset({AppointmentStatus.NO_SHOW}),
    AppointmentStatus.NO_SHOW: frozenset({AppointmentStatus.COMPLETED}),
    AppointmentStatus.CANCELLED: frozenset(),
}


def check_transition(current: AppointmentStatus, new: AppointmentStatus) -> None:
    if new not in _TRANSITIONS[current]:
        raise InvalidTransitionError(f"A {current.value} booking can't be marked {new.value}.")


class Channel(StrEnum):
    WEB = "web"
    WHATSAPP = "whatsapp"
    ADMIN = "admin"
    API = "api"


_PHONE_RE = re.compile(r"^\+\d{10,13}$")


def normalize_phone(raw: str, default_country_code: str = "+91") -> str:
    """Return an E.164-style number. A bare 10-digit number gets the default country code."""
    digits = re.sub(r"\D", "", raw or "")
    if (raw or "").strip().startswith("+"):
        phone = "+" + digits
    elif len(digits) == 10:
        phone = default_country_code + digits
    else:
        phone = "+" + digits
    if not _PHONE_RE.match(phone):
        raise InvalidInputError("Please give a valid 10-digit mobile number.")
    return phone


@dataclass(frozen=True, slots=True)
class Doctor:
    id: int
    name: str
    specialty: str
    work_days: frozenset[int]
    start_time: str
    end_time: str
    slot_minutes: int
    fee: int
    active: bool = True

    @classmethod
    def from_row(cls, row: RowLike) -> Doctor:
        return cls(
            id=row["id"],
            name=row["name"],
            specialty=row["specialty"],
            work_days=frozenset(int(d) for d in row["work_days"].split(",") if d.strip()),
            start_time=row["start_time"],
            end_time=row["end_time"],
            slot_minutes=row["slot_minutes"],
            fee=row["fee"],
            active=bool(row["active"]),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["work_days"] = sorted(self.work_days)
        return data


@dataclass(frozen=True, slots=True)
class Appointment:
    code: str
    doctor_id: int
    doctor_name: str
    specialty: str
    fee: int
    patient_name: str
    patient_phone: str
    date: str
    time: str
    reason: str
    status: AppointmentStatus
    source: Channel
    created_at: str
    rescheduled_count: int = 0
    reminder_sent_at: str | None = None

    @classmethod
    def from_row(cls, row: RowLike) -> Appointment:
        return cls(
            code=row["code"],
            doctor_id=row["doctor_id"],
            doctor_name=row["doctor_name"],
            specialty=row["specialty"],
            fee=row["fee"],
            patient_name=row["patient_name"],
            patient_phone=row["patient_phone"],
            date=row["date"],
            time=row["time"],
            reason=row["reason"] or "",
            status=AppointmentStatus(row["status"]),
            source=Channel(row["source"]),
            created_at=row["created_at"],
            rescheduled_count=row["rescheduled_count"],
            reminder_sent_at=row["reminder_sent_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DoctorLeave:
    id: int
    doctor_id: int
    start_date: str
    end_date: str
    reason: str

    @classmethod
    def from_row(cls, row: RowLike) -> DoctorLeave:
        return cls(row["id"], row["doctor_id"], row["start_date"], row["end_date"], row["reason"])

    def covers(self, day: str) -> bool:
        return self.start_date <= day <= self.end_date

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
