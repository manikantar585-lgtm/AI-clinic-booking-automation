"""Shared fixtures. Time is frozen so tests never depend on the real clock or weekday."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.assistant import Assistant, ConversationState
from app.ai.llm import Extractor, NullExtractor
from app.core.config import Settings
from app.core.database import Database
from app.main import create_app
from app.services.booking import BookingService
from app.services.sessions import SessionStore
from tests.support import FrozenClock


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


@pytest.fixture
def database(db_path: str) -> Database:
    db = Database(db_path)
    with db.session() as conn:
        db.migrate(conn)
        db.seed_demo_doctors(conn)
    return db


@pytest.fixture
def conn(database: Database) -> Iterator[sqlite3.Connection]:
    with database.session() as c:
        yield c


@pytest.fixture
def booking(conn: sqlite3.Connection, clock: FrozenClock) -> BookingService:
    return BookingService(conn, clock, clinic_name="Test Clinic")


@pytest.fixture
def make_assistant(conn: sqlite3.Connection, booking: BookingService):
    def factory(extractor: Extractor | None = None) -> Assistant:
        sessions = SessionStore(conn, ConversationState, timedelta(minutes=30))
        return Assistant(booking, sessions, extractor or NullExtractor(), "Test Clinic")

    return factory


@pytest.fixture
def assistant(make_assistant) -> Assistant:
    return make_assistant()


@pytest.fixture
def settings(db_path: str) -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        db_path=db_path,
        admin_token="test-admin-token",
        llm_api_key="",
        twilio_auth_token="",
    )


@pytest.fixture
def make_client(clock: FrozenClock):
    clients: list[TestClient] = []

    def factory(settings: Settings, extractor: Extractor | None = None) -> TestClient:
        client = TestClient(create_app(settings, clock=clock, extractor=extractor))
        client.__enter__()  # run lifespan: migrations + seed
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client, settings: Settings) -> TestClient:
    return make_client(settings)
