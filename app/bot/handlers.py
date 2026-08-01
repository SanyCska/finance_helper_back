"""Обработчики бота."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from app.bot import texts
from app.config import get_settings
from app.db import SessionLocal
from app.models import User
from app.services.importer import ImportReport, import_csv
from app.services.zen_csv import ZenCsvError

logger = logging.getLogger(__name__)
router = Router()


def webapp_keyboard() -> InlineKeyboardMarkup | None:
    url = get_settings().webapp_url
    # Telegram принимает кнопку Mini App только для https
    if not url.startswith("https://"):
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть аппу", web_app=WebAppInfo(url=url))]
        ]
    )


def is_allowed(telegram_id: int) -> bool:
    allowed = get_settings().allowed_telegram_ids
    return not allowed or telegram_id in allowed


def get_or_create_user(db, telegram_id: int, username: str | None) -> User:
    user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            base_currency=get_settings().base_currency,
        )
        db.add(user)
        db.commit()
    return user


def run_import(telegram_id: int, username: str | None, filename: str, content: bytes) -> ImportReport:
    """Синхронный импорт в отдельной сессии — вызывается из обработчика документа."""
    with SessionLocal() as db:
        user = get_or_create_user(db, telegram_id, username)
        return import_csv(db, user, filename, content)


@router.message(Command("start"))
async def handle_start(message: Message) -> None:
    if not is_allowed(message.from_user.id):
        await message.answer(texts.FOREIGN_USER)
        return
    await message.answer(texts.GREETING, reply_markup=webapp_keyboard())


@router.message(F.document)
async def handle_document(message: Message) -> None:
    if not is_allowed(message.from_user.id):
        await message.answer(texts.FOREIGN_USER)
        return

    document = message.document
    filename = document.file_name or "import.csv"
    if not filename.lower().endswith(".csv"):
        await message.answer(texts.NOT_A_CSV)
        return

    buffer = await message.bot.download(document)
    content = buffer.read()

    try:
        report = run_import(message.from_user.id, message.from_user.username, filename, content)
    except ZenCsvError as exc:
        await message.answer(texts.format_error(str(exc)))
        return
    except Exception:  # импорт не должен ронять бота
        logger.exception("Импорт упал")
        await message.answer(texts.format_error("внутренняя ошибка, смотри логи"))
        return

    await message.answer(texts.format_import_report(report), reply_markup=webapp_keyboard())


@router.message(F.text)
async def handle_other(message: Message) -> None:
    if not is_allowed(message.from_user.id):
        await message.answer(texts.FOREIGN_USER)
        return
    await message.answer(texts.GREETING, reply_markup=webapp_keyboard())
