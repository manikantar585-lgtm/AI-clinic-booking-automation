"""LLM-backed field extraction behind a small interface.

The model's output is never trusted. It is parsed into a typed schema, invalid
fields are dropped one at a time (not the whole answer), and values are checked
against the real world: the doctor must exist and the date can't be in the past.
Every failure degrades to "nothing extracted", so the assistant falls back to its
rules and the patient never sees an error because an LLM was slow or wrong.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import random
import re
import time
from collections.abc import Sequence
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError, ValidatorFunctionWrapHandler, field_validator

from app.ai.nlu import Intent
from app.ai.prompts import PROMPT_VERSION, build_messages
from app.domain.models import Doctor

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


class Extraction(BaseModel):
    intent: Intent | None = None
    doctor_id: int | None = None
    date: dt.date | None = None
    time: str | None = None
    name: str | None = None
    phone: str | None = None
    reason: str | None = None

    @field_validator("*", mode="wrap")
    @classmethod
    def _drop_invalid(cls, value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        try:
            return handler(value)
        except ValidationError:
            return None

    @field_validator("time")
    @classmethod
    def _valid_time(cls, value: str | None) -> str | None:
        return value if value and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value) else None

    @field_validator("name", "reason", "phone")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return value.strip()[:200] or None if value else None

    def grounded(self, doctors: Sequence[Doctor], today: dt.date) -> Extraction:
        """Drop values that are well-formed but impossible."""
        updates: dict[str, Any] = {}
        if self.doctor_id is not None and self.doctor_id not in {d.id for d in doctors}:
            updates["doctor_id"] = None
        if self.date is not None and self.date < today:
            updates["date"] = None
        return self.model_copy(update=updates) if updates else self

    @property
    def is_empty(self) -> bool:
        return not any(self.model_dump().values())


class Extractor(Protocol):
    def extract(self, text: str, doctors: Sequence[Doctor], today: dt.date) -> Extraction: ...


class NullExtractor:
    """Used when no LLM is configured."""

    def extract(self, text: str, doctors: Sequence[Doctor], today: dt.date) -> Extraction:
        return Extraction()


def parse_model_output(content: str, doctors: Sequence[Doctor], today: dt.date) -> Extraction:
    data = json.loads(_FENCE_RE.sub("", content.strip()))
    if not isinstance(data, dict):
        raise ValueError("model did not return a JSON object")
    return Extraction.model_validate(data).grounded(doctors, today)


class OpenAICompatibleExtractor:
    """Works with any `/chat/completions` API: OpenAI, Groq, Together, Ollama, vLLM..."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 8.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            last = attempt == self.max_retries
            try:
                response = self._client.post("/chat/completions", json=payload)
                if response.status_code not in _RETRYABLE_STATUS or last:
                    return response.raise_for_status()
            except httpx.TransportError:  # includes timeouts
                if last:
                    raise
            # Short, jittered backoff: a patient is waiting on the other end.
            time.sleep(min(1.0, 0.2 * 2**attempt) * (0.5 + random.random()))
        raise AssertionError("unreachable")

    def extract(self, text: str, doctors: Sequence[Doctor], today: dt.date) -> Extraction:
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": build_messages(text, doctors, today),
        }
        started = time.perf_counter()
        meta = {"model": self.model, "prompt_version": PROMPT_VERSION}
        try:
            body = self._post(payload).json()
            result = parse_model_output(body["choices"][0]["message"]["content"], doctors, today)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
            log.warning("llm extraction failed: %s", type(e).__name__, extra=meta)
            return Extraction()
        log.info(
            "llm extraction ok",
            extra={
                **meta,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "tokens": body.get("usage", {}).get("total_tokens"),
            },
        )
        return result
