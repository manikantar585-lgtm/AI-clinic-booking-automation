"""SQLite storage: doctors, appointments and chat sessions."""
import json
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
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
    date TEXT NOT NULL,          -- YYYY-MM-DD
    time TEXT NOT NULL,          -- HH:MM
    reason TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'booked',   -- booked | cancelled | completed | no_show
    source TEXT NOT NULL DEFAULT 'web',      -- web | whatsapp | admin | api
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_slot
    ON appointments(doctor_id, date, time) WHERE status = 'booked';
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

SEED_DOCTORS = [
    ("Dr. Anitha Rao", "General Physician", "0,1,2,3,4,5", "09:30", "13:30", 15, 400),
    ("Dr. Ravi Kumar", "Pediatrician", "0,1,2,3,4", "10:00", "16:00", 20, 500),
    ("Dr. Sneha Reddy", "Dermatologist", "1,3,5", "11:00", "17:00", 20, 600),
    ("Dr. Kiran Varma", "Dentist", "0,1,2,3,4,5", "10:00", "18:00", 30, 300),
    ("Dr. Lakshmi Prasad", "Gynecologist", "0,2,4", "10:00", "14:00", 20, 700),
]


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db(conn: sqlite3.Connection, seed: bool = True) -> None:
    conn.executescript(SCHEMA)
    if seed and conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO doctors (name, specialty, work_days, start_time, end_time, slot_minutes, fee) "
            "VALUES (?,?,?,?,?,?,?)",
            SEED_DOCTORS,
        )
    conn.commit()


def get_session(conn, session_id: str) -> dict:
    row = conn.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
    return json.loads(row["state"]) if row else {}


def save_session(conn, session_id: str, state: dict) -> None:
    with tx(conn):
        conn.execute(
            "INSERT INTO sessions (id, state, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
            (session_id, json.dumps(state)),
        )
