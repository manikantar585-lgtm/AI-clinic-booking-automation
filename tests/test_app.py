from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import booking, config, db
from app.assistant import Assistant, parse_date, parse_time
from app.main import create_app

# Monday 2 Nov 2026, 09:00 - fixed so tests don't depend on the real clock
NOW = datetime(2026, 11, 2, 9, 0)
TODAY = NOW.date()


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    return c


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "")


# ---------------- parsing

def test_parse_date():
    assert parse_date("tomorrow please", TODAY) == TODAY + timedelta(days=1)
    assert parse_date("రేపు రావచ్చా", TODAY) == TODAY + timedelta(days=1)
    assert parse_date("day after tomorrow", TODAY) == TODAY + timedelta(days=2)
    assert parse_date("on 5/11", TODAY) == date(2026, 11, 5)
    assert parse_date("2026-11-20", TODAY) == date(2026, 11, 20)
    assert parse_date("10 nov", TODAY) == date(2026, 11, 10)
    assert parse_date("friday", TODAY) == date(2026, 11, 6)
    assert parse_date("hello", TODAY) is None


def test_parse_time():
    assert parse_time("11am", False) == "11:00"
    assert parse_time("at 4", False) == "16:00"
    assert parse_time("4:30 pm", False) == "16:30"
    assert parse_time("11", True) == "11:00"
    assert parse_time("11", False) is None
    assert parse_time("evening", False) == "~16:30"


# ---------------- booking core

def test_slots_and_double_booking(conn):
    slots = booking.available_slots(conn, 4, TODAY, NOW)  # dentist 10:00-18:00, 30 min
    assert slots[0] == "10:00" and "17:30" in slots
    booking.book(conn, 4, TODAY, "10:00", "Ravi", "9876543210", now=NOW)
    assert "10:00" not in booking.available_slots(conn, 4, TODAY, NOW)
    with pytest.raises(booking.BookingError):
        booking.book(conn, 4, TODAY, "10:00", "Sita", "9876543211", now=NOW)


def test_closed_day_and_past(conn):
    sunday = TODAY + timedelta(days=6)
    assert booking.available_slots(conn, 1, sunday, NOW) == []
    assert booking.available_slots(conn, 1, TODAY - timedelta(days=1), NOW) == []
    late = datetime(2026, 11, 2, 12, 0)
    assert all(s >= "12:15" for s in booking.available_slots(conn, 1, TODAY, late))


def test_cancel_checks_phone(conn):
    a = booking.book(conn, 2, TODAY, "10:00", "Ravi", "9876543210", now=NOW)
    with pytest.raises(booking.BookingError):
        booking.cancel(conn, a["code"], "9999999999")
    assert booking.cancel(conn, a["code"], "+919876543210")["status"] == "cancelled"
    assert "10:00" in booking.available_slots(conn, 2, TODAY, NOW)


def test_bad_phone(conn):
    with pytest.raises(booking.BookingError):
        booking.book(conn, 2, TODAY, "10:00", "Ravi", "123", now=NOW)


# ---------------- assistant dialogue

def chat(bot, msgs, sid="s1"):
    out = None
    for m in msgs:
        out = bot.reply(sid, m, now=NOW)
    return out


def test_full_booking_step_by_step(conn):
    bot = Assistant(conn)
    r = chat(bot, ["Book appointment"])
    assert "Which doctor" in r["text"]
    r = chat(bot, ["4"])
    assert "When would you like" in r["text"]
    r = chat(bot, ["tomorrow"])
    assert "Free slots" in r["text"]
    r = chat(bot, ["11:00", "Ravi Teja", "98765 43210"])
    assert "Please confirm" in r["text"] and "Dentist" in r["text"]
    r = chat(bot, ["yes"])
    assert "Booked!" in r["text"]
    appt = r["appointment"]
    assert appt["date"] == "2026-11-03" and appt["time"] == "11:00" and appt["patient_name"] == "Ravi Teja"


def test_one_shot_message(conn):
    bot = Assistant(conn)
    r = chat(bot, ["I have tooth pain, can I come tomorrow at 11am?"])
    assert "patient's name" in r["text"]
    r = chat(bot, ["my name is Sita", "9876543210", "yes"])
    assert r["appointment"]["reason"].startswith("I have tooth pain")


def test_name_matching_doctor_is_not_a_doctor_switch(conn):
    bot = Assistant(conn)
    chat(bot, ["dentist tomorrow 11am"])
    r = chat(bot, ["Ravi"])  # also a doctor's first name
    assert "Thanks Ravi" in r["text"]
    assert bot.reply("s1", "9876543210", now=NOW)["state"]["doctor_id"] == 4


def test_unavailable_time_suggests_nearby(conn):
    bot = Assistant(conn)
    r = chat(bot, ["dentist tomorrow at 7am"])
    assert "isn't free" in r["text"] and r["options"]


def test_telugu_reply(conn):
    r = chat(Assistant(conn), ["రేపు పంటి నొప్పి"])
    assert "ఖాళీ సమయాలు" in r["text"]


def test_cancel_and_lookup_via_chat(conn):
    bot = Assistant(conn)
    code = chat(bot, ["dentist tomorrow 11am", "Ravi", "9876543210", "yes"])["appointment"]["code"]
    r = chat(bot, ["my bookings", "9876543210"], sid="s2")
    assert code in r["text"]
    r = chat(bot, [f"cancel {code}"], sid="s3")
    assert "cancelled" in r["text"]


def test_say_no_at_confirm(conn):
    r = chat(Assistant(conn), ["dentist tomorrow 11am", "Ravi", "9876543210", "no"])
    assert "didn't book" in r["text"]
    assert booking.list_appointments(conn) == []


# ---------------- HTTP API

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secret")
    return TestClient(create_app(str(tmp_path / "t.db")))


def _future_workday():
    d = booking.now_local().date() + timedelta(days=1)
    while d.weekday() == 6:
        d += timedelta(days=1)
    return d


def test_api_flow(client):
    assert client.get("/health").json()["ok"]
    assert len(client.get("/api/doctors").json()) == 5
    d = _future_workday()
    slots = client.get(f"/api/doctors/4/slots?date={d}").json()["slots"]
    r = client.post("/api/appointments", json={"doctor_id": 4, "date": str(d), "time": slots[0],
                                               "patient_name": "Ravi", "patient_phone": "9876543210"})
    assert r.status_code == 201
    code = r.json()["code"]
    assert client.get(f"/api/appointments/{code}?phone=9876543210").status_code == 200
    assert client.get(f"/api/appointments/{code}?phone=9000000000").status_code == 404
    assert client.get("/api/admin/stats").status_code == 401
    h = {"X-Admin-Token": "secret"}
    assert client.get("/api/admin/stats", headers=h).json()["upcoming"] == 1
    assert client.post(f"/api/admin/appointments/{code}/status", json={"status": "completed"}, headers=h).status_code == 200
    assert client.get("/api/admin/stats", headers=h).json()["revenue_completed"] == 300


def test_whatsapp_webhook(client):
    r = client.post("/webhook/whatsapp", data={"From": "whatsapp:+919876543210", "Body": "hi"})
    assert r.status_code == 200 and "<Message>" in r.text


def test_pages(client):
    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 200
