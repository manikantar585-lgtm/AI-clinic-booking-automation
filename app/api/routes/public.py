import datetime as dt
from typing import Any

from fastapi import APIRouter

from app.api.deps import BookingDep, SettingsDep
from app.api.schemas import (
    AppointmentOut,
    BookIn,
    CancelIn,
    ClinicOut,
    DoctorOut,
    ErrorOut,
    RescheduleIn,
    SlotsOut,
)
from app.domain.models import Channel

router = APIRouter(prefix="/api", tags=["public"])

_ERRORS: dict[int | str, dict[str, Any]] = {code: {"model": ErrorOut} for code in (400, 404, 409)}


@router.get("/clinic", response_model=ClinicOut)
def clinic(settings: SettingsDep) -> ClinicOut:
    return ClinicOut(name=settings.clinic_name, phone=settings.clinic_phone)


@router.get("/doctors", response_model=list[DoctorOut])
def list_doctors(booking: BookingDep) -> list[DoctorOut]:
    return [DoctorOut(**d.to_dict()) for d in booking.list_doctors()]


@router.get("/doctors/{doctor_id}/slots", response_model=SlotsOut, responses=_ERRORS)
def slots(doctor_id: int, date: dt.date, booking: BookingDep) -> SlotsOut:
    return SlotsOut(doctor_id=doctor_id, date=date, slots=booking.available_slots(doctor_id, date))


@router.post("/appointments", status_code=201, response_model=AppointmentOut, responses=_ERRORS)
def create_appointment(body: BookIn, booking: BookingDep) -> AppointmentOut:
    appt = booking.book(
        doctor_id=body.doctor_id,
        day=body.date,
        time=body.time,
        patient_name=body.patient_name,
        patient_phone=body.patient_phone,
        reason=body.reason,
        source=Channel.API,
    )
    return AppointmentOut.model_validate(appt)


@router.get("/appointments/{code}", response_model=AppointmentOut, responses=_ERRORS)
def get_appointment(code: str, phone: str, booking: BookingDep) -> AppointmentOut:
    return AppointmentOut.model_validate(booking.get_for_patient(code, phone))


@router.post("/appointments/{code}/cancel", response_model=AppointmentOut, responses=_ERRORS)
def cancel_appointment(code: str, body: CancelIn, booking: BookingDep) -> AppointmentOut:
    return AppointmentOut.model_validate(booking.cancel(code, phone=body.phone))


@router.post("/appointments/{code}/reschedule", response_model=AppointmentOut, responses=_ERRORS)
def reschedule_appointment(code: str, body: RescheduleIn, booking: BookingDep) -> AppointmentOut:
    return AppointmentOut.model_validate(booking.reschedule(code, day=body.date, time=body.time, phone=body.phone))
