"""Typed application settings, loaded from environment variables and `.env`.

Every setting maps to an upper-case env var of the same name (see `.env.example`).
"""

from __future__ import annotations

from functools import cached_property, lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
_INSECURE_TOKENS = {"", "change-me", "admin", "secret"}


class Settings(BaseSettings):
    # env_ignore_empty: "KEY=" in .env means "use the default", not "parse an empty string"
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore", frozen=True, env_ignore_empty=True)

    env: Literal["development", "test", "production"] = "development"

    # Clinic
    clinic_name: str = "Sunrise Multi-Speciality Clinic"
    clinic_phone: str = "+91 90000 00000"
    clinic_timezone: str = "Asia/Kolkata"
    booking_lead_minutes: int = Field(15, ge=0, description="Earliest bookable slot from now.")
    booking_horizon_days: int = Field(30, ge=1, le=365)

    # Storage
    db_path: str = str(BASE_DIR / "clinic.db")
    database_url: SecretStr = Field(
        SecretStr(""), description="Postgres URL (e.g. Neon/Supabase). When set, it is used instead of DB_PATH."
    )
    seed_demo_data: bool = True

    # Security
    admin_token: SecretStr = SecretStr("change-me")
    twilio_auth_token: SecretStr = SecretStr("")
    public_base_url: str = Field("", description="External URL, used to verify Twilio signatures behind a proxy.")
    chat_rate_limit_per_minute: int = Field(30, ge=1)

    # WhatsApp sending (Twilio). Receiving works without these; sending reminders needs all three.
    twilio_account_sid: str = ""
    twilio_whatsapp_from: str = Field("", description="Twilio WhatsApp sender, e.g. +14155238886 for the sandbox.")
    reminder_send_hour: int | None = Field(
        None, ge=0, le=23, description="Send tomorrow's reminders daily at this hour."
    )

    # Conversation
    session_ttl_minutes: int = Field(30, ge=1)

    # Observability
    log_level: str = "INFO"
    log_json: bool = False

    # LLM (any OpenAI-compatible API). Disabled when no key is set.
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "llama-3.1-8b-instant"
    llm_timeout_seconds: float = Field(8.0, gt=0)
    llm_max_retries: int = Field(2, ge=0, le=5)

    @model_validator(mode="after")
    def _forbid_insecure_production(self) -> Settings:
        if self.env == "production" and self.admin_token.get_secret_value() in _INSECURE_TOKENS:
            raise ValueError("ADMIN_TOKEN must be set to a strong secret when ENV=production")
        return self

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.clinic_timezone)

    @property
    def whatsapp_sending_enabled(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token.get_secret_value() and self.twilio_whatsapp_from)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
