"""Application factory. `uvicorn app.main:app` serves the default instance."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse

from app import __version__
from app.ai.assistant import ConversationState
from app.ai.llm import Extractor, NullExtractor, OpenAICompatibleExtractor
from app.api.errors import install_error_handlers
from app.api.routes import routers
from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.logging import configure_logging, request_id_var
from app.core.security import RateLimiter
from app.services.notifications import DisabledNotifier, Notifier, TwilioWhatsAppNotifier
from app.services.scheduler import reminder_loop
from app.services.sessions import SessionStore

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


def build_extractor(settings: Settings) -> Extractor:
    if not settings.llm_enabled:
        return NullExtractor()
    return OpenAICompatibleExtractor(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def build_notifier(settings: Settings) -> Notifier:
    if not settings.whatsapp_sending_enabled:
        return DisabledNotifier()
    return TwilioWhatsAppNotifier(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token.get_secret_value(),
        from_number=settings.twilio_whatsapp_from,
    )


def create_app(
    settings: Settings | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    extractor: Extractor | None = None,
    notifier: Notifier | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db: Database = app.state.db
        with db.session() as conn:
            version = db.migrate(conn)
            if settings.seed_demo_data:
                db.seed_demo_doctors(conn)
            ttl = timedelta(minutes=settings.session_ttl_minutes)
            purged = SessionStore(conn, ConversationState, ttl).purge_expired()
        log.info(
            "startup complete",
            extra={
                "schema_version": version,
                "sessions_purged": purged,
                "llm_enabled": settings.llm_enabled,
                "env": settings.env,
            },
        )
        scheduler = None
        if settings.reminder_send_hour is not None and app.state.notifier.configured:
            scheduler = asyncio.create_task(reminder_loop(app, settings.reminder_send_hour))
        yield
        if scheduler:
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler
        for client in (app.state.extractor, app.state.notifier):
            close = getattr(client, "close", None)
            if close:
                close()

    app = FastAPI(
        title=f"{settings.clinic_name} · AI Booking API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.env == "production" else "/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.db = Database(settings.db_path)
    app.state.clock = clock or (lambda: datetime.now(settings.tz).replace(tzinfo=None))
    app.state.extractor = extractor or build_extractor(settings)
    app.state.notifier = notifier or build_notifier(settings)
    app.state.rate_limiter = RateLimiter(settings.chat_rate_limit_per_minute)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            response.headers.update(_SECURITY_HEADERS)
            if request.url.path not in ("/health", "/ready"):
                log.info(
                    "%s %s %d",
                    request.method,
                    request.url.path,
                    response.status_code,
                    extra={"duration_ms": round((time.perf_counter() - started) * 1000, 1)},
                )
            return response
        finally:
            request_id_var.reset(token)

    install_error_handlers(app)
    for router in routers:
        app.include_router(router)

    @app.get("/", include_in_schema=False)
    def chat_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "admin.html")

    return app


app = create_app()
