from datetime import timedelta

from app.domain.models import AppointmentStatus, Channel
from app.services.booking import BookingService
from tests.support import DENTIST, GP, TODAY, FrozenClock


def test_insights_aggregates(booking: BookingService, clock: FrozenClock) -> None:
    # Book on past days by moving the clock back, then return to "today".
    real_now = clock.now
    clock.now = real_now - timedelta(days=3)
    past = clock.now.date()
    a = booking.book(doctor_id=DENTIST, day=past, time="10:00", patient_name="Asha", patient_phone="9000000001")
    b = booking.book(
        doctor_id=DENTIST,
        day=past,
        time="10:30",
        patient_name="Bala",
        patient_phone="9000000002",
        source=Channel.WHATSAPP,
    )
    c = booking.book(doctor_id=GP, day=past, time="10:00", patient_name="Chitra", patient_phone="9000000003")
    clock.now = real_now
    booking.set_status(a.code, AppointmentStatus.COMPLETED)
    booking.set_status(b.code, AppointmentStatus.NO_SHOW)
    booking.set_status(c.code, AppointmentStatus.CANCELLED)
    booking.book(
        doctor_id=DENTIST, day=TODAY + timedelta(days=2), time="11:00", patient_name="Deepa", patient_phone="9000000004"
    )

    data = booking.insights(days=7)
    t = data["totals"]
    assert (t["appointments"], t["completed"], t["no_show"], t["cancelled"], t["revenue"]) == (3, 1, 1, 1, 300)
    assert t["no_show_rate"] == 0.5 and t["cancellation_rate"] == round(1 / 3, 3)
    assert t["upcoming_7_days"] == 1
    assert len(data["daily"]) == 7 and data["daily"][-1]["date"] == TODAY.isoformat()
    day = next(d for d in data["daily"] if d["date"] == past.isoformat())
    assert (day["completed"], day["no_show"], day["cancelled"]) == (1, 1, 1)
    assert data["by_channel"] == {"web": 2, "whatsapp": 1}
    assert data["by_doctor"][0]["name"] == "Dr. Kiran Varma" and data["by_doctor"][0]["revenue"] == 300
    assert next(h for h in data["by_hour"] if h["hour"] == 10)["count"] == 2  # cancelled one excluded


def test_insights_empty_window(booking: BookingService) -> None:
    t = booking.insights(days=30)["totals"]
    assert t["appointments"] == 0 and t["no_show_rate"] == 0.0 and t["avg_lead_days"] is None
