"""Twilio WhatsApp webhook (sandbox or WhatsApp Business through Twilio)."""

import logging
from typing import Annotated
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.api.deps import AssistantDep, SettingsDep
from app.core.security import verify_twilio_signature
from app.domain.models import Channel

log = logging.getLogger(__name__)
router = APIRouter(tags=["whatsapp"])


async def twilio_form(request: Request, settings: SettingsDep) -> dict[str, str]:
    """Read the form body and, when TWILIO_AUTH_TOKEN is set, reject requests not signed by Twilio."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    token = settings.twilio_auth_token.get_secret_value()
    if token:
        base = settings.public_base_url.rstrip("/")
        url = f"{base}{request.url.path}" if base else str(request.url)
        if not verify_twilio_signature(token, url, form, request.headers.get("x-twilio-signature", "")):
            log.warning("rejected unsigned whatsapp webhook")
            raise HTTPException(403, "Invalid Twilio signature")
    if not form.get("From"):
        raise HTTPException(400, "Missing From")
    return form


@router.post("/webhook/whatsapp", response_class=Response)
def whatsapp(
    form: Annotated[dict[str, str], Depends(twilio_form)], assistant: AssistantDep, settings: SettingsDep
) -> Response:
    sender = form["From"]  # "whatsapp:+919876543210"
    # Only trust the number as verified when Twilio signed the request.
    verified_phone = sender.removeprefix("whatsapp:") if settings.twilio_auth_token.get_secret_value() else None
    reply = assistant.reply(
        "wa:" + sender, form.get("Body") or "hi", source=Channel.WHATSAPP, sender_phone=verified_phone
    )
    text = reply.text + ("\n\n" + " | ".join(reply.options) if reply.options else "")
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(text)}</Message></Response>'
    return Response(twiml, media_type="application/xml")
