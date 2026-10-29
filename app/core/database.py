"""SQLite access: connection factory, explicit transactions and versioned migrations.

Design notes
- One connection per request (no global lock). WAL mode lets readers run while a
  booking is being written; `busy_timeout` makes concurrent writers wait instead of failing.
- Connections run in autocommit mode and `transaction()` issues BEGIN/COMMIT itself,
  so it is always obvious where a transaction starts and ends.
- Schema changes are append-only entries in `MIGRATIONS`, tracked with `PRAGMA user_version`.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

log = logging.getLogger(__name__)

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

DEMO_DOCTORS = [
    ("Dr. Anitha Rao", "General Physician", "0,1,2,3,4,5", "09:30", "13:30", 15, 400),
    ("Dr. Ravi Kumar", "Pediatrician", "0,1,2,3,4", "10:00", "16:00", 20, 500),
    ("Dr. Sneha Reddy", "Dermatologist", "1,3,5", "11:00", "17:00", 20, 600),
    ("Dr. Kiran Varma", "Dentist", "0,1,2,3,4,5", "10:00", "18:00", 30, 300),
    ("Dr. Lakshmi Prasad", "Gynecologist", "0,2,4", "10:00", "14:00", 20, 700),
]


@contextmanager
def transaction(conn: sqlite3.Connection, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    """Run a block atomically. `immediate=True` takes the write lock up front, which is
    what a read-then-write (check slot is free, then insert) needs to be race-free."""
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def migrate(self, conn: sqlite3.Connection) -> int:
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
    def seed_demo_doctors(conn: sqlite3.Connection) -> None:
        with transaction(conn, immediate=True):
            if conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO doctors (name, specialty, work_days, start_time, end_time, slot_minutes, fee) "
                    "VALUES (?,?,?,?,?,?,?)",
                    DEMO_DOCTORS,
                )
