"""Conversation state storage with idle expiry."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from pydantic import BaseModel, ValidationError

from app.core.database import transaction


class SessionStore[S: BaseModel]:
    """Persists one pydantic state model per session id.

    A session idle for longer than `ttl` starts fresh, so a patient who comes back
    tomorrow isn't dropped into yesterday's half-finished booking.
    """

    def __init__(self, conn: sqlite3.Connection, model: type[S], ttl: timedelta) -> None:
        self.conn = conn
        self.model = model
        self.ttl = ttl

    def load(self, session_id: str) -> S:
        row = self.conn.execute(
            "SELECT state FROM sessions WHERE id = ? AND updated_at >= datetime('now', ?)",
            (session_id, f"-{int(self.ttl.total_seconds())} seconds"),
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
                "INSERT INTO sessions (id, state, updated_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
                (session_id, state.model_dump_json(exclude_defaults=True)),
            )

    def purge_expired(self) -> int:
        with transaction(self.conn):
            cur = self.conn.execute(
                "DELETE FROM sessions WHERE updated_at < datetime('now', ?)",
                (f"-{int(self.ttl.total_seconds())} seconds",),
            )
        return cur.rowcount
