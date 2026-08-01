"""Тесты валидации initData Telegram."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.auth import InitDataError, validate_init_data

BOT_TOKEN = "123456:TEST-TOKEN"


def make_init_data(
    telegram_id: int = 42,
    auth_date: int | None = None,
    token: str = BOT_TOKEN,
    corrupt: bool = False,
) -> str:
    payload = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAF",
        "user": json.dumps(
            {"id": telegram_id, "first_name": "Тест", "username": "tester"},
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    }
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if corrupt:
        signature = "0" * 64
    return urlencode({**payload, "hash": signature})


def test_valid_init_data_returns_user():
    parsed = validate_init_data(make_init_data(), BOT_TOKEN, max_age_s=3600)

    assert parsed["id"] == 42
    assert parsed["username"] == "tester"


def test_corrupted_hash_is_rejected():
    with pytest.raises(InitDataError):
        validate_init_data(make_init_data(corrupt=True), BOT_TOKEN, max_age_s=3600)


def test_data_signed_with_another_token_is_rejected():
    foreign = make_init_data(token="999:OTHER")

    with pytest.raises(InitDataError):
        validate_init_data(foreign, BOT_TOKEN, max_age_s=3600)


def test_expired_auth_date_is_rejected():
    old = int(time.time()) - 60 * 60 * 48

    with pytest.raises(InitDataError):
        validate_init_data(make_init_data(auth_date=old), BOT_TOKEN, max_age_s=3600)


def test_missing_hash_is_rejected():
    with pytest.raises(InitDataError):
        validate_init_data("auth_date=1&user=%7B%7D", BOT_TOKEN, max_age_s=3600)


def test_empty_init_data_is_rejected():
    with pytest.raises(InitDataError):
        validate_init_data("", BOT_TOKEN, max_age_s=3600)


def test_init_data_without_user_is_rejected():
    payload = {"auth_date": str(int(time.time()))}
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    raw = urlencode({**payload, "hash": signature})

    with pytest.raises(InitDataError):
        validate_init_data(raw, BOT_TOKEN, max_age_s=3600)


def test_future_auth_date_within_tolerance_is_accepted():
    soon = int(time.time()) + 30

    parsed = validate_init_data(make_init_data(auth_date=soon), BOT_TOKEN, max_age_s=3600)

    assert parsed["id"] == 42


def test_auth_date_is_returned_as_datetime():
    now = int(time.time())

    parsed = validate_init_data(make_init_data(auth_date=now), BOT_TOKEN, max_age_s=3600)

    assert isinstance(parsed["auth_date"], dt.datetime)
