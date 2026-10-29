"""Dialogue manager: a slot-filling state machine for booking, cancelling and lookups.

Each turn:
  1. Load the session state (expired sessions start fresh).
  2. Understand the message: rules first, the LLM only when rules come up empty.
  3. Absorb whatever fields the message carries (one message can fill several).
  4. Ask for the next missing field, or confirm and book.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from app.ai.lexicon import NO, TELUGU_RE, YES
from app.ai.llm import Extraction, Extractor
from app.ai.messages import Lang, render
from app.ai.nlu import (
    Intent,
    TimeRequest,
    detect_intent,
    extract_name,
    find_code,
    find_phone,
    match_doctor,
    parse_date,
    parse_time,
)
from app.ai.safety import detect_red_flag
from app.core.errors import AppError, InvalidInputError, InvalidTransitionError, SlotUnavailableError
from app.domain.models import AppointmentStatus, Channel, Doctor, normalize_phone
from app.domain.scheduling import nearest_slots
from app.services.booking import BookingService
from app.services.sessions import SessionStore

log = logging.getLogger(__name__)

MAIN_MENU = ["Book appointment", "Reschedule", "My bookings", "Cancel", "Doctors"]
_MENU_WORDS = {o.lower() for o in [*MAIN_MENU, "Book another"]}


class Step(StrEnum):
    DOCTOR = "doctor"
    DATE = "date"
    TIME = "time"
    NAME = "name"
    PHONE = "phone"
    CONFIRM = "confirm"
    CANCEL_CODE = "cancel_code"
    RESCHEDULE_CODE = "reschedule_code"
    LOOKUP_PHONE = "lookup_phone"


CHOOSING = frozenset({Step.DOCTOR, Step.DATE, Step.TIME})


class ConversationState(BaseModel):
    lang: Lang = "en"
    step: Step | None = None
    doctor_id: int | None = None
    day: dt.date | None = None
    time: str | None = None
    want_time: TimeRequest | None = None
    name: str | None = None
    phone: str | None = None
    reason: str | None = None
    reschedule_code: str | None = None  # set while moving an existing booking
    reschedule_from: str | None = None

    def reset(self) -> None:
        """Forget the booking in progress but keep the patient's language."""
        for field, info in type(self).model_fields.items():
            if field != "lang":
                setattr(self, field, info.default)


class Reply(BaseModel):
    text: str
    options: list[str] = []
    appointment: dict[str, Any] | None = None
    alert: str | None = None  # "emergency" | "self_harm"


def _fmt_day(d: dt.date) -> str:
    return d.strftime("%a %d %b")


def _mask(code: str) -> str:
    return code[:2] + "••••" + code[-2:]


class Assistant:
    def __init__(
        self,
        booking: BookingService,
        sessions: SessionStore[ConversationState],
        extractor: Extractor,
        clinic_name: str,
        clinic_phone: str = "",
    ) -> None:
        self.booking = booking
        self.sessions = sessions
        self.extractor = extractor
        self.clinic_name = clinic_name
        self.clinic_phone = clinic_phone

    def reply(
        self,
        session_id: str,
        message: str,
        source: Channel = Channel.WEB,
        sender_phone: str | None = None,
    ) -> Reply:
        """`sender_phone` is a number the channel has already verified (e.g. WhatsApp's
        sender). It unlocks owner-only actions and saves asking the patient for it."""
        state = self.sessions.load(session_id)
        if TELUGU_RE.search(message):
            state.lang = "te"
        try:
            out = self._handle(state, message.strip(), source, sender_phone)
        except AppError as e:
            out = Reply(text=e.message)
        self.sessions.save(session_id, state)
        return out

    # ------------------------------------------------------------------ helpers

    def _t(self, state: ConversationState, key: str, **values: object) -> str:
        return render(state.lang, key, clinic=self.clinic_name, **values)

    def _understand(
        self, state: ConversationState, msg: str, intent: Intent | None, doctors: Sequence[Doctor], today: dt.date
    ) -> Extraction:
        """Call the LLM only when it can add something the rules missed."""
        step = state.step
        if not (step is None or step in CHOOSING) or intent not in (None, Intent.BOOK):
            return Extraction()
        if msg.lower() in _MENU_WORDS:
            return Extraction()
        rules_got_it = {
            None: match_doctor(msg, doctors) is not None,
            Step.DOCTOR: match_doctor(msg, doctors, allow_number=True) is not None,
            Step.DATE: parse_date(msg, today) is not None,
            Step.TIME: parse_time(msg, loose=True) is not None,
        }[step]
        if rules_got_it:
            return Extraction()
        return self.extractor.extract(msg, doctors, today)

    # --------------------------------------------------------------------- turn

    def _handle(self, state: ConversationState, msg: str, source: Channel, sender_phone: str | None) -> Reply:
        doctors = self.booking.list_doctors()
        today = self.booking.clock().date()
        step = state.step
        low = msg.lower().strip(" .!")

        # Safety first: a red-flag message is never treated as a booking request.
        # The booking in progress is kept, so the patient can carry on afterwards.
        if flag := detect_red_flag(msg):
            log.warning("red flag detected", extra={"category": flag.value, "channel": source.value})
            return Reply(
                text=self._t(state, flag.value, phone=self.clinic_phone or "108"),
                options=["Book appointment"],
                alert=flag.value,
            )

        intent = detect_intent(msg)
        llm = self._understand(state, msg, intent, doctors, today)
        intent = intent or llm.intent

        if step is Step.CONFIRM:
            if low in YES:
                return self._finish_reschedule(state) if state.reschedule_code else self._finish_booking(state, source)
            if low in NO or intent is Intent.CANCEL:
                old = state.reschedule_from
                state.reset()
                if old:
                    return Reply(text=self._t(state, "reschedule_kept", old=old), options=["My bookings"])
                return Reply(text=self._t(state, "aborted"), options=["Book appointment"])

        if step is Step.RESCHEDULE_CODE or intent is Intent.RESCHEDULE:
            return self._start_reschedule(state, msg, sender_phone, doctors)

        if step is Step.CANCEL_CODE or intent is Intent.CANCEL:
            return self._cancel(state, msg, sender_phone)
        if step is Step.LOOKUP_PHONE or intent is Intent.MY_BOOKINGS:
            return self._my_bookings(state, msg, sender_phone)

        if intent is Intent.DOCTORS and step is None:
            items = "; ".join(f"{i}. {d.name} ({d.specialty}, Rs {d.fee})" for i, d in enumerate(doctors, 1))
            return Reply(text=self._t(state, "doctors", items=items), options=["Book appointment"])
        if intent is Intent.HELP:
            return Reply(text=self._t(state, "help"), options=MAIN_MENU[:4])
        if step is None and intent is not Intent.BOOK:
            understood = not llm.is_empty or match_doctor(msg, doctors) or parse_date(msg, today)
            if intent is Intent.GREET or not understood:
                return Reply(text=self._t(state, "welcome"), options=MAIN_MENU)

        # Booking flow
        if state.step is None:
            state.step = Step.DOCTOR
            if sender_phone and not state.phone:
                state.phone = normalize_phone(sender_phone)
        self._absorb(state, msg, llm, doctors, today)
        return self._next_question(state, doctors)

    def _cancel(self, state: ConversationState, msg: str, sender_phone: str | None) -> Reply:
        code = find_code(msg)
        if not code:
            state.step = Step.CANCEL_CODE
            return Reply(text=self._t(state, "ask_code"))
        appt = self.booking.cancel(code, phone=sender_phone)
        state.reset()
        return Reply(
            text=self._t(state, "cancelled", code=appt.code, doctor=appt.doctor_name, date=appt.date, time=appt.time),
            options=["Book appointment"],
        )

    def _start_reschedule(
        self, state: ConversationState, msg: str, sender_phone: str | None, doctors: Sequence[Doctor]
    ) -> Reply:
        code = find_code(msg)
        state.reset()
        if not code:
            state.step = Step.RESCHEDULE_CODE
            return Reply(text=self._t(state, "ask_reschedule_code"))
        appt = self.booking.get_for_patient(code, sender_phone) if sender_phone else self.booking.get_appointment(code)
        if appt.status is not AppointmentStatus.BOOKED:
            raise InvalidTransitionError(f"This booking is already {appt.status.value.replace('_', '-')}.")
        state.reschedule_code = appt.code
        state.reschedule_from = f"{_fmt_day(dt.date.fromisoformat(appt.date))} {appt.time}"
        state.doctor_id, state.name, state.phone = appt.doctor_id, appt.patient_name, appt.patient_phone
        state.step = Step.DATE
        # "reschedule CB1A2B3C to friday 4pm" carries the new slot too
        self._absorb(state, msg, Extraction(), doctors, self.booking.clock().date())
        return self._next_question(state, doctors)

    def _my_bookings(self, state: ConversationState, msg: str, sender_phone: str | None) -> Reply:
        raw = sender_phone or find_phone(msg)
        if not raw:
            state.step = Step.LOOKUP_PHONE
            return Reply(text=self._t(state, "ask_phone_lookup"))
        phone = normalize_phone(raw)
        items = self.booking.list_appointments(phone=phone, status=AppointmentStatus.BOOKED, upcoming_only=True)
        state.reset()
        if not items:
            return Reply(text=self._t(state, "my_none", phone=phone), options=["Book appointment"])
        if sender_phone:  # verified owner: full details
            lines = [f"{a.code} - {a.doctor_name} {a.date} {a.time}" for a in items]
        else:  # anyone can type a number on the web, so don't reveal codes or doctors
            lines = [f"{_mask(a.code)} - {a.date} {a.time}" for a in items]
        return Reply(text=self._t(state, "my_list", items="; ".join(lines)), options=["Book appointment"])

    def _absorb(
        self, state: ConversationState, msg: str, llm: Extraction, doctors: Sequence[Doctor], today: dt.date
    ) -> None:
        step = state.step
        choosing = step in CHOOSING

        rescheduling = state.reschedule_code is not None  # the doctor is fixed when moving a booking
        doc = match_doctor(msg, doctors, allow_number=step is Step.DOCTOR) if choosing and not rescheduling else None
        if not doc and llm.doctor_id and not rescheduling:
            doc = next((d for d in doctors if d.id == llm.doctor_id), None)
        if doc and doc.id != state.doctor_id:
            state.doctor_id = doc.id
            state.time = None

        day = (parse_date(msg, today) if choosing else None) or llm.date
        if day and day >= today:
            if day != state.day:
                state.time = None
            state.day = day

        if choosing:
            wanted = parse_time(msg, loose=step is Step.TIME) or (TimeRequest(hhmm=llm.time) if llm.time else None)
            if wanted:
                state.want_time = wanted

        if rescheduling:
            return
        if llm.reason and not state.reason:
            state.reason = llm.reason
        elif (
            step is Step.DOCTOR
            and doc
            and not state.reason
            and not re.match(r"\s*\d{1,2}\b", msg)
            and detect_intent(msg) is None
        ):
            state.reason = msg[:200]

        if step is Step.NAME:
            state.name = extract_name(msg) or state.name
        elif llm.name:
            state.name = llm.name[:60]

        raw_phone = find_phone(msg) or (find_phone(llm.phone) if llm.phone else None)
        if raw_phone and step in {Step.PHONE, Step.NAME, *CHOOSING}:
            try:
                state.phone = normalize_phone(raw_phone)
            except InvalidInputError:
                if step is Step.PHONE:
                    raise

    def _next_question(self, state: ConversationState, doctors: Sequence[Doctor]) -> Reply:
        doc = next((d for d in doctors if d.id == state.doctor_id), None)
        if not doc:
            state.step = Step.DOCTOR
            listing = "; ".join(f"{i}. {d.name} ({d.specialty})" for i, d in enumerate(doctors, 1))
            return Reply(
                text=self._t(state, "ask_doctor") + "\n" + listing,
                options=[f"{i}. {d.specialty}" for i, d in enumerate(doctors, 1)],
            )

        open_days = self.booking.next_open_days(doc.id, 3)
        days_txt = ", ".join(_fmt_day(d) for d in open_days) or "-"
        day_options = [d.isoformat() for d in open_days]
        if not state.day:
            state.step = Step.DATE
            if state.reschedule_code:
                text = self._t(
                    state,
                    "reschedule_ask_date",
                    code=state.reschedule_code,
                    doctor=doc.name,
                    old=state.reschedule_from,
                    days=days_txt,
                )
            else:
                text = self._t(state, "ask_date", doctor=doc.name, days=days_txt)
            return Reply(text=text, options=day_options)

        day = state.day
        slots = self.booking.available_slots(doc.id, day)
        if not slots:
            state.day = None
            state.step = Step.DATE
            if leave := self.booking.leave_on(doc.id, day):
                reason = f" ({leave.reason})" if leave.reason else ""
                return Reply(
                    text=self._t(state, "on_leave", doctor=doc.name, date=_fmt_day(day), reason=reason, days=days_txt),
                    options=day_options,
                )
            return Reply(
                text=self._t(state, "no_slots", doctor=doc.name, date=_fmt_day(day), days=days_txt), options=day_options
            )

        if not state.time and (wanted := state.want_time):
            state.want_time = None
            if wanted.hhmm in slots:
                state.time = wanted.hhmm
            elif wanted.approximate:
                state.time = nearest_slots(slots, wanted.hhmm, 1)[0]
            else:
                near = nearest_slots(slots, wanted.hhmm)
                state.step = Step.TIME
                return Reply(text=self._t(state, "bad_time", slots=", ".join(near)), options=near)
        if not state.time:
            state.step = Step.TIME
            show = slots[:8]
            return Reply(
                text=self._t(state, "ask_time", doctor=doc.name, date=_fmt_day(day), slots=", ".join(show)),
                options=show,
            )

        if not state.name:
            state.step = Step.NAME
            return Reply(text=self._t(state, "ask_name", time=state.time, date=_fmt_day(day)))
        if not state.phone:
            state.step = Step.PHONE
            return Reply(text=self._t(state, "ask_phone", name=state.name))

        state.step = Step.CONFIRM
        if state.reschedule_code:
            return Reply(
                text=self._t(
                    state,
                    "reschedule_confirm",
                    code=state.reschedule_code,
                    doctor=doc.name,
                    old=state.reschedule_from,
                    date=_fmt_day(day),
                    time=state.time,
                ),
                options=["Yes", "No"],
            )
        return Reply(
            text=self._t(
                state,
                "confirm",
                name=state.name,
                doctor=doc.name,
                specialty=doc.specialty,
                date=_fmt_day(day),
                time=state.time,
                fee=doc.fee,
            ),
            options=["Yes", "No"],
        )

    def _finish_reschedule(self, state: ConversationState) -> Reply:
        if not (state.reschedule_code and state.day and state.time):
            raise RuntimeError("confirm step reached with incomplete reschedule state")
        try:
            appt = self.booking.reschedule(state.reschedule_code, day=state.day, time=state.time)
        except SlotUnavailableError as e:
            state.time = None
            state.step = Step.TIME
            return Reply(text=e.message)
        state.reset()
        return Reply(
            text=self._t(
                state,
                "rescheduled",
                code=appt.code,
                doctor=appt.doctor_name,
                date=_fmt_day(dt.date.fromisoformat(appt.date)),
                time=appt.time,
            ),
            options=["My bookings", "Book appointment"],
            appointment=appt.to_dict(),
        )

    def _finish_booking(self, state: ConversationState, source: Channel) -> Reply:
        if not (state.doctor_id and state.day and state.time and state.name and state.phone):
            raise RuntimeError("confirm step reached with incomplete state")
        try:
            appt = self.booking.book(
                doctor_id=state.doctor_id,
                day=state.day,
                time=state.time,
                patient_name=state.name,
                patient_phone=state.phone,
                reason=state.reason or "",
                source=source,
            )
        except SlotUnavailableError as e:
            state.time = None
            state.step = Step.TIME
            return Reply(text=e.message)
        state.reset()
        return Reply(
            text=self._t(
                state,
                "booked",
                code=appt.code,
                doctor=appt.doctor_name,
                date=_fmt_day(dt.date.fromisoformat(appt.date)),
                time=appt.time,
            ),
            options=["Book another", "My bookings"],
            appointment=appt.to_dict(),
        )
