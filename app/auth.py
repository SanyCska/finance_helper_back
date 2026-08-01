"""Авторизация Mini App по подписи Telegram `initData`.

Алгоритм из документации Telegram: строка проверки — все поля кроме `hash`,
отсортированные по ключу и склеенные через перевод строки; секрет —
`HMAC_SHA256("WebAppData", bot_token)`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import User

#: допуск на расхождение часов клиента и сервера
CLOCK_SKEW_S = 300


class InitDataError(Exception):
    """initData не прошла проверку."""


def validate_init_data(init_data: str, bot_token: str, max_age_s: int) -> dict:
    if not init_data:
        raise InitDataError("Пустая initData")
    if not bot_token:
        raise InitDataError("Не настроен токен бота")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("В initData нет поля hash")

    check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("Подпись initData не совпадает")

    raw_auth_date = pairs.get("auth_date")
    if not raw_auth_date or not raw_auth_date.isdigit():
        raise InitDataError("Некорректное поле auth_date")
    auth_date = int(raw_auth_date)
    age = time.time() - auth_date
    if age > max_age_s:
        raise InitDataError("initData устарела")
    if age < -CLOCK_SKEW_S:
        raise InitDataError("auth_date из будущего")

    raw_user = pairs.get("user")
    if not raw_user:
        raise InitDataError("В initData нет данных пользователя")
    try:
        user = json.loads(raw_user)
    except json.JSONDecodeError as exc:
        raise InitDataError("Не удалось разобрать данные пользователя") from exc
    if not isinstance(user, dict) or "id" not in user:
        raise InitDataError("В данных пользователя нет id")

    return {
        "id": int(user["id"]),
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "auth_date": dt.datetime.fromtimestamp(auth_date, dt.UTC),
    }


def _get_or_create_user(db: Session, telegram_id: int, username: str | None) -> User:
    user = db.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            base_currency=get_settings().base_currency,
        )
        db.add(user)
        db.commit()
    elif username and user.username != username:
        user.username = username
        db.commit()
    return user


def _check_allowed(telegram_id: int, settings: Settings) -> None:
    if settings.allowed_telegram_ids and telegram_id not in settings.allowed_telegram_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступ запрещён")


def current_user(
    authorization: str | None = Header(default=None),
    x_internal_token: str | None = Header(default=None),
    x_telegram_id: int | None = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """Пользователь запроса: initData, внутренний токен бота или обход в разработке."""
    # бот ходит в API от имени пользователя по общему секрету
    if x_internal_token and hmac.compare_digest(x_internal_token, settings.internal_token):
        if x_telegram_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Не передан X-Telegram-Id")
        _check_allowed(x_telegram_id, settings)
        return _get_or_create_user(db, x_telegram_id, None)

    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "tma" or not value:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ожидается схема tma")
        try:
            parsed = validate_init_data(value, settings.bot_token, settings.init_data_max_age_s)
        except InitDataError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        _check_allowed(parsed["id"], settings)
        return _get_or_create_user(db, parsed["id"], parsed["username"])

    if settings.bypass_auth_enabled:
        return _get_or_create_user(db, settings.dev_telegram_id, "dev")

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужна авторизация Telegram")
