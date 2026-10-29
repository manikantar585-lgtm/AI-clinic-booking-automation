# Changelog

## 2.1.0

### Features
- **Emergency and self-harm detection.** Red-flag messages ("chest pain", "can't breathe", "ఛాతీ నొప్పి", suicidal thoughts)
  skip the booking flow and get an urgent alert with tap-to-call 108, the clinic, or Tele-MANAS 14416. The check runs before
  the LLM, handles negation ("no chest pain"), and keeps the booking in progress. The safety eval gates 100% recall in CI.
- **Reschedule.** In chat ("reschedule CB1A2B3C to friday 4pm"), by API (code + phone), and from the dashboard (slot picker).
  The code stays the same and the reminder is re-armed for the new time. Works in English and Telugu.
- **Doctor leave.** Block days off. Those days disappear from availability, patients are told the doctor is on leave
  (with the reason), and staff get a list of already-booked patients to call.
- **Insights dashboard.** Appointments per day by outcome, busiest hours, revenue and no-show rate by doctor, booking
  channels, booking lead time. 7/30/90-day ranges, hover tooltips, table view, light and dark mode.
- **CSV export** of a month's appointments, with protection against spreadsheet formula injection.
- **Automatic WhatsApp reminders** via Twilio. "Send all" from the dashboard, or daily at `REMINDER_SEND_HOUR`.
  Each booking is reminded at most once, failures are retried on the next run, and the scheduler is in-process.

### Internals
- Migration 4: `doctor_leaves` table, `appointments.rescheduled_count`, `appointments.reminder_sent_at`.
- `Notifier` interface (`TwilioWhatsAppNotifier` / `DisabledNotifier`), so tests never send real messages.
- Prompt `extract-v3` (adds the reschedule intent). NLU eval grew to 33 cases (96.2%).
- Tests: 119 → 160, coverage 96%.

## 2.0.0

### Architecture
- Split into layers: `core`, `domain`, `services`, `ai`, `api` (routers + schemas).
- Typed settings (`pydantic-settings`). Production refuses to start with a default admin token.
- One SQLite connection per request (WAL + busy timeout) replaces the global lock.
- Versioned migrations via `PRAGMA user_version`. v1 databases upgrade in place.
- Injected clock, so services never read the wall clock directly.

### Reliability
- Booking is an atomic check-then-insert (`BEGIN IMMEDIATE`) and retries booking-code collisions.
- Appointment status changes follow a transition table (e.g. a cancelled booking can't be reopened).
- Availability for the whole booking horizon takes one query instead of 30.
- Conversation sessions expire after inactivity. Corrupt or old-format state resets cleanly.

### AI
- LLM output is validated per field and grounded against real doctors and dates.
- LLM calls get retries with jittered backoff, connection pooling, and logs of latency, tokens and prompt version.
- Prompt moved to `app/ai/prompts.py` with few-shot examples and prompt-injection framing.
- The LLM is only called when the rules miss, never on name, phone or confirmation turns.
- New NLU eval suite (`evals/`) with a CI accuracy gate (rules: 95.8%).
- Rules understand romanized Telugu/Hindi ("repu", "ellundi", "5 baje", "pantlu").
- Fixed: "son" matched inside "person" and "reason". A child's symptoms now route to the pediatrician.

### Security
- Constant-time admin token check.
- Twilio webhook signature verification.
- Per-client rate limit on `/api/chat`.
- Web chat "my bookings" no longer reveals booking codes or doctors for an arbitrary phone number.
- WhatsApp cancellations are restricted to the verified sender's own bookings.
- Web clients can no longer address WhatsApp sessions by id.
- `/api/chat` no longer returns internal conversation state.
- Security headers on every response.

### API
- Consistent error envelope `{"error": {"code", "message", "details"}, "request_id"}`.
- Double booking returns `409`. Validation errors return `422` with per-field details.
- `/health` (liveness) and `/ready` (DB check).
- Admin list supports `limit` / `offset`. `work_days` accepts a list (the old string form still works).

### Tooling
- `pyproject.toml` with ruff, mypy `--strict`, pytest and coverage (≥ 90%).
- GitHub Actions: lint, types, tests on Python 3.12 and 3.13, eval gate, Docker build + smoke test.
- Multi-stage, non-root Docker image with a healthcheck, plus docker-compose.
- pre-commit hooks, Makefile, architecture doc, ADRs.
- Tests: 16 → 119.
