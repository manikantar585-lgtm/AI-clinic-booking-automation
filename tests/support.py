"""Test constants and helpers shared across test modules."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.ai.assistant import Assistant, Reply

# Monday 2 Nov 2026, 09:00
NOW = datetime(2026, 11, 2, 9, 0)
TODAY = NOW.date()
TOMORROW = TODAY + timedelta(days=1)

# Seeded demo doctors
GP, PEDIATRICIAN, DERMATOLOGIST, DENTIST, GYNECOLOGIST = 1, 2, 3, 4, 5


class FrozenClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def chat(bot: Assistant, messages: list[str], session: str = "s1", **kwargs: object) -> Reply:
    """Send messages in order and return the last reply."""
    reply = None
    for m in messages:
        reply = bot.reply(session, m, **kwargs)  # type: ignore[arg-type]
    assert reply is not None
    return reply
