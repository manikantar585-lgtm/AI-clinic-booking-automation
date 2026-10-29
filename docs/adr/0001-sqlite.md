# ADR 0001: SQLite as the system of record

**Status:** accepted · amended in 2.2: Postgres is supported via `DATABASE_URL` for hosted deploys (see `app/core/database.py`)

## Context
The target is one small clinic: tens to a few hundred bookings a day, one app instance, and
deployment on free or cheap hosts. The operators are not database administrators.

## Decision
Use SQLite in WAL mode, with one connection per request, `busy_timeout`, explicit
`BEGIN IMMEDIATE` for check-then-write operations, and versioned migrations.

## Consequences
- No database server to run, back up or pay for. A backup is a file copy.
- Double booking is prevented by the write lock *and* a partial unique index.
- Writes are serialised. That is fine at this scale; `tests/unit/test_booking_service.py` runs 8
  concurrent writers against one slot and expects exactly one winner.
- **Revisit when** a multi-clinic deployment or more than one replica is needed. The SQL is
  portable and confined to `app/services` and `app/core/database.py`, so moving to Postgres is
  contained.
