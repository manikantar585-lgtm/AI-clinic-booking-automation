"""FastAPI dependencies: the wiring between HTTP requests and services."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.ai.assistant import Assistant, ConversationState
from app.core.config import Settings
from app.core.security import tokens_match
from app.services.booking import BookingService
from app.services.sessions import SessionStore


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    with request.app.state.db.session() as conn:
        yield conn


SettingsDep = Annotated[Settings, Depends(get_settings)]
ConnDep = Annotated[sqlite3.Connection, Depends(get_conn)]


def get_booking(request: Request, conn: ConnDep, settings: SettingsDep) -> BookingService:
    return BookingService(
        conn,
        request.app.state.clock,
        clinic_name=settings.clinic_name,
        lead_minutes=settings.booking_lead_minutes,
        horizon_days=settings.booking_horizon_days,
    )


BookingDep = Annotated[BookingService, Depends(get_booking)]


def get_assistant(request: Request, conn: ConnDep, booking: BookingDep, settings: SettingsDep) -> Assistant:
    sessions = SessionStore(conn, ConversationState, timedelta(minutes=settings.session_ttl_minutes))
    return Assistant(booking, sessions, request.app.state.extractor, settings.clinic_name, settings.clinic_phone)


AssistantDep = Annotated[Assistant, Depends(get_assistant)]


def require_admin(settings: SettingsDep, x_admin_token: Annotated[str, Header()] = "") -> None:
    if not tokens_match(x_admin_token, settings.admin_token.get_secret_value()):
        raise HTTPException(401, "Invalid admin token", headers={"WWW-Authenticate": "X-Admin-Token"})


def client_key(request: Request) -> str:
    # Behind one reverse proxy (Render, Railway, nginx) the last X-Forwarded-For entry is
    # the address the proxy saw. Earlier entries are client-supplied and can be spoofed.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[-1].strip() or (request.client.host if request.client else "unknown")


def rate_limited(request: Request) -> None:
    if not request.app.state.rate_limiter.allow(client_key(request)):
        raise HTTPException(
            429, "Too many messages. Please wait a minute and try again.", headers={"Retry-After": "60"}
        )
