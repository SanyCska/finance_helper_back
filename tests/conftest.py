"""Общие фикстуры тестов: изолированная SQLite-база на каждый тест."""

from __future__ import annotations

import os

# тесты не должны зависеть от локального .env разработчика
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN"
os.environ["ALLOWED_TELEGRAM_IDS"] = "42"
os.environ["EXCLUDED_CATEGORIES"] = "Correction"
os.environ["BASE_CURRENCY"] = "USD"
os.environ["DEV_BYPASS_AUTH"] = "false"
os.environ["ENV"] = "test"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import User


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def user(db: Session) -> User:
    u = User(telegram_id=42, base_currency="USD")
    db.add(u)
    db.commit()
    return u
