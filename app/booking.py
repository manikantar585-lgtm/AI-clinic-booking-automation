"""Core booking logic: doctors, free slots, booking, cancelling."""
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import config
from .db import tx

PHONE_RE = re.compile(r"^\+?\d{10,13}$")


class BookingError(Exception):
    """Raised for anything the patient can fix (bad slot, bad phone...)."""


def now_local() -> datetime:
    tz = timezone(timedelta(minutes=config.TIMEZONE_OFFSET_MIN))
    return datetime.now(tz).replace(tzinfo=None)


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", phone or "")
    if digits.startswith("+"):
        digits = "+" + digits[1:].replace("+", "")
    if len(digits) == 10 and digits.isdigit():
        digits = "+91" + digits
    if not PHONE_RE.match(digits):
        raise BookingError("Please give a valid 10-digit mobile number.")
    return digits


# ---------- doctors ----------

def list_doctors(conn: sqlite3.Connection, active_only: bool = True) -> list[dict]:
    q = "SELECT * FROM doctors" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
    return [dict(r) for r in conn.execute(q)]


def get_doctor(conn, doctor_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM doctors WHERE id=?", (doctor_id,)).fetchone()
    return dict(row) if row else None


def add_doctor(conn, data: dict) -> dict:
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO doctors (name, specialty, work_days, start_time, end_time, slot_minutes, fee) "
            "VALUES (:name, :specialty, :work_days, :start_time, :end_time, :slot_minutes, :fee)",
            data,
        )
    return get_doctor(conn, cur.lastrowid)


# ---------- slots ----------

def _time_range(start: str, end: str, step: int) -> list[str]:
    t = datetime.strptime(start, "%H:%M")
    stop = datetime.strptime(end, "%H:%M")
    out = []
    while t + timedelta(minutes=step) <= stop:
        out.append(t.strftime("%H:%M"))
        t += timedelta(minutes=step)
    return out


def available_slots(conn, doctor_id: int, day: date, now: datetime | None = None) -> list[str]:
    doc = get_doctor(conn, doctor_id)
    if not doc or not doc["active"]:
        return []
    now = now or now_local()
    if day < now.date():
        return []
    work_days = {int(d) for d in doc["work_days"].split(",") if d.strip()}
    if day.weekday() not in work_days:
        return []
    taken = {
        r["time"]
        for r in conn.execute(
            "SELECT time FROM appointments WHERE doctor_id=? AND date=? AND status='booked'",
            (doctor_id, day.isoformat()),
        )
    }
    slots = [s for s in _time_range(doc["start_time"], doc["end_time"], doc["slot_minutes"]) if s not in taken]
    if day == now.date():
        cutoff = (now + timedelta(minutes=15)).strftime("%H:%M")
        slots = [s for s in slots if s >= cutoff]
    return slots


def next_open_days(conn, doctor_id: int, count: int = 3, now: datetime | None = None) -> list[date]:
    now = now or now_local()
    days, d = [], now.date()
    for _ in range(30):
        if available_slots(conn, doctor_id, d, now):
            days.append(d)
            if len(days) == count:
                break
        d += timedelta(days=1)
    return days


# ---------- appointments ----------

def _new_code() -> str:
    return "CB" + secrets.token_hex(3).upper()


def book(conn, doctor_id: int, day: date, time: str, name: str, phone: str,
         reason: str = "", source: str = "web", now: datetime | None = None) -> dict:
    name = (name or "").strip()
    if len(name) < 2:
        raise BookingError("Please give the patient's name.")
    phone = normalize_phone(phone)
    if time not in available_slots(conn, doctor_id, day, now):
        raise BookingError("Sorry, that slot is not available any more. Please pick another time.")
    code = _new_code()
    try:
        with tx(conn):
            conn.execute(
                "INSERT INTO appointments (code, doctor_id, patient_name, patient_phone, date, time, reason, source) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (code, doctor_id, name, phone, day.isoformat(), time, reason, source),
            )
    except sqlite3.IntegrityError:
        raise BookingError("Sorry, someone just booked that slot. Please pick another time.")
    return get_appointment(conn, code)


def get_appointment(conn, code: str) -> dict | None:
    row = conn.execute(
        "SELECT a.*, d.name AS doctor_name, d.specialty, d.fee FROM appointments a "
        "JOIN doctors d ON d.id = a.doctor_id WHERE a.code=?",
        (code.upper().strip(),),
    ).fetchone()
    return dict(row) if row else None


def cancel(conn, code: str, phone: str | None = None) -> dict:
    appt = get_appointment(conn, code)
    if not appt:
        raise BookingError("I couldn't find a booking with that code.")
    if phone is not None and normalize_phone(phone) != appt["patient_phone"]:
        raise BookingError("That phone number doesn't match the booking.")
    if appt["status"] != "booked":
        raise BookingError(f"This booking is already {appt['status']}.")
    with tx(conn):
        conn.execute("UPDATE appointments SET status='cancelled' WHERE code=?", (appt["code"],))
    return get_appointment(conn, appt["code"])


def set_status(conn, code: str, status: str) -> dict:
    if status not in {"booked", "cancelled", "completed", "no_show"}:
        raise BookingError("Unknown status.")
    if not get_appointment(conn, code):
        raise BookingError("Booking not found.")
    with tx(conn):
        conn.execute("UPDATE appointments SET status=? WHERE code=?", (status, code.upper()))
    return get_appointment(conn, code)


def list_appointments(conn, day: str | None = None, doctor_id: int | None = None,
                      phone: str | None = None, status: str | None = None) -> list[dict]:
    q = ("SELECT a.*, d.name AS doctor_name, d.specialty FROM appointments a "
         "JOIN doctors d ON d.id=a.doctor_id WHERE 1=1")
    args: list = []
    if day:
        q += " AND a.date=?"; args.append(day)
    if doctor_id:
        q += " AND a.doctor_id=?"; args.append(doctor_id)
    if phone:
        q += " AND a.patient_phone=?"; args.append(normalize_phone(phone))
    if status:
        q += " AND a.status=?"; args.append(status)
    q += " ORDER BY a.date, a.time"
    return [dict(r) for r in conn.execute(q, args)]


def stats(conn, now: datetime | None = None) -> dict:
    today = (now or now_local()).date().isoformat()
    one = lambda q, *a: conn.execute(q, a).fetchone()[0]  # noqa: E731
    return {
        "today_booked": one("SELECT COUNT(*) FROM appointments WHERE date=? AND status='booked'", today),
        "upcoming": one("SELECT COUNT(*) FROM appointments WHERE date>=? AND status='booked'", today),
        "completed": one("SELECT COUNT(*) FROM appointments WHERE status='completed'"),
        "cancelled": one("SELECT COUNT(*) FROM appointments WHERE status='cancelled'"),
        "no_show": one("SELECT COUNT(*) FROM appointments WHERE status='no_show'"),
        "revenue_completed": one(
            "SELECT COALESCE(SUM(d.fee),0) FROM appointments a JOIN doctors d ON d.id=a.doctor_id "
            "WHERE a.status='completed'"),
    }


def reminders_for(conn, day: date) -> list[dict]:
    """Bookings on `day` with a ready-to-send reminder text (for WhatsApp/SMS)."""
    out = []
    for a in list_appointments(conn, day=day.isoformat(), status="booked"):
        a["message"] = (
            f"Reminder from {config.CLINIC_NAME}: {a['patient_name']}, your appointment with "
            f"{a['doctor_name']} is on {a['date']} at {a['time']}. Booking code {a['code']}. "
            f"Reply CANCEL {a['code']} to cancel."
        )
        out.append(a)
    return out
