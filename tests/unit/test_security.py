from datetime import UTC, datetime, timedelta

from app.ai.assistant import ConversationState, Step
from app.core.security import RateLimiter, tokens_match, twilio_signature, verify_twilio_signature
from app.services.sessions import SessionStore


def test_tokens_match() -> None:
    assert tokens_match("s3cret", "s3cret")
    assert not tokens_match("s3cre", "s3cret")
    assert not tokens_match("", "")  # an unset token never authenticates


def test_twilio_signature_roundtrip_and_tamper() -> None:
    params = {"From": "whatsapp:+919876543210", "Body": "hi"}
    sig = twilio_signature("tok", "https://x.test/webhook/whatsapp", params)
    assert verify_twilio_signature("tok", "https://x.test/webhook/whatsapp", params, sig)
    assert not verify_twilio_signature("tok", "https://x.test/webhook/whatsapp", {**params, "Body": "bye"}, sig)
    assert not verify_twilio_signature("other", "https://x.test/webhook/whatsapp", params, sig)


def test_rate_limiter_is_per_key() -> None:
    limiter = RateLimiter(limit=2)
    assert limiter.allow("a") and limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")


def test_session_expires_after_ttl(conn) -> None:
    store = SessionStore(conn, ConversationState, timedelta(minutes=30))
    store.save("s", ConversationState(step=Step.NAME, doctor_id=4))
    assert store.load("s").doctor_id == 4
    stale = (datetime.now(UTC) - timedelta(minutes=31)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE sessions SET updated_at = ?", (stale,))
    assert store.load("s") == ConversationState()
    assert store.purge_expired() == 1


def test_corrupt_session_state_starts_fresh(conn) -> None:
    conn.execute("INSERT INTO sessions (id, state) VALUES ('s', '{\"step\": \"nonsense\"}')")
    assert SessionStore(conn, ConversationState, timedelta(minutes=30)).load("s") == ConversationState()
