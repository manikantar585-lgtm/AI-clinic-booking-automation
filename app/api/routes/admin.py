import csv
import datetime as dt
import io
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import BookingDep, SettingsDep, require_admin
from app.api.schemas import (
    AdminConfigOut,
    AdminRescheduleIn,
    AppointmentOut,
    DoctorIn,
    DoctorOut,
    ErrorOut,
    InsightsOut,
    LeaveCreatedOut,
    LeaveIn,
    LeaveOut,
    ReminderOut,
    ReminderRunOut,
    StatsOut,
    StatusIn,
)
from app.core.errors import NotConfiguredError
from app.domain.models import AppointmentStatus
from app.services.notifications import NOT_CONFIGURED

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
    responses={401: {"model": ErrorOut}},
)
_ERRORS: dict[int | str, dict[str, Any]] = {code: {"model": ErrorOut} for code in (400, 404, 409)}


@router.get("/config", response_model=AdminConfigOut, summary="Which optional integrations are switched on")
def config(settings: SettingsDep) -> AdminConfigOut:
    return AdminConfigOut(
        whatsapp_sending=settings.whatsapp_sending_enabled,
        reminder_send_hour=settings.reminder_send_hour,
        llm_enabled=settings.llm_enabled,
    )


@router.get("/stats", response_model=StatsOut)
def stats(booking: BookingDep) -> StatsOut:
    return StatsOut(**booking.stats())


@router.get("/insights", response_model=InsightsOut)
def insights(booking: BookingDep, days: Annotated[int, Query(ge=7, le=365)] = 30) -> InsightsOut:
    return InsightsOut.model_validate(booking.insights(days))


# ---------------------------------------------------------------- appointments


@router.get("/appointments", response_model=list[AppointmentOut])
def list_appointments(
    booking: BookingDep,
    date: dt.date | None = None,
    status: AppointmentStatus | None = None,
    doctor_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AppointmentOut]:
    rows = booking.list_appointments(day=date, status=status, doctor_id=doctor_id, limit=limit, offset=offset)
    return [AppointmentOut.model_validate(a) for a in rows]


_CSV_COLUMNS = [
    "date",
    "time",
    "code",
    "patient_name",
    "patient_phone",
    "doctor_name",
    "specialty",
    "reason",
    "status",
    "source",
    "fee",
    "rescheduled_count",
    "created_at",
]


def _csv_safe(value: object) -> object:
    """Neutralise spreadsheet formula injection (a patient named "=HYPERLINK(...)").
    Plain phone numbers like +9198... are left alone: they can't run as formulas."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") and not value[1:].isdigit():
        return "'" + value
    return value


@router.get("/appointments.csv", response_class=Response, summary="Export appointments as CSV")
def export_csv(
    booking: BookingDep,
    date_from: Annotated[dt.date, Query(alias="from")],
    date_to: Annotated[dt.date, Query(alias="to")],
    status: AppointmentStatus | None = None,
    doctor_id: int | None = None,
) -> Response:
    rows = booking.list_appointments(
        date_from=date_from, date_to=date_to, status=status, doctor_id=doctor_id, limit=10_000
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    for a in rows:
        d = a.to_dict()
        writer.writerow([_csv_safe(d[c]) for c in _CSV_COLUMNS])
    filename = f"appointments_{date_from}_{date_to}.csv"
    return Response(
        "\ufeff" + buf.getvalue(),  # BOM so Excel opens Telugu names correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/appointments/{code}/status", response_model=AppointmentOut, responses=_ERRORS)
def set_status(code: str, body: StatusIn, booking: BookingDep) -> AppointmentOut:
    return AppointmentOut.model_validate(booking.set_status(code, body.status))


@router.post("/appointments/{code}/reschedule", response_model=AppointmentOut, responses=_ERRORS)
def reschedule(code: str, body: AdminRescheduleIn, booking: BookingDep) -> AppointmentOut:
    return AppointmentOut.model_validate(booking.reschedule(code, day=body.date, time=body.time))


# ---------------------------------------------------------------- doctors & leave


@router.post("/doctors", status_code=201, response_model=DoctorOut)
def add_doctor(body: DoctorIn, booking: BookingDep) -> DoctorOut:
    return DoctorOut(**booking.add_doctor(**body.model_dump()).to_dict())


@router.get("/leaves", response_model=list[LeaveOut])
def list_leaves(booking: BookingDep, doctor_id: int | None = None) -> list[LeaveOut]:
    return [LeaveOut.model_validate(lv) for lv in booking.list_leaves(doctor_id)]


@router.post("/doctors/{doctor_id}/leaves", status_code=201, response_model=LeaveCreatedOut, responses=_ERRORS)
def add_leave(doctor_id: int, body: LeaveIn, booking: BookingDep) -> LeaveCreatedOut:
    leave, affected = booking.add_leave(doctor_id, body.start_date, body.end_date, body.reason)
    return LeaveCreatedOut(
        leave=LeaveOut.model_validate(leave), affected=[AppointmentOut.model_validate(a) for a in affected]
    )


@router.delete("/leaves/{leave_id}", status_code=204, response_class=Response, responses=_ERRORS)
def delete_leave(leave_id: int, booking: BookingDep) -> Response:
    booking.delete_leave(leave_id)
    return Response(status_code=204)


# ---------------------------------------------------------------- reminders


@router.get("/reminders", response_model=list[ReminderOut])
def reminders(date: dt.date, booking: BookingDep) -> list[ReminderOut]:
    return [ReminderOut.model_validate(r) for r in booking.reminders_for(date)]


@router.post("/reminders/send", response_model=ReminderRunOut, responses=_ERRORS)
def send_reminders(date: dt.date, booking: BookingDep, request: Request) -> ReminderRunOut:
    notifier = request.app.state.notifier
    if not notifier.configured:
        raise NotConfiguredError(NOT_CONFIGURED)
    return ReminderRunOut.model_validate(booking.send_reminders(date, notifier))
