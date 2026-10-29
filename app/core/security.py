"""Authentication helpers and a small in-process rate limiter."""

from __future__ import annotations

import base64
import hashlib
import hmac
import threading
import time
from collections import deque
from collections.abc import Mapping


def tokens_match(given: str, expected: str) -> bool:
    """Constant-time comparison, so the admin token can't be guessed by timing."""
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def twilio_signature(auth_token: str, url: str, params: Mapping[str, str]) -> str:
    """Compute `X-Twilio-Signature` as documented by Twilio (HMAC-SHA1 over url + sorted params)."""
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_twilio_signature(auth_token: str, url: str, params: Mapping[str, str], signature: str) -> bool:
    return hmac.compare_digest(twilio_signature(auth_token, url, params), signature or "")


class RateLimiter:
    """Sliding-window limiter keyed by caller.

    Per process only. Good enough to stop one client from burning LLM credits; use
    a shared store (e.g. Redis) when running more than one replica.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:  # drop idle keys so memory stays bounded
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] <= self.window}
            return True
