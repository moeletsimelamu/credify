"""Application settings, validated at start-up.

The application refuses to start when a required variable is missing or unsafe, so a
misconfigured deployment fails loudly instead of running with weak defaults.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK_SECRET_MARKERS = ("change-me", "changeme", "placeholder", "secret", "example")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CREDIFY_",
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: PostgresDsn
    jwt_secret: SecretStr
    jwt_issuer: str = "credify"
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_ttl_days: int = Field(default=7, ge=1, le=30)
    cors_origins: str = "http://localhost:5173"
    cookie_secure: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True
    login_max_attempts: int = Field(default=5, ge=1, le=20)
    login_lockout_minutes: int = Field(default=15, ge=1, le=1440)

    @field_validator("jwt_secret")
    @classmethod
    def _strong_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < 32:
            raise ValueError("must be at least 32 characters")
        if any(marker in raw.lower() for marker in _WEAK_SECRET_MARKERS):
            raise ValueError("looks like a placeholder; generate a random secret")
        return value

    @model_validator(mode="after")
    def _production_hardening(self) -> Settings:
        if self.environment == "production" and not self.cookie_secure:
            raise ValueError("cookie_secure must be true in production")
        return self

    @property
    def database_url_str(self) -> str:
        return str(self.database_url)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # values come from the environment
