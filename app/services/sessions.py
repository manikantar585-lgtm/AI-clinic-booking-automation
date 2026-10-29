"""Conversation state storage with idle expiry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ValidationError

from app.core.database import DbConnection, transaction, utcnow


class SessionStore[S: BaseModel]:
    """Persists one pydantic state model per session id.

    A session idle for longer than `ttl` starts fresh, so a patient who comes back
    tomorrow isn't dropped into yesterday's half-finished booking.
    """

    def __init__(self, conn: DbConnection, model: type[S], ttl: timedelta) -> None:
        self.conn = conn
        self.model = model
        self.ttl = ttl

    def _cutoff(self) -> str:
        return (datetime.now(UTC) - self.ttl).strftime("%Y-%m-%d %H:%M:%S")

    def load(self, session_id: str) -> S:
        row = self.conn.execute(
            "SELECT state FROM sessions WHERE id = ? AND updated_at >= ?",
            (session_id, self._cutoff()),
        ).fetchone()
        if row:
            try:
                return self.model.model_validate_json(row["state"])
            except ValidationError:
                pass  # state written by an older schema: start over
        return self.model()

    def save(self, session_id: str, state: S) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO sessions (id, state, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
                (session_id, state.model_dump_json(exclude_defaults=True), utcnow()),
            )

    def purge_expired(self) -> int:
        with transaction(self.conn):
            cur = self.conn.execute(
                "DELETE FROM sessions WHERE updated_at < ?",
                (self._cutoff(),),
            )
        return int(cur.rowcount)
