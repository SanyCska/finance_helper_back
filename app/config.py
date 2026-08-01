"""Конфигурация приложения."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"

    database_url: str = "postgresql+psycopg://finance:finance@localhost:5432/finance"

    bot_token: str = ""
    webapp_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"

    base_currency: str = "USD"
    excluded_categories: Annotated[list[str], NoDecode] = ["Correction"]
    allowed_telegram_ids: Annotated[list[int], NoDecode] = []

    internal_token: str = "dev-internal-token"
    dev_bypass_auth: bool = False
    dev_telegram_id: int = 1

    init_data_max_age_s: int = 24 * 60 * 60

    fx_api_base: str = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api"
    fx_lookback_days: int = 7
    fx_http_timeout_s: float = 10.0
    fx_concurrency: int = 8

    reminder_hour: int = 11

    @field_validator("excluded_categories", mode="before")
    @classmethod
    def _parse_excluded(cls, value: object) -> object:
        if isinstance(value, str):
            return _split_csv(value)
        return value

    @field_validator("allowed_telegram_ids", mode="before")
    @classmethod
    def _parse_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(item) for item in _split_csv(value)]
        return value

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"prod", "production"}

    @property
    def bypass_auth_enabled(self) -> bool:
        """Обход авторизации доступен только вне продакшена."""
        return self.dev_bypass_auth and not self.is_production

    def is_excluded_category(self, name: str) -> bool:
        normalized = name.strip().casefold()
        return any(
            normalized == excluded.strip().casefold() for excluded in self.excluded_categories
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
