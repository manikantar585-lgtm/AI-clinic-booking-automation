"""In-process daily job: send tomorrow's appointment reminders at REMINDER_SEND_HOUR.

Runs inside the web process, so there is no extra worker to deploy. That's fine because
the job is idempotent (each booking is reminded at most once), so restarts, overlapping
replicas or a manual "Send now" from the dashboard can't double-message a patient.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.services.booking import BookingService

if TYPE_CHECKING:
    from fastapi import FastAPI

log = logging.getLogger(__name__)


def seconds_until(now: datetime, hour: int) -> float:
    """Seconds from `now` to the next `hour`:00 (today if still ahead, else tomorrow)."""
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def send_tomorrows_reminders(app: FastAPI) -> dict[str, Any]:
    settings = app.state.settings
    with app.state.db.session() as conn:
        booking = BookingService(conn, app.state.clock, clinic_name=settings.clinic_name)
        tomorrow = app.state.clock().date() + timedelta(days=1)
        return booking.send_reminders(tomorrow, app.state.notifier)


async def reminder_loop(app: FastAPI, hour: int) -> None:
    log.info("reminder scheduler started", extra={"hour": hour})
    while True:
        await asyncio.sleep(seconds_until(app.state.clock(), hour))
        try:
            await asyncio.to_thread(send_tomorrows_reminders, app)
        except Exception:
            log.exception("scheduled reminder run failed")
