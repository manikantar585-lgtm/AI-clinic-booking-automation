"""HTTP tests for reschedule, doctor leave, insights, CSV export and reminder sending."""

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.support import DENTIST, TOMORROW
from tests.unit.test_notifications import RecordingNotifier

ADMIN = {"X-Admin-Token": "test-admin-token"}


def _book(client: TestClient, time: str = "10:00", name: str = "Ravi", phone: str = "9876543210") -> dict:
    r = client.post(
        "/api/appointments",
        json={"doctor_id": DENTIST, "date": str(TOMORROW), "time": time, "patient_name": name, "patient_phone": phone},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_patient_reschedule_requires_phone(client: TestClient) -> None:
    code = _book(client)["code"]
    body = {"date": str(TOMORROW), "time": "15:00"}
    assert client.post(f"/api/appointments/{code}/reschedule", json={**body, "phone": "9000000000"}).status_code == 404
    r = client.post(f"/api/appointments/{code}/reschedule", json={**body, "phone": "9876543210"})
    assert r.status_code == 200 and r.json()["time"] == "15:00" and r.json()["rescheduled_count"] == 1


def test_admin_reschedule_conflict_is_409(client: TestClient) -> None:
    code = _book(client)["code"]
    _book(client, time="12:00", phone="9000000000")
    r = client.post(
        f"/api/admin/appointments/{code}/reschedule", json={"date": str(TOMORROW), "time": "12:00"}, headers=ADMIN
    )
    assert r.status_code == 409


def test_leave_lifecycle(client: TestClient) -> None:
    code = _book(client)["code"]
    r = client.post(
        f"/api/admin/doctors/{DENTIST}/leaves",
        headers=ADMIN,
        json={"start_date": str(TOMORROW), "end_date": str(TOMORROW), "reason": "Conference"},
    )
    assert r.status_code == 201
    assert [a["code"] for a in r.json()["affected"]] == [code]
    leave_id = r.json()["leave"]["id"]
    assert client.get(f"/api/doctors/{DENTIST}/slots", params={"date": str(TOMORROW)}).json()["slots"] == []
    assert [lv["id"] for lv in client.get("/api/admin/leaves", headers=ADMIN).json()] == [leave_id]
    assert client.delete(f"/api/admin/leaves/{leave_id}", headers=ADMIN).status_code == 204
    assert client.get(f"/api/doctors/{DENTIST}/slots", params={"date": str(TOMORROW)}).json()["slots"]


def test_leave_requires_admin(client: TestClient) -> None:
    r = client.post(
        f"/api/admin/doctors/{DENTIST}/leaves", json={"start_date": str(TOMORROW), "end_date": str(TOMORROW)}
    )
    assert r.status_code == 401


def test_insights_endpoint(client: TestClient) -> None:
    _book(client)
    r = client.get("/api/admin/insights", params={"days": 7}, headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert len(body["daily"]) == 7 and {"totals", "by_doctor", "by_channel", "by_hour"} <= set(body)
    assert client.get("/api/admin/insights", params={"days": 2}, headers=ADMIN).status_code == 422


def test_csv_export_neutralises_formulas(client: TestClient) -> None:
    _book(client, name="=HYPERLINK(evil)")
    params = {"from": str(TOMORROW - timedelta(days=1)), "to": str(TOMORROW)}
    r = client.get("/api/admin/appointments.csv", params=params, headers=ADMIN)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.lstrip("﻿").splitlines()
    assert lines[0].startswith("date,time,code,patient_name")
    assert "'=HYPERLINK(evil)" in lines[1]
    assert ",+919876543210," in lines[1]  # phone numbers are not mangled


def test_config_and_reminders_not_configured(client: TestClient) -> None:
    cfg = client.get("/api/admin/config", headers=ADMIN).json()
    assert cfg == {"whatsapp_sending": False, "reminder_send_hour": None, "llm_enabled": False}
    r = client.post("/api/admin/reminders/send", params={"date": str(TOMORROW)}, headers=ADMIN)
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_configured"


def test_reminders_send_via_notifier(settings: Settings, clock) -> None:
    from app.main import create_app

    notifier = RecordingNotifier()
    with TestClient(create_app(settings, clock=clock, notifier=notifier)) as client:
        _book(client)
        r = client.post("/api/admin/reminders/send", params={"date": str(TOMORROW)}, headers=ADMIN)
        assert r.status_code == 200 and r.json()["sent"] == 1
        assert notifier.sent[0][0] == "+919876543210"
        [rem] = client.get("/api/admin/reminders", params={"date": str(TOMORROW)}, headers=ADMIN).json()
        assert rem["reminder_sent_at"]
        again = client.post("/api/admin/reminders/send", params={"date": str(TOMORROW)}, headers=ADMIN).json()
        assert (again["sent"], again["already_sent"]) == (0, 1)


def test_chat_emergency_alert_over_http(client: TestClient) -> None:
    r = client.post("/api/chat", json={"session_id": "web-em1", "message": "my father has chest pain"})
    assert r.json()["alert"] == "emergency"


def test_scheduled_job_sends_tomorrows_reminders(settings: Settings, clock) -> None:
    from app.main import create_app
    from app.services.scheduler import send_tomorrows_reminders

    notifier = RecordingNotifier()
    hourly = settings.model_copy(update={"reminder_send_hour": 18})
    with TestClient(create_app(hourly, clock=clock, notifier=notifier)) as client:
        _book(client)
        assert send_tomorrows_reminders(client.app)["sent"] == 1  # type: ignore[arg-type]
        assert send_tomorrows_reminders(client.app)["sent"] == 0  # type: ignore[arg-type]
    assert len(notifier.sent) == 1  # app shut down cleanly with the scheduler task cancelled
