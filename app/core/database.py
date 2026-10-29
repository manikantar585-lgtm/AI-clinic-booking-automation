"""Database access for SQLite (local / single box) and Postgres (hosted), behind one small API.

Design notes
- The app writes portable SQL with `?` / `:name` placeholders and no engine-specific
  functions. `PgConnection` translates the placeholders for psycopg, and timestamps are
  produced in Python (`utcnow()`), so the services never branch on the engine.
- SQLite: one connection per request, WAL mode, `busy_timeout`, and `BEGIN IMMEDIATE` for
  check-then-write. Postgres: a connection pool and a transaction-scoped advisory lock
  for the same check-then-write guarantee. Both engines also enforce "one booking per
  slot" with a partial unique index.
- Connections run in autocommit mode and `transaction()` issues BEGIN/COMMIT itself,
  so it is always obvious where a transaction starts and ends.
- Schema changes are append-only, in lockstep: entry N of `MIGRATIONS` (SQLite) and of
  `PG_MIGRATIONS` describe the same change. SQLite tracks the version with
  `PRAGMA user_version`; Postgres with a `schema_version` table.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

log = logging.getLogger(__name__)

try:  # Postgres support is optional at runtime: only needed when DATABASE_URL is set.
    import psycopg
    from psycopg_pool import ConnectionPool

    _PG_INTEGRITY: tuple[type[Exception], ...] = (psycopg.IntegrityError,)
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]
    _PG_INTEGRITY = ()

#: Raised by either engine when a UNIQUE / CHECK / FK constraint is violated.
IntegrityError: tuple[type[Exception], ...] = (sqlite3.IntegrityError, *_PG_INTEGRITY)


def utcnow() -> str:
    """UTC timestamp in the stored text format ("YYYY-MM-DD HH:MM:SS"), which sorts correctly."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


class DbConnection(Protocol):
    def execute(self, sql: str, parameters: Any = ..., /) -> Any: ...
    def executemany(self, sql: str, parameters: Iterable[Any], /) -> Any: ...
    def close(self) -> None: ...


MIGRATIONS: list[str] = [
    # 1: initial schema (statements are idempotent so pre-migration databases upgrade cleanly)
    """
    CREATE TABLE IF NOT EXISTS doctors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        specialty TEXT NOT NULL,
        work_days TEXT NOT NULL DEFAULT '0,1,2,3,4,5',   -- Mon=0 ... Sun=6
        start_time TEXT NOT NULL DEFAULT '10:00',
        end_time TEXT NOT NULL DEFAULT '17:00',
        slot_minutes INTEGER NOT NULL DEFAULT 20,
        fee INTEGER NOT NULL DEFAULT 500,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        doctor_id INTEGER NOT NULL REFERENCES doctors(id),
        patient_name TEXT NOT NULL,
        patient_phone TEXT NOT NULL,
        date TEXT NOT NULL,
        time TEXT NOT NULL,
        reason TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'booked',
        source TEXT NOT NULL DEFAULT 'web',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_slot
        ON appointments(doctor_id, date, time) WHERE status = 'booked';
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    # 2: indexes for the admin dashboard and patient lookups
    """
    CREATE INDEX IF NOT EXISTS idx_appointments_date ON appointments(date, time);
    CREATE INDEX IF NOT EXISTS idx_appointments_phone ON appointments(patient_phone);
    CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
    """,
    # 3: audit columns for status changes
    """
    ALTER TABLE appointments ADD COLUMN updated_at TEXT;
    """,
    # 4: doctor leave, reschedule tracking, reminder delivery
    """
    CREATE TABLE IF NOT EXISTS doctor_leaves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doctor_id INTEGER NOT NULL REFERENCES doctors(id),
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        CHECK (start_date <= end_date)
    );
    CREATE INDEX IF NOT EXISTS idx_leaves_doctor ON doctor_leaves(doctor_id, end_date);
    ALTER TABLE appointments ADD COLUMN rescheduled_count INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE appointments ADD COLUMN reminder_sent_at TEXT;
    """,
]

_PG_NOW = "to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS')"

PG_MIGRATIONS: list[str] = [
    f"""
    CREATE TABLE IF NOT EXISTS doctors (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        specialty TEXT NOT NULL,
        work_days TEXT NOT NULL DEFAULT '0,1,2,3,4,5',
        start_time TEXT NOT NULL DEFAULT '10:00',
        end_time TEXT NOT NULL DEFAULT '17:00',
        slot_minutes INTEGER NOT NULL DEFAULT 20,
        fee INTEGER NOT NULL DEFAULT 500,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS appointments (
        id BIGSERIAL PRIMARY KEY,
        code TEXT UNIQUE NOT NULL,
        doctor_id BIGINT NOT NULL REFERENCES doctors(id),
        patient_name TEXT NOT NULL,
        patient_phone TEXT NOT NULL,
        date TEXT NOT NULL,
        time TEXT NOT NULL,
        reason TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'booked',
        source TEXT NOT NULL DEFAULT 'web',
        created_at TEXT NOT NULL DEFAULT {_PG_NOW}
    );
    CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_slot
        ON appointments(doctor_id, date, time) WHERE status = 'booked';
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT {_PG_NOW}
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_appointments_date ON appointments(date, time);
    CREATE INDEX IF NOT EXISTS idx_appointments_phone ON appointments(patient_phone);
    CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
    """,
    """
    ALTER TABLE appointments ADD COLUMN IF NOT EXISTS updated_at TEXT;
    """,
    f"""
    CREATE TABLE IF NOT EXISTS doctor_leaves (
        id BIGSERIAL PRIMARY KEY,
        doctor_id BIGINT NOT NULL REFERENCES doctors(id),
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT {_PG_NOW},
        CHECK (start_date <= end_date)
    );
    CREATE INDEX IF NOT EXISTS idx_leaves_doctor ON doctor_leaves(doctor_id, end_date);
    ALTER TABLE appointments ADD COLUMN IF NOT EXISTS rescheduled_count INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_sent_at TEXT;
    """,
]
assert len(PG_MIGRATIONS) == len(MIGRATIONS), "keep SQLite and Postgres migrations in lockstep"

DEMO_DOCTORS = [
    ("Dr. Anitha Rao", "General Physician", "0,1,2,3,4,5", "09:30", "13:30", 15, 400),
    ("Dr. Ravi Kumar", "Pediatrician", "0,1,2,3,4", "10:00", "16:00", 20, 500),
    ("Dr. Sneha Reddy", "Dermatologist", "1,3,5", "11:00", "17:00", 20, 600),
    ("Dr. Kiran Varma", "Dentist", "0,1,2,3,4,5", "10:00", "18:00", 30, 300),
    ("Dr. Lakshmi Prasad", "Gynecologist", "0,2,4", "10:00", "14:00", 20, 700),
]

_TABLES = ("sessions", "doctor_leaves", "appointments", "doctors", "schema_version")


# ---------------------------------------------------------------- Postgres adapter

_QUOTED = re.compile(r"('(?:[^']|'')*')")
_NAMED = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def to_pg_sql(sql: str) -> str:
    """Translate `?` / `:name` placeholders to psycopg's `%s` / `%(name)s`, outside string literals."""
    # psycopg treats every "%" as special once parameters are passed, even inside string
    # literals, so escape all of them; placeholders are only rewritten outside literals.
    parts = _QUOTED.split(sql.replace("%", "%%"))
    for i in range(0, len(parts), 2):  # even indexes are outside quotes
        parts[i] = _NAMED.sub(r"%(\1)s", parts[i].replace("?", "%s"))
    return "".join(parts)


class Row(dict[str, Any]):
    """A dict row that also supports positional access (`row[0]`), like `sqlite3.Row`."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor: Any) -> Any:
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values, strict=True))


class PgConnection:
    """Makes a psycopg connection look like the sqlite3 API the services use."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw

    def execute(self, sql: str, parameters: Sequence[Any] | dict[str, Any] = (), /) -> Any:
        if not parameters:  # no params: psycopg sends the text as-is, so don't escape %
            return self.raw.execute(sql)
        return self.raw.execute(to_pg_sql(sql), parameters)

    def executemany(self, sql: str, parameters: Iterable[Any], /) -> Any:
        with self.raw.cursor() as cur:
            cur.executemany(to_pg_sql(sql), list(parameters))
            return cur

    def close(self) -> None:  # the pool owns the connection
        pass


# ---------------------------------------------------------------- shared API


@contextmanager
def transaction(conn: DbConnection, *, immediate: bool = False) -> Iterator[DbConnection]:
    """Run a block atomically. `immediate=True` serialises writers up front, which is
    what a read-then-write (check slot is free, then insert) needs to be race-free."""
    if isinstance(conn, PgConnection):
        conn.execute("BEGIN")
        if immediate:
            conn.execute("SELECT pg_advisory_xact_lock(4242)")
    else:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


class Database:
    def __init__(self, path: str = ":memory:", *, url: str = "") -> None:
        self.path = path
        self.url = url
        self.engine = "postgres" if url else "sqlite"
        self._pool: Any = None
        if url:
            if psycopg is None:  # pragma: no cover
                raise RuntimeError("DATABASE_URL is set but psycopg is not installed")
            self._pool = ConnectionPool(
                url,
                min_size=1,
                max_size=10,
                kwargs={"autocommit": True, "row_factory": _row_factory},
                open=True,
            )

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def session(self) -> Iterator[DbConnection]:
        if self._pool is not None:
            with self._pool.connection() as raw:
                yield PgConnection(raw)
            return
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------ schema

    def migrate(self, conn: DbConnection) -> int:
        if isinstance(conn, PgConnection):
            return self._migrate_pg(conn)
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
            with transaction(conn, immediate=True):
                for statement in filter(str.strip, script.split(";")):
                    try:
                        conn.execute(statement)
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e):  # column added by an older build
                            raise
                conn.execute(f"PRAGMA user_version = {version}")
            log.info("applied migration %d", version)
        return len(MIGRATIONS)

    @staticmethod
    def _migrate_pg(conn: PgConnection) -> int:
        with transaction(conn, immediate=True):  # the advisory lock also guards concurrent boots
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            current = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
            for version, script in enumerate(PG_MIGRATIONS[current:], start=current + 1):
                for statement in filter(str.strip, script.split(";")):
                    conn.raw.execute(statement)  # DDL: no placeholders to translate
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
                log.info("applied migration %d", version)
        return len(PG_MIGRATIONS)

    def drop_all(self, conn: DbConnection) -> None:
        """Test helper: remove every table so each test starts from an empty schema."""
        if isinstance(conn, PgConnection):
            conn.raw.execute(f"DROP TABLE IF EXISTS {', '.join(_TABLES)} CASCADE")
        else:
            for table in _TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute("PRAGMA user_version = 0")

    @staticmethod
    def seed_demo_doctors(conn: DbConnection) -> None:
        with transaction(conn, immediate=True):
            if conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO doctors (name, specialty, work_days, start_time, end_time, slot_minutes, fee) "
                    "VALUES (?,?,?,?,?,?,?)",
                    DEMO_DOCTORS,
                )
