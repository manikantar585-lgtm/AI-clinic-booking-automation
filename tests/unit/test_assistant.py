from collections.abc import Sequence
from datetime import date

from app.ai.llm import Extraction
from app.domain.models import Doctor
from app.services.booking import BookingService
from tests.support import DENTIST, PEDIATRICIAN, TOMORROW, chat


class FakeExtractor:
    """Stands in for the LLM and records whether it was consulted."""

    def __init__(self, result: Extraction | None = None) -> None:
        self.result = result or Extraction()
        self.calls: list[str] = []

    def extract(self, text: str, doctors: Sequence[Doctor], today: date) -> Extraction:
        self.calls.append(text)
        return self.result


def test_full_booking_step_by_step(assistant, booking: BookingService) -> None:
    assert "Which doctor" in chat(assistant, ["Book appointment"]).text
    assert "When would you like" in chat(assistant, ["4"]).text
    assert "Free slots" in chat(assistant, ["tomorrow"]).text
    r = chat(assistant, ["11:00", "Ravi Teja", "98765 43210"])
    assert "Please confirm" in r.text and "Dentist" in r.text
    r = chat(assistant, ["yes"])
    assert r.text.startswith("Booked!") and r.appointment
    assert (r.appointment["date"], r.appointment["time"], r.appointment["patient_name"]) == (
        TOMORROW.isoformat(),
        "11:00",
        "Ravi Teja",
    )


def test_one_shot_message_fills_several_fields(assistant) -> None:
    r = chat(assistant, ["I have tooth pain, can I come tomorrow at 11am?"])
    assert "patient's name" in r.text
    r = chat(assistant, ["my name is Sita", "9876543210", "yes"])
    assert r.appointment and r.appointment["reason"].startswith("I have tooth pain")


def test_patient_name_matching_a_doctor_is_not_a_doctor_switch(assistant, booking: BookingService) -> None:
    chat(assistant, ["dentist tomorrow 11am"])
    assert "Thanks Ravi" in chat(assistant, ["Ravi"]).text  # also Dr. Ravi Kumar's first name
    r = chat(assistant, ["9876543210", "yes"])
    assert r.appointment and r.appointment["doctor_id"] == DENTIST


def test_unavailable_time_suggests_nearby(assistant) -> None:
    r = chat(assistant, ["dentist tomorrow at 7am"])
    assert "isn't free" in r.text and r.options == ["10:00", "10:30", "11:00", "11:30"]


def test_approximate_time_picks_nearest_slot(assistant) -> None:
    assert "16:30 on" in chat(assistant, ["dentist tomorrow evening"]).text


def test_telugu_reply(assistant) -> None:
    assert "ఖాళీ సమయాలు" in chat(assistant, ["రేపు పంటి నొప్పి"]).text


def test_say_no_at_confirm(assistant, booking: BookingService) -> None:
    r = chat(assistant, ["dentist tomorrow 11am", "Ravi", "9876543210", "no"])
    assert "didn't book" in r.text
    assert booking.list_appointments() == []


def test_slot_taken_between_confirm_and_yes(assistant, booking: BookingService) -> None:
    chat(assistant, ["dentist tomorrow 11am", "Ravi", "9876543210"])
    booking.book(doctor_id=DENTIST, day=TOMORROW, time="11:00", patient_name="Other", patient_phone="9000000000")
    r = chat(assistant, ["yes"])
    assert "not available" in r.text
    assert "Please confirm" in chat(assistant, ["11:30"]).text  # name and phone are kept


def test_web_lookup_masks_codes_and_doctors(assistant) -> None:
    code = chat(assistant, ["dentist tomorrow 11am", "Ravi", "9876543210", "yes"]).appointment["code"]
    r = chat(assistant, ["my bookings", "9876543210"], session="s2")
    assert code not in r.text and code[-2:] in r.text and "Kiran" not in r.text


def test_whatsapp_verified_sender_sees_full_bookings_and_skips_phone_question(assistant) -> None:
    wa = {"session": "wa:1", "sender_phone": "+919876543210"}
    r = chat(assistant, ["dentist tomorrow 11am", "Ravi"], **wa)
    assert "Please confirm" in r.text  # phone came from WhatsApp
    code = chat(assistant, ["yes"], **wa).appointment["code"]
    assert code in chat(assistant, ["my bookings"], **wa).text


def test_whatsapp_cannot_cancel_someone_elses_booking(assistant) -> None:
    code = chat(assistant, ["dentist tomorrow 11am", "Ravi", "9876543210", "yes"]).appointment["code"]
    r = chat(assistant, [f"cancel {code}"], session="wa:2", sender_phone="+919000000000")
    assert "not found" in r.text.lower()
    assert "cancelled" in chat(assistant, [f"cancel {code}"], session="s3").text


def test_unknown_code_is_a_friendly_message(assistant) -> None:
    assert "couldn't find" in chat(assistant, ["cancel CB000000"]).text


def test_llm_not_called_when_rules_understand(make_assistant) -> None:
    llm = FakeExtractor()
    chat(make_assistant(llm), ["dentist tomorrow 11am", "Ravi", "9876543210", "yes"])
    assert llm.calls == []


def test_llm_fills_what_rules_miss(make_assistant) -> None:
    llm = FakeExtractor(Extraction(doctor_id=PEDIATRICIAN, date=TOMORROW, time="10:00", reason="vaccination"))
    r = chat(make_assistant(llm), ["need the little one's shots done"])
    assert llm.calls == ["need the little one's shots done"]
    assert "patient's name" in r.text
    r = chat(make_assistant(llm), ["Anu", "9876543210", "yes"])
    assert r.appointment and r.appointment["doctor_id"] == PEDIATRICIAN and r.appointment["reason"] == "vaccination"


def test_llm_past_date_is_ignored(make_assistant) -> None:
    llm = FakeExtractor(Extraction(doctor_id=DENTIST, date=date(2020, 1, 1)))
    assert "When would you like" in chat(make_assistant(llm), ["something vague"]).text


def test_greeting_and_help(assistant) -> None:
    assert "booking assistant" in chat(assistant, ["hi"]).text
    assert "You can say" in chat(assistant, ["help"]).text
    assert "Our doctors" in chat(assistant, ["doctors"]).text


def test_sessions_are_isolated(assistant) -> None:
    chat(assistant, ["dentist tomorrow 11am"], session="a")
    assert "Which doctor" in chat(assistant, ["book"], session="b").text
