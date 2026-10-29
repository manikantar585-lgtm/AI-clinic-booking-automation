"""Outbound patient messages (WhatsApp via Twilio), behind a small interface so tests and
unconfigured installs never send anything real."""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from app.core.errors import NotConfiguredError

log = logging.getLogger(__name__)

NOT_CONFIGURED = "WhatsApp sending isn't set up. Add TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM."


class DeliveryError(Exception):
    """The provider rejected or failed to accept a message."""


class Notifier(Protocol):
    @property
    def configured(self) -> bool: ...

    def send(self, to_phone: str, body: str) -> str:
        """Send `body` to an E.164 number and return the provider's message id."""
        ...


class DisabledNotifier:
    configured = False

    def send(self, to_phone: str, body: str) -> str:
        raise NotConfiguredError(NOT_CONFIGURED)


class TwilioWhatsAppNotifier:
    """Twilio Programmable Messaging: POST /2010-04-01/Accounts/{sid}/Messages.json."""

    configured = True

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.from_number = from_number.removeprefix("whatsapp:")
        self._client = httpx.Client(
            base_url=f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}",
            auth=(account_sid, auth_token),
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def send(self, to_phone: str, body: str) -> str:
        try:
            response = self._client.post(
                "/Messages.json",
                data={"To": f"whatsapp:{to_phone}", "From": f"whatsapp:{self.from_number}", "Body": body},
            )
        except httpx.HTTPError as e:
            raise DeliveryError(f"could not reach Twilio: {type(e).__name__}") from e
        if response.status_code >= 400:
            try:
                detail = response.json().get("message", response.text)
            except ValueError:
                detail = response.text
            raise DeliveryError(f"Twilio {response.status_code}: {detail}")
        sid: str = response.json().get("sid", "")
        log.info("whatsapp message accepted", extra={"provider_sid": sid})
        return sid
