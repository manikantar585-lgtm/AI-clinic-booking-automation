"""Booking use-cases: doctors, availability, booking, cancellation, admin views."""

from __future__ import annotations

import logging
import secrets
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.core.database import transaction
from app.core.errors import InvalidInputError, InvalidTransitionError, NotFoundError, SlotUnavailableError
from app.domain.models import (
    Appointment,
    AppointmentStatus,
    Channel,
    Doctor,
    DoctorLeave,
    check_transition,
    normalize_phone,
)
from app.domain.scheduling import free_slots

if TYPE_CHECKING:
    from app.services.notifications import Notifier

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]

_APPOINTMENT_SELECT = (
    "SELECT a.*, d.name AS doctor_name, d.specialty, d.fee FROM appointments a JOIN doctors d ON d.id = a.doctor_id"
)


class BookingService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Clock,
        *,
        clinic_name: str = "the clinic",
        lead_minutes: int = 15,
        horizon_days: int = 30,
    ) -> None:
        self.conn = conn
        self.clock = clock
        self.clinic_name = clinic_name
        self.lead_minutes = lead_minutes
        self.horizon_days = horizon_days

    # ------------------------------------------------------------------ doctors

    def list_doctors(self, active_only: bool = True) -> list[Doctor]:
        sql = "SELECT * FROM doctors" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
        return [Doctor.from_row(r) for r in self.conn.execute(sql)]

    def get_doctor(self, doctor_id: int) -> Doctor:
        row = self.conn.execute("SELECT * FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
        if not row:
            raise NotFoundError("Doctor not found.")
        return Doctor.from_row(row)

    def add_doctor(
        self,
        *,
        name: str,
        specialty: str,
        work_days: list[int],
        start_time: str,
        end_time: str,
        slot_minutes: int,
        fee: int,
    ) -> Doctor:
        if start_time >= end_time:
            raise InvalidInputError("start_time must be before end_time.")
        with transaction(self.conn):
            cur = self.conn.execute(
                "INSERT INTO doctors (name, specialty, work_days, start_time, end_time, slot_minutes, fee) "
                "VALUES (?,?,?,?,?,?,?)",
                (name, specialty, ",".join(map(str, sorted(set(work_days)))), start_time, end_time, slot_minutes, fee),
            )
        log.info("doctor added", extra={"doctor_id": cur.lastrowid})
        return self.get_doctor(cur.lastrowid or 0)

    # ------------------------------------------------------------- availability

    def _taken(self, doctor_id: int, start: date, end: date) -> dict[str, set[str]]:
        rows = self.conn.execute(
            "SELECT date, time FROM appointments WHERE doctor_id = ? AND status = 'booked' AND date BETWEEN ? AND ?",
            (doctor_id, start.isoformat(), end.isoformat()),
        )
        taken: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            taken[r["date"]].add(r["time"])
        return taken

    def _leaves(self, doctor_id: int, start: date, end: date) -> list[DoctorLeave]:
        rows = self.conn.execute(
            "SELECT * FROM doctor_leaves WHERE doctor_id = ? AND start_date <= ? AND end_date >= ? ORDER BY start_date",
            (doctor_id, end.isoformat(), start.isoformat()),
        )
        return [DoctorLeave.from_row(r) for r in rows]

    def leave_on(self, doctor_id: int, day: date) -> DoctorLeave | None:
        return next(iter(self._leaves(doctor_id, day, day)), None)

    def available_slots(self, doctor_id: int, day: date) -> list[str]:
        doctor = self.get_doctor(doctor_id)
        if self.leave_on(doctor_id, day):
            return []
        taken = self._taken(doctor_id, day, day)
        return free_slots(doctor, day, taken[day.isoformat()], self.clock(), self.lead_minutes)

    def next_open_days(self, doctor_id: int, count: int = 3) -> list[date]:
        """Next days with at least one free slot (one query for the whole horizon)."""
        doctor = self.get_doctor(doctor_id)
        now = self.clock()
        first = now.date()
        last = first + timedelta(days=self.horizon_days - 1)
        taken = self._taken(doctor_id, first, last)
        leaves = self._leaves(doctor_id, first, last)
        days: list[date] = []
        for offset in range(self.horizon_days):
            day = first + timedelta(days=offset)
            if any(lv.covers(day.isoformat()) for lv in leaves):
                continue
            if free_slots(doctor, day, taken[day.isoformat()], now, self.lead_minutes):
                days.append(day)
                if len(days) == count:
                    break
        return days

    # ------------------------------------------------------------- appointments

    def book(
        self,
        *,
        doctor_id: int,
        day: date,
        time: str,
        patient_name: str,
        patient_phone: str,
        reason: str = "",
        source: Channel = Channel.WEB,
    ) -> Appointment:
        name = (patient_name or "").strip()
        if len(name) < 2:
            raise InvalidInputError("Please give the patient's name.")
        phone = normalize_phone(patient_phone)
        if day > self.clock().date() + timedelta(days=self.horizon_days):
            raise InvalidInputError(f"Bookings open {self.horizon_days} days ahead. Please pick an earlier date.")

        # Check-and-insert under one write lock. The partial unique index is the
        # second line of defence if another process slips in anyway.
        for _ in range(3):
            code = "CB" + secrets.token_hex(3).upper()
            try:
                with transaction(self.conn, immediate=True):
                    if time not in self.available_slots(doctor_id, day):
                        raise SlotUnavailableError(
                            "Sorry, that slot is not available any more. Please pick another time."
                        )
                    self.conn.execute(
                        "INSERT INTO appointments "
                        "(code, doctor_id, patient_name, patient_phone, date, time, reason, source) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (code, doctor_id, name, phone, day.isoformat(), time, reason[:200], source.value),
                    )
            except sqlite3.IntegrityError as e:
                if "appointments.code" in str(e):
                    continue  # booking-code collision: retry with a new code
                raise SlotUnavailableError("Sorry, someone just booked that slot. Please pick another time.") from e
            log.info("appointment booked", extra={"code": code, "doctor_id": doctor_id, "source": source.value})
            return self.get_appointment(code)
        raise RuntimeError("could not generate a unique booking code")

    def reschedule(self, code: str, *, day: date, time: str, phone: str | None = None) -> Appointment:
        """Move a booked appointment to another free slot with the same doctor. The code
        stays the same, and the reminder flag resets so the patient is reminded of the new time."""
        appt = self.get_for_patient(code, phone) if phone is not None else self.get_appointment(code)
        if appt.status is not AppointmentStatus.BOOKED:
            raise InvalidTransitionError(f"This booking is already {appt.status.value.replace('_', '-')}.")
        if (appt.date, appt.time) == (day.isoformat(), time):
            raise InvalidInputError("That's the same time as the current booking. Please pick a different slot.")
        if day > self.clock().date() + timedelta(days=self.horizon_days):
            raise InvalidInputError(f"Bookings open {self.horizon_days} days ahead. Please pick an earlier date.")
        try:
            with transaction(self.conn, immediate=True):
                if time not in self.available_slots(appt.doctor_id, day):
                    raise SlotUnavailableError("Sorry, that slot is not available. Please pick another time.")
                self.conn.execute(
                    "UPDATE appointments SET date = ?, time = ?, rescheduled_count = rescheduled_count + 1, "
                    "reminder_sent_at = NULL, updated_at = datetime('now') WHERE code = ?",
                    (day.isoformat(), time, appt.code),
                )
        except sqlite3.IntegrityError as e:
            raise SlotUnavailableError("Sorry, someone just booked that slot. Please pick another time.") from e
        log.info("appointment rescheduled", extra={"code": appt.code, "from": f"{appt.date} {appt.time}"})
        return self.get_appointment(appt.code)

    # ---------------------------------------------------------------- leave

    def add_leave(
        self, doctor_id: int, start: date, end: date, reason: str = ""
    ) -> tuple[DoctorLeave, list[Appointment]]:
        """Block days off for a doctor. Returns the leave plus the booked appointments it
        clashes with, so staff can call those patients. Existing bookings are not cancelled."""
        self.get_doctor(doctor_id)
        if end < start:
            raise InvalidInputError("The leave must end on or after the day it starts.")
        if end < self.clock().date():
            raise InvalidInputError("That leave is entirely in the past.")
        with transaction(self.conn):
            cur = self.conn.execute(
                "INSERT INTO doctor_leaves (doctor_id, start_date, end_date, reason) VALUES (?,?,?,?)",
                (doctor_id, start.isoformat(), end.isoformat(), reason.strip()[:120]),
            )
        row = self.conn.execute("SELECT * FROM doctor_leaves WHERE id = ?", (cur.lastrowid,)).fetchone()
        affected = self.list_appointments(
            doctor_id=doctor_id, status=AppointmentStatus.BOOKED, date_from=start, date_to=end, limit=500
        )
        log.info("doctor leave added", extra={"doctor_id": doctor_id, "affected": len(affected)})
        return DoctorLeave.from_row(row), affected

    def list_leaves(self, doctor_id: int | None = None) -> list[DoctorLeave]:
        """Current and upcoming leave. Past leave is history and isn't shown."""
        sql = "SELECT * FROM doctor_leaves WHERE end_date >= ?"
        args: list[Any] = [self.clock().date().isoformat()]
        if doctor_id:
            sql += " AND doctor_id = ?"
            args.append(doctor_id)
        return [DoctorLeave.from_row(r) for r in self.conn.execute(sql + " ORDER BY start_date", args)]

    def delete_leave(self, leave_id: int) -> None:
        with transaction(self.conn):
            if not self.conn.execute("DELETE FROM doctor_leaves WHERE id = ?", (leave_id,)).rowcount:
                raise NotFoundError("Leave not found.")

    # ---------------------------------------------------------------- lookups

    def get_appointment(self, code: str) -> Appointment:
        row = self.conn.execute(f"{_APPOINTMENT_SELECT} WHERE a.code = ?", (code.strip().upper(),)).fetchone()
        if not row:
            raise NotFoundError("I couldn't find a booking with that code.")
        return Appointment.from_row(row)

    def get_for_patient(self, code: str, phone: str) -> Appointment:
        """Look up a booking, proving ownership with the phone number. Any mismatch is a 404
        so the endpoint can't be used to discover which codes exist."""
        try:
            appt = self.get_appointment(code)
            if normalize_phone(phone) == appt.patient_phone:
                return appt
        except (NotFoundError, InvalidInputError):
            pass
        raise NotFoundError("Booking not found.")

    def cancel(self, code: str, phone: str | None = None) -> Appointment:
        appt = self.get_for_patient(code, phone) if phone is not None else self.get_appointment(code)
        return self.set_status(appt.code, AppointmentStatus.CANCELLED)

    def set_status(self, code: str, status: AppointmentStatus) -> Appointment:
        appt = self.get_appointment(code)
        check_transition(appt.status, status)
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE appointments SET status = ?, updated_at = datetime('now') WHERE code = ?",
                (status.value, appt.code),
            )
        log.info("appointment status changed", extra={"code": appt.code, "from": appt.status, "to": status})
        return self.get_appointment(appt.code)

    def list_appointments(
        self,
        *,
        day: date | None = None,
        doctor_id: int | None = None,
        phone: str | None = None,
        status: AppointmentStatus | None = None,
        upcoming_only: bool = False,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Appointment]:
        clauses: list[str] = []
        args: list[Any] = []
        if day:
            clauses.append("a.date = ?")
            args.append(day.isoformat())
        if doctor_id:
            clauses.append("a.doctor_id = ?")
            args.append(doctor_id)
        if phone:
            clauses.append("a.patient_phone = ?")
            args.append(normalize_phone(phone))
        if status:
            clauses.append("a.status = ?")
            args.append(status.value)
        if upcoming_only:
            clauses.append("a.date >= ?")
            args.append(self.clock().date().isoformat())
        if date_from:
            clauses.append("a.date >= ?")
            args.append(date_from.isoformat())
        if date_to:
            clauses.append("a.date <= ?")
            args.append(date_to.isoformat())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"{_APPOINTMENT_SELECT}{where} ORDER BY a.date, a.time LIMIT ? OFFSET ?"
        return [Appointment.from_row(r) for r in self.conn.execute(sql, [*args, limit, offset])]

    # --------------------------------------------------------------- reporting

    def stats(self) -> dict[str, int]:
        today = self.clock().date().isoformat()
        row = self.conn.execute(
            """
            SELECT
              COALESCE(SUM(a.status = 'booked' AND a.date = :today), 0)  AS today_booked,
              COALESCE(SUM(a.status = 'booked' AND a.date >= :today), 0) AS upcoming,
              COALESCE(SUM(a.status = 'completed'), 0)                   AS completed,
              COALESCE(SUM(a.status = 'cancelled'), 0)                   AS cancelled,
              COALESCE(SUM(a.status = 'no_show'), 0)                     AS no_show,
              COALESCE(SUM(CASE WHEN a.status = 'completed' THEN d.fee END), 0) AS revenue_completed
            FROM appointments a JOIN doctors d ON d.id = a.doctor_id
            """,
            {"today": today},
        ).fetchone()
        return dict(row)

    def _reminder_text(self, a: Appointment) -> str:
        return (
            f"Reminder from {self.clinic_name}: {a.patient_name}, your appointment with "
            f"{a.doctor_name} is on {a.date} at {a.time}. Booking code {a.code}. "
            f"Reply RESCHEDULE {a.code} to change it or CANCEL {a.code} to cancel."
        )

    def reminders_for(self, day: date) -> list[dict[str, Any]]:
        """Bookings on `day`, each with a ready-to-send WhatsApp/SMS reminder."""
        return [
            {**a.to_dict(), "message": self._reminder_text(a)}
            for a in self.list_appointments(day=day, status=AppointmentStatus.BOOKED, limit=1000)
        ]

    def send_reminders(self, day: date, notifier: Notifier) -> dict[str, Any]:
        """Send each not-yet-reminded booking on `day` its reminder. Safe to run repeatedly:
        a booking is marked as reminded only after the provider accepts the message."""
        from app.services.notifications import DeliveryError

        sent, already = 0, 0
        failed: list[dict[str, str]] = []
        for a in self.list_appointments(day=day, status=AppointmentStatus.BOOKED, limit=1000):
            if a.reminder_sent_at:
                already += 1
                continue
            try:
                notifier.send(a.patient_phone, self._reminder_text(a))
            except DeliveryError as e:
                log.warning("reminder failed", extra={"code": a.code, "error": str(e)})
                failed.append({"code": a.code, "patient_name": a.patient_name, "error": str(e)})
                continue
            with transaction(self.conn):
                self.conn.execute(
                    "UPDATE appointments SET reminder_sent_at = datetime('now') WHERE code = ?", (a.code,)
                )
            sent += 1
        log.info("reminders run", extra={"date": day.isoformat(), "sent": sent, "failed": len(failed)})
        return {"date": day.isoformat(), "sent": sent, "already_sent": already, "failed": failed}

    def insights(self, days: int = 30) -> dict[str, Any]:
        """Operational analytics for appointments dated in the last `days` days, today included."""
        today = self.clock().date()
        start = today - timedelta(days=days - 1)
        window = {"start": start.isoformat(), "end": today.isoformat()}
        q = self.conn.execute
        in_window = "date BETWEEN :start AND :end"

        daily = {(start + timedelta(days=i)).isoformat(): dict.fromkeys(AppointmentStatus, 0) for i in range(days)}
        for r in q(
            f"SELECT date, status, COUNT(*) n FROM appointments WHERE {in_window} GROUP BY date, status", window
        ):
            daily[r["date"]][AppointmentStatus(r["status"])] = r["n"]

        t = q(
            """SELECT COUNT(*) total,
                      COALESCE(SUM(a.status = 'completed'), 0) completed,
                      COALESCE(SUM(a.status = 'no_show'), 0) no_show,
                      COALESCE(SUM(a.status = 'cancelled'), 0) cancelled,
                      COALESCE(SUM(CASE WHEN a.status = 'completed' THEN d.fee END), 0) revenue,
                      AVG(julianday(a.date) - julianday(date(a.created_at))) lead_days
               FROM appointments a JOIN doctors d ON d.id = a.doctor_id
               WHERE a.date BETWEEN :start AND :end""",
            window,
        ).fetchone()
        attended = t["completed"] + t["no_show"]

        by_doctor = [
            dict(r)
            for r in q(
                """SELECT d.id, d.name, d.specialty, COUNT(a.id) total,
                          COALESCE(SUM(a.status = 'completed'), 0) completed,
                          COALESCE(SUM(a.status = 'no_show'), 0) no_show,
                          COALESCE(SUM(CASE WHEN a.status = 'completed' THEN d.fee END), 0) revenue
                   FROM doctors d LEFT JOIN appointments a
                     ON a.doctor_id = d.id AND a.date BETWEEN :start AND :end
                   WHERE d.active = 1 GROUP BY d.id ORDER BY revenue DESC, total DESC""",
                window,
            )
        ]
        by_channel = {
            r["source"]: r["n"]
            for r in q(f"SELECT source, COUNT(*) n FROM appointments WHERE {in_window} GROUP BY source", window)
        }
        by_hour = {
            int(r["h"]): r["n"]
            for r in q(
                f"SELECT substr(time, 1, 2) h, COUNT(*) n FROM appointments "
                f"WHERE {in_window} AND status != 'cancelled' GROUP BY h",
                window,
            )
        }
        upcoming = q(
            "SELECT COUNT(*) FROM appointments WHERE status = 'booked' AND date > ? AND date <= ?",
            (today.isoformat(), (today + timedelta(days=7)).isoformat()),
        ).fetchone()[0]
        return {
            "start": start.isoformat(),
            "end": today.isoformat(),
            "totals": {
                "appointments": t["total"],
                "completed": t["completed"],
                "no_show": t["no_show"],
                "cancelled": t["cancelled"],
                "revenue": t["revenue"],
                "no_show_rate": round(t["no_show"] / attended, 3) if attended else 0.0,
                "cancellation_rate": round(t["cancelled"] / t["total"], 3) if t["total"] else 0.0,
                "avg_lead_days": round(t["lead_days"], 1) if t["lead_days"] is not None else None,
                "upcoming_7_days": upcoming,
            },
            "daily": [{"date": d, **{st.value: n for st, n in counts.items()}} for d, counts in daily.items()],
            "by_doctor": by_doctor,
            "by_channel": by_channel,
            "by_hour": [{"hour": h, "count": by_hour.get(h, 0)} for h in range(7, 21)],
        }
