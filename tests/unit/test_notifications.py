from datetime import datetime

import httpx
import pytest

from app.core.errors import NotConfiguredError
from app.services.booking import BookingService
from app.services.notifications import DeliveryError, DisabledNotifier, TwilioWhatsAppNotifier
from app.services.scheduler import seconds_until
from tests.support import DENTIST, TOMORROW


class RecordingNotifier:
    configured = True

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail_for = fail_for or set()

    def send(self, to_phone: str, body: str) -> str:
        if to_phone in self.fail_for:
            raise DeliveryError("number not on WhatsApp")
        self.sent.append((to_phone, body))
        return "SM123"


def _book(booking: BookingService, time: str, phone: str):
    return booking.book(doctor_id=DENTIST, day=TOMORROW, time=time, patient_name="Ravi", patient_phone=phone)


def test_send_reminders_is_idempotent_and_survives_failures(booking: BookingService) -> None:
    _book(booking, "10:00", "9876543210")
    _book(booking, "10:30", "9000000000")
    notifier = RecordingNotifier(fail_for={"+919000000000"})

    first = booking.send_reminders(TOMORROW, notifier)
    assert (first["sent"], first["already_sent"], len(first["failed"])) == (1, 0, 1)
    assert "RESCHEDULE" in notifier.sent[0][1]

    notifier.fail_for.clear()
    second = booking.send_reminders(TOMORROW, notifier)
    assert (second["sent"], second["already_sent"]) == (1, 1)  # only the failed one is retried
    assert len(notifier.sent) == 2


def test_twilio_notifier_posts_whatsapp_message() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(201, json={"sid": "SMabc"})

    n = TwilioWhatsAppNotifier(
        account_sid="AC1", auth_token="tok", from_number="whatsapp:+14155238886", transport=httpx.MockTransport(handler)
    )
    assert n.send("+919876543210", "hello") == "SMabc"
    assert seen["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages.json"
    assert seen["auth"].startswith("Basic ")
    assert seen["body"] == {"To": "whatsapp:+919876543210", "From": "whatsapp:+14155238886", "Body": "hello"}


@pytest.mark.parametrize(
    "response",
    [httpx.Response(400, json={"message": "Invalid 'To' number"}), httpx.Response(500, text="oops")],
)
def test_twilio_errors_become_delivery_errors(response: httpx.Response) -> None:
    n = TwilioWhatsAppNotifier(
        account_sid="AC1", auth_token="t", from_number="+1", transport=httpx.MockTransport(lambda _: response)
    )
    with pytest.raises(DeliveryError, match=str(response.status_code)):
        n.send("+919876543210", "hi")


def test_disabled_notifier_explains_setup() -> None:
    with pytest.raises(NotConfiguredError, match="TWILIO_ACCOUNT_SID"):
        DisabledNotifier().send("+91", "x")


@pytest.mark.parametrize(
    ("now", "hour", "expected_hours"),
    [(datetime(2026, 11, 2, 9, 0), 18, 9), (datetime(2026, 11, 2, 19, 0), 18, 23), (datetime(2026, 11, 2, 18), 18, 24)],
)
def test_seconds_until_next_run(now: datetime, hour: int, expected_hours: int) -> None:
    assert seconds_until(now, hour) == expected_hours * 3600
