from datetime import timedelta

import pytest

from app.core.errors import InvalidInputError, InvalidTransitionError, NotFoundError, SlotUnavailableError
from app.domain.models import AppointmentStatus
from app.services.booking import BookingService
from tests.support import DENTIST, GP, TODAY, TOMORROW, chat


def _book(booking: BookingService, time: str = "11:00", day=TOMORROW, phone: str = "9876543210"):
    return booking.book(doctor_id=DENTIST, day=day, time=time, patient_name="Ravi", patient_phone=phone)


# ---------------------------------------------------------------- reschedule (service)


def test_reschedule_moves_slot_keeps_code_and_resets_reminder(booking: BookingService) -> None:
    appt = _book(booking)
    booking.conn.execute("UPDATE appointments SET reminder_sent_at = datetime('now')")
    moved = booking.reschedule(appt.code, day=TOMORROW, time="15:00", phone="9876543210")
    assert (moved.code, moved.time, moved.rescheduled_count, moved.reminder_sent_at) == (appt.code, "15:00", 1, None)
    slots = booking.available_slots(DENTIST, TOMORROW)
    assert "11:00" in slots and "15:00" not in slots


def test_reschedule_guards(booking: BookingService) -> None:
    appt = _book(booking)
    _book(booking, time="12:00", phone="9000000000")
    with pytest.raises(SlotUnavailableError):
        booking.reschedule(appt.code, day=TOMORROW, time="12:00")
    with pytest.raises(InvalidInputError, match="same time"):
        booking.reschedule(appt.code, day=TOMORROW, time="11:00")
    with pytest.raises(NotFoundError):
        booking.reschedule(appt.code, day=TOMORROW, time="15:00", phone="9111111111")
    booking.cancel(appt.code)
    with pytest.raises(InvalidTransitionError):
        booking.reschedule(appt.code, day=TOMORROW, time="15:00")


# ---------------------------------------------------------------- leave (service)


def test_leave_blocks_days_and_reports_clashing_bookings(booking: BookingService) -> None:
    appt = _book(booking)
    leave, affected = booking.add_leave(DENTIST, TOMORROW, TOMORROW + timedelta(days=2), "Conference")
    assert [a.code for a in affected] == [appt.code]
    assert booking.get_appointment(appt.code).status is AppointmentStatus.BOOKED  # not auto-cancelled
    assert booking.available_slots(DENTIST, TOMORROW) == []
    open_days = booking.next_open_days(DENTIST, count=2)
    assert TOMORROW not in open_days and all(d > TOMORROW + timedelta(days=2) or d == TODAY for d in open_days)
    with pytest.raises(SlotUnavailableError):
        _book(booking, time="13:00", day=TOMORROW, phone="9000000000")

    assert [lv.id for lv in booking.list_leaves(DENTIST)] == [leave.id]
    booking.delete_leave(leave.id)
    assert booking.available_slots(DENTIST, TOMORROW)
    with pytest.raises(NotFoundError):
        booking.delete_leave(leave.id)


def test_leave_validation(booking: BookingService) -> None:
    with pytest.raises(InvalidInputError):
        booking.add_leave(DENTIST, TOMORROW, TODAY)
    with pytest.raises(InvalidInputError):
        booking.add_leave(DENTIST, TODAY - timedelta(days=5), TODAY - timedelta(days=3))
    with pytest.raises(NotFoundError):
        booking.add_leave(999, TODAY, TODAY)


# ---------------------------------------------------------------- chat


def test_chat_reschedule_in_one_message(assistant, booking: BookingService) -> None:
    code = chat(assistant, ["dentist tomorrow 11am", "Ravi", "9876543210", "yes"]).appointment["code"]
    r = chat(assistant, [f"reschedule {code} to tomorrow 3pm"], session="s2")
    assert "Move booking" in r.text and "15:00" in r.text and r.options == ["Yes", "No"]
    r = chat(assistant, ["yes"], session="s2")
    assert r.text.startswith("Done!") and r.appointment["code"] == code and r.appointment["time"] == "15:00"
    assert booking.get_appointment(code).rescheduled_count == 1


def test_chat_reschedule_step_by_step_and_decline(assistant, booking: BookingService) -> None:
    code = chat(assistant, ["dentist tomorrow 11am", "Ravi", "9876543210", "yes"]).appointment["code"]
    assert "Which booking" in chat(assistant, ["I want to reschedule"], session="s2").text
    assert "Which day works" in chat(assistant, [code], session="s2").text
    assert "Free slots" in chat(assistant, ["tomorrow"], session="s2").text
    r = chat(assistant, ["16:00", "no"], session="s2")
    assert "nothing changed" in r.text
    assert booking.get_appointment(code).time == "11:00"


def test_chat_reschedule_does_not_switch_doctor(assistant) -> None:
    code = chat(assistant, ["dentist tomorrow 11am", "Ravi", "9876543210", "yes"]).appointment["code"]
    r = chat(assistant, [f"reschedule {code}", "skin rash, tomorrow 2pm"], session="s2")
    assert "Dr. Kiran Varma" in r.text  # still the dentist


def test_chat_reschedule_on_whatsapp_requires_owner(assistant) -> None:
    code = chat(assistant, ["dentist tomorrow 11am", "Ravi", "9876543210", "yes"]).appointment["code"]
    r = chat(assistant, [f"reschedule {code}"], session="wa:x", sender_phone="+919000000000")
    assert "not found" in r.text.lower()


def test_chat_explains_doctor_leave(assistant, booking: BookingService) -> None:
    booking.add_leave(GP, TOMORROW, TOMORROW, "Medical camp")
    r = chat(assistant, ["fever, tomorrow"])
    assert "on leave" in r.text and "Medical camp" in r.text and TOMORROW.isoformat() not in r.options
