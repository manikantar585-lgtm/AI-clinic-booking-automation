import threading
from datetime import timedelta

import pytest

from app.core.database import Database
from app.core.errors import InvalidInputError, InvalidTransitionError, NotFoundError, SlotUnavailableError
from app.domain.models import AppointmentStatus, Channel
from app.services.booking import BookingService
from tests.support import DENTIST, GP, PEDIATRICIAN, TODAY, TOMORROW, FrozenClock


def _book(booking: BookingService, time: str = "10:00", phone: str = "9876543210", doctor: int = DENTIST):
    return booking.book(doctor_id=doctor, day=TODAY, time=time, patient_name="Ravi", patient_phone=phone)


def test_book_removes_slot_and_blocks_double_booking(booking: BookingService) -> None:
    assert booking.available_slots(DENTIST, TODAY)[0] == "10:00"
    appt = _book(booking)
    assert appt.code.startswith("CB") and appt.patient_phone == "+919876543210"
    assert "10:00" not in booking.available_slots(DENTIST, TODAY)
    with pytest.raises(SlotUnavailableError):
        _book(booking, phone="9876543211")


def test_concurrent_bookings_for_one_slot_have_exactly_one_winner(database: Database, clock: FrozenClock) -> None:
    results: list[str] = []
    barrier = threading.Barrier(8)

    def attempt(i: int) -> None:
        with database.session() as conn:
            service = BookingService(conn, clock)
            barrier.wait()
            try:
                service.book(
                    doctor_id=DENTIST,
                    day=TOMORROW,
                    time="11:00",
                    patient_name=f"P{i}",
                    patient_phone=f"98765432{i:02d}",
                )
                results.append("ok")
            except SlotUnavailableError:
                results.append("taken")

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1 and results.count("taken") == 7


def test_book_validates_input(booking: BookingService) -> None:
    with pytest.raises(InvalidInputError):
        booking.book(doctor_id=DENTIST, day=TODAY, time="10:00", patient_name="R", patient_phone="9876543210")
    with pytest.raises(InvalidInputError):
        _book(booking, phone="123")
    with pytest.raises(InvalidInputError):
        booking.book(
            doctor_id=DENTIST,
            day=TODAY + timedelta(days=90),
            time="10:00",
            patient_name="Ravi",
            patient_phone="9876543210",
        )
    with pytest.raises(NotFoundError):
        _book(booking, doctor=999)


def test_cancel_requires_matching_phone_and_frees_slot(booking: BookingService) -> None:
    appt = _book(booking, doctor=PEDIATRICIAN)
    with pytest.raises(NotFoundError):
        booking.cancel(appt.code, phone="9999999999")
    assert booking.cancel(appt.code, phone="+919876543210").status is AppointmentStatus.CANCELLED
    assert "10:00" in booking.available_slots(PEDIATRICIAN, TODAY)
    with pytest.raises(InvalidTransitionError):
        booking.cancel(appt.code)


def test_get_for_patient_hides_existence(booking: BookingService) -> None:
    appt = _book(booking)
    with pytest.raises(NotFoundError, match="Booking not found"):
        booking.get_for_patient(appt.code, "9000000000")
    with pytest.raises(NotFoundError, match="Booking not found"):
        booking.get_for_patient("CB000000", "9876543210")


def test_next_open_days_skips_closed_days(booking: BookingService, clock: FrozenClock) -> None:
    days = booking.next_open_days(GP, count=7)
    assert len(days) == 7
    assert all(d.weekday() != 6 for d in days)  # GP doesn't work Sundays


def test_stats_and_reminders(booking: BookingService) -> None:
    appt = _book(booking)
    _book(booking, time="10:30")
    booking.set_status(appt.code, AppointmentStatus.COMPLETED)
    stats = booking.stats()
    assert stats == {
        "today_booked": 1,
        "upcoming": 1,
        "completed": 1,
        "cancelled": 0,
        "no_show": 0,
        "revenue_completed": 300,
    }
    [reminder] = booking.reminders_for(TODAY)
    assert reminder["time"] == "10:30" and "CANCEL" in reminder["message"] and "RESCHEDULE" in reminder["message"]


def test_list_appointments_filters(booking: BookingService) -> None:
    _book(booking)
    other = booking.book(
        doctor_id=GP,
        day=TOMORROW,
        time="10:00",
        patient_name="Sita",
        patient_phone="9000000001",
        source=Channel.WHATSAPP,
    )
    assert [a.code for a in booking.list_appointments(day=TOMORROW)] == [other.code]
    assert [a.code for a in booking.list_appointments(phone="9000000001")] == [other.code]
    assert len(booking.list_appointments(limit=1)) == 1


def test_add_doctor_validates_hours(booking: BookingService) -> None:
    with pytest.raises(InvalidInputError):
        booking.add_doctor(
            name="Dr X", specialty="ENT", work_days=[0], start_time="17:00", end_time="09:00", slot_minutes=15, fee=100
        )
    doc = booking.add_doctor(
        name="Dr X",
        specialty="ENT",
        work_days=[2, 0, 0],
        start_time="09:00",
        end_time="10:00",
        slot_minutes=15,
        fee=100,
    )
    assert doc.work_days == frozenset({0, 2})
