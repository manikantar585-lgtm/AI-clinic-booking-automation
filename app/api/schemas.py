"""Request and response contracts. These are the public API, so change them deliberately."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.domain.models import AppointmentStatus, Channel

HHMM = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", examples=["10:30"])]


def _split_days(value: Any) -> Any:
    """Accept the legacy "0,1,2" string form as well as a list."""
    if isinstance(value, str):
        return [int(d) for d in value.split(",") if d.strip()]
    return value


WorkDays = Annotated[list[Annotated[int, Field(ge=0, le=6)]], BeforeValidator(_split_days), Field(min_length=1)]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorOut(BaseModel):
    error: ErrorBody
    request_id: str


# ---------------------------------------------------------------- public


class ClinicOut(BaseModel):
    name: str
    phone: str


class HealthOut(BaseModel):
    status: str
    version: str


class ReadyOut(HealthOut):
    database: str
    llm_enabled: bool


class DoctorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    specialty: str
    work_days: list[int]
    start_time: str
    end_time: str
    slot_minutes: int
    fee: int
    active: bool


class SlotsOut(BaseModel):
    doctor_id: int
    date: dt.date
    slots: list[str]


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    doctor_id: int
    doctor_name: str
    specialty: str
    fee: int
    patient_name: str
    patient_phone: str
    date: dt.date
    time: str
    reason: str
    status: AppointmentStatus
    source: Channel
    created_at: str
    rescheduled_count: int = 0
    reminder_sent_at: str | None = None


class BookIn(BaseModel):
    doctor_id: int
    date: dt.date
    time: HHMM
    patient_name: str = Field(min_length=2, max_length=60)
    patient_phone: str = Field(max_length=20)
    reason: str = Field("", max_length=200)


class CancelIn(BaseModel):
    phone: str = Field(max_length=20)


class RescheduleIn(BaseModel):
    phone: str = Field(max_length=20)
    date: dt.date
    time: HHMM


class ChatIn(BaseModel):
    # Colons are reserved for server-side ids (e.g. "wa:<number>") so a web client
    # can't take over a WhatsApp conversation by guessing its session id.
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{4,64}$")
    message: str = Field(min_length=1, max_length=1000)


class ChatOut(BaseModel):
    text: str
    options: list[str]
    appointment: AppointmentOut | None = None
    alert: Literal["emergency", "self_harm"] | None = None


# ----------------------------------------------------------------- admin


class StatusIn(BaseModel):
    status: AppointmentStatus


class DoctorIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    specialty: str = Field(min_length=2, max_length=60)
    work_days: WorkDays = [0, 1, 2, 3, 4, 5]
    start_time: HHMM = "10:00"
    end_time: HHMM = "17:00"
    slot_minutes: int = Field(20, ge=5, le=120)
    fee: int = Field(500, ge=0)


class StatsOut(BaseModel):
    today_booked: int
    upcoming: int
    completed: int
    cancelled: int
    no_show: int
    revenue_completed: int


class ReminderOut(AppointmentOut):
    message: str


class AdminRescheduleIn(BaseModel):
    date: dt.date
    time: HHMM


class LeaveIn(BaseModel):
    start_date: dt.date
    end_date: dt.date
    reason: str = Field("", max_length=120)


class LeaveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doctor_id: int
    start_date: dt.date
    end_date: dt.date
    reason: str


class LeaveCreatedOut(BaseModel):
    leave: LeaveOut
    affected: list[AppointmentOut]


class ReminderFailure(BaseModel):
    code: str
    patient_name: str
    error: str


class ReminderRunOut(BaseModel):
    date: dt.date
    sent: int
    already_sent: int
    failed: list[ReminderFailure]


class AdminConfigOut(BaseModel):
    whatsapp_sending: bool
    reminder_send_hour: int | None
    llm_enabled: bool


class InsightTotals(BaseModel):
    appointments: int
    completed: int
    no_show: int
    cancelled: int
    revenue: int
    no_show_rate: float
    cancellation_rate: float
    avg_lead_days: float | None
    upcoming_7_days: int


class InsightDay(BaseModel):
    date: dt.date
    booked: int
    completed: int
    no_show: int
    cancelled: int


class InsightDoctor(BaseModel):
    id: int
    name: str
    specialty: str
    total: int
    completed: int
    no_show: int
    revenue: int


class InsightHour(BaseModel):
    hour: int
    count: int


class InsightsOut(BaseModel):
    start: dt.date
    end: dt.date
    totals: InsightTotals
    daily: list[InsightDay]
    by_doctor: list[InsightDoctor]
    by_channel: dict[str, int]
    by_hour: list[InsightHour]
