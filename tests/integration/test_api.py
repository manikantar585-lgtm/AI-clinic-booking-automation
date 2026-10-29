"""End-to-end HTTP tests through the real app factory, middleware and error handlers."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.core.security import twilio_signature
from tests.support import DENTIST, TOMORROW

ADMIN = {"X-Admin-Token": "test-admin-token"}


def _book(client: TestClient, time: str = "10:00") -> dict:
    r = client.post(
        "/api/appointments",
        json={
            "doctor_id": DENTIST,
            "date": str(TOMORROW),
            "time": time,
            "patient_name": "Ravi",
            "patient_phone": "9876543210",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_ops_endpoints(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready").json()
    assert ready["database"] == "ok" and ready["llm_enabled"] is False


def test_pages_and_security_headers(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/admin").status_code == 200


def test_request_id_is_echoed_or_generated(client: TestClient) -> None:
    assert client.get("/health", headers={"X-Request-ID": "abc123"}).headers["x-request-id"] == "abc123"
    assert len(client.get("/health").headers["x-request-id"]) == 16


def test_booking_lifecycle(client: TestClient) -> None:
    assert len(client.get("/api/doctors").json()) == 5
    slots = client.get(f"/api/doctors/{DENTIST}/slots", params={"date": str(TOMORROW)}).json()["slots"]
    assert slots[0] == "10:00"

    code = _book(client)["code"]
    assert client.get(f"/api/appointments/{code}", params={"phone": "9876543210"}).status_code == 200
    assert client.get(f"/api/appointments/{code}", params={"phone": "9000000000"}).status_code == 404

    r = client.post(f"/api/appointments/{code}/cancel", json={"phone": "9876543210"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_double_booking_is_409_with_error_envelope(client: TestClient) -> None:
    _book(client)
    r = client.post(
        "/api/appointments",
        json={
            "doctor_id": DENTIST,
            "date": str(TOMORROW),
            "time": "10:00",
            "patient_name": "Sita",
            "patient_phone": "9000000000",
        },
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "slot_unavailable" and body["request_id"] == r.headers["x-request-id"]


def test_validation_errors_are_422_with_field_details(client: TestClient) -> None:
    r = client.post("/api/appointments", json={"doctor_id": DENTIST, "date": "nope", "time": "9am"})
    assert r.status_code == 422
    fields = {d["field"] for d in r.json()["error"]["details"]}
    assert {"date", "time", "patient_name", "patient_phone"} <= fields


def test_unknown_doctor_is_404(client: TestClient) -> None:
    assert client.get("/api/doctors/99/slots", params={"date": str(TOMORROW)}).json()["error"]["code"] == "not_found"


def test_admin_requires_token(client: TestClient) -> None:
    assert client.get("/api/admin/stats").status_code == 401
    assert client.get("/api/admin/stats", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_admin_workflow(client: TestClient) -> None:
    code = _book(client)["code"]
    assert client.get("/api/admin/stats", headers=ADMIN).json()["upcoming"] == 1
    rows = client.get("/api/admin/appointments", params={"date": str(TOMORROW)}, headers=ADMIN).json()
    assert [a["code"] for a in rows] == [code]

    r = client.post(f"/api/admin/appointments/{code}/status", json={"status": "completed"}, headers=ADMIN)
    assert r.status_code == 200
    assert client.get("/api/admin/stats", headers=ADMIN).json()["revenue_completed"] == 300

    r = client.post(f"/api/admin/appointments/{code}/status", json={"status": "booked"}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_transition"
    r = client.post(f"/api/admin/appointments/{code}/status", json={"status": "teleported"}, headers=ADMIN)
    assert r.status_code == 422


def test_admin_add_doctor_accepts_legacy_work_days(client: TestClient) -> None:
    r = client.post(
        "/api/admin/doctors", headers=ADMIN, json={"name": "Dr. New", "specialty": "ENT", "work_days": "0,2,4"}
    )
    assert r.status_code == 201 and r.json()["work_days"] == [0, 2, 4]


def test_reminders(client: TestClient) -> None:
    _book(client)
    [rem] = client.get("/api/admin/reminders", params={"date": str(TOMORROW)}, headers=ADMIN).json()
    assert rem["message"].startswith("Reminder from")


def test_chat_round_trip_does_not_leak_state(client: TestClient) -> None:
    r = client.post("/api/chat", json={"session_id": "web-abc1", "message": "dentist tomorrow 11am"})
    assert r.status_code == 200
    assert set(r.json()) == {"text", "options", "appointment", "alert"}


def test_chat_rejects_server_reserved_session_ids(client: TestClient) -> None:
    r = client.post("/api/chat", json={"session_id": "wa:whatsapp:+919876543210", "message": "my bookings"})
    assert r.status_code == 422


def test_chat_rate_limit(make_client, settings: Settings) -> None:
    client = make_client(settings.model_copy(update={"chat_rate_limit_per_minute": 2}))
    body = {"session_id": "web-abc1", "message": "hi"}
    assert [client.post("/api/chat", json=body).status_code for _ in range(3)] == [200, 200, 429]


def test_whatsapp_webhook_without_signing(client: TestClient) -> None:
    r = client.post("/webhook/whatsapp", data={"From": "whatsapp:+919876543210", "Body": "hi"})
    assert r.status_code == 200 and "<Message>" in r.text and "Book appointment" in r.text


@pytest.fixture
def signed_client(make_client, settings: Settings) -> TestClient:
    return make_client(
        settings.model_copy(
            update={"twilio_auth_token": SecretStr("twilio-tok"), "public_base_url": "https://clinic.test"}
        )
    )


def test_whatsapp_webhook_rejects_bad_signature(signed_client: TestClient) -> None:
    r = signed_client.post(
        "/webhook/whatsapp",
        data={"From": "whatsapp:+919876543210", "Body": "hi"},
        headers={"X-Twilio-Signature": "forged"},
    )
    assert r.status_code == 403


def test_whatsapp_webhook_accepts_valid_signature(signed_client: TestClient) -> None:
    form = {"From": "whatsapp:+919876543210", "Body": "my bookings"}
    sig = twilio_signature("twilio-tok", "https://clinic.test/webhook/whatsapp", form)
    r = signed_client.post("/webhook/whatsapp", data=form, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200 and "no upcoming bookings" in r.text  # verified sender: no phone question


def test_production_refuses_default_admin_token() -> None:
    with pytest.raises(ValueError, match="ADMIN_TOKEN"):
        Settings(_env_file=None, env="production", admin_token="change-me")


def test_empty_env_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMINDER_SEND_HOUR", "")
    monkeypatch.setenv("SESSION_TTL_MINUTES", "")
    s = Settings(_env_file=None)
    assert s.reminder_send_hour is None and s.session_ttl_minutes == 30
