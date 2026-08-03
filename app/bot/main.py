"""Запуск бота: long polling плюс напоминание в конце месяца."""

from __future__ import annotations

import asyncio
import calendar
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.bot import texts
from app.bot.handlers import router, webapp_keyboard
from app.config import get_settings
from app.db import SessionLocal
from app.models import User
from app.services import budget, funds, recurring, stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def is_reminder_day(today: dt.date) -> bool:
    """Напоминаем за день до конца месяца."""
    last_day = calendar.monthrange(today.year, today.month)[1]
    return today.day == last_day - 1


def is_month_result_day(today: dt.date) -> bool:
    """Итог месяца и просьбу сверить шлём первого числа следующего."""
    return today.day == 1


def is_last_day(today: dt.date) -> bool:
    """Свести средства просим в последний день месяца."""
    return today.day == calendar.monthrange(today.year, today.month)[1]


async def send_reminder(bot: Bot) -> None:
    if not is_reminder_day(dt.date.today()):
        return
    for telegram_id in get_settings().allowed_telegram_ids:
        try:
            await bot.send_message(telegram_id, texts.REMINDER, reply_markup=webapp_keyboard())
        except Exception:
            logger.exception("Не удалось отправить напоминание %s", telegram_id)


def month_result_text(telegram_id: int, today: dt.date | None = None) -> str | None:
    """Итог прошедшего месяца и просьба сверить остатки. `None` — писать нечего."""
    today = today or dt.date.today()
    month = stats.shift_month(today.replace(day=1), -1)

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            return None

        # подписки прошлого месяца могли остаться неначисленными
        recurring.run(db, user, today)

        transactions = stats.fetch_month(db, user.id, month)
        income, _, _ = budget.get_income(db, user, month)
        summary = stats.month_summary(transactions, income)
        if summary.tx_count == 0 and summary.income_total == 0:
            return None

        needs_check = funds.pending_check_month(db, user, today) is not None
        return texts.format_month_result(
            month=stats.format_month(month),
            income=summary.income_total,
            outcome=summary.outcome_total,
            saldo=summary.saldo,
            needs_check=needs_check,
        )


def funds_reminder_text(telegram_id: int, today: dt.date | None = None) -> str | None:
    """Напоминание свести средства. `None` — источников нет, напоминать нечего."""
    today = today or dt.date.today()
    month = today.replace(day=1)

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            return None

        states = funds.source_states(db, user)
        if not states:
            return None

        rows = [
            (
                state.source.title,
                state.amount_original,
                state.source.currency,
                state.updated_on.strftime("%d.%m") if state.updated_on else None,
            )
            for state in states
        ]
        charges = [
            (item.title, item.amount, item.currency)
            for item in recurring.list_items(db, user, only_active=True)
            if recurring.charges_in_month(item, month)
        ]
        return texts.format_funds_reminder(stats.format_month(month), rows, charges)


async def send_funds_reminder(bot: Bot) -> None:
    today = dt.date.today()
    if not is_last_day(today):
        return
    for telegram_id in get_settings().allowed_telegram_ids:
        try:
            text = funds_reminder_text(telegram_id, today)
            if text is None:
                continue
            await bot.send_message(telegram_id, text, reply_markup=webapp_keyboard())
        except Exception:
            logger.exception("Не удалось отправить напоминание о средствах %s", telegram_id)


async def send_month_result(bot: Bot) -> None:
    today = dt.date.today()
    if not is_month_result_day(today):
        return
    for telegram_id in get_settings().allowed_telegram_ids:
        try:
            text = month_result_text(telegram_id, today)
            if text is None:
                continue
            await bot.send_message(telegram_id, text, reply_markup=webapp_keyboard())
        except Exception:
            logger.exception("Не удалось отправить итог месяца %s", telegram_id)


async def main() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise SystemExit("Не задан BOT_TOKEN — положи его в .env")

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    # расписание идёт по местному времени владельца: напоминание в четыре часа
    # дня должно приходить в четыре часа дня и зимой, и летом
    tz = ZoneInfo(settings.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        send_reminder,
        CronTrigger(hour=settings.reminder_hour, minute=0, timezone=tz),
        args=[bot],
        id="month-end-reminder",
    )
    scheduler.add_job(
        send_month_result,
        CronTrigger(hour=settings.reminder_hour, minute=10, timezone=tz),
        args=[bot],
        id="month-result",
    )
    scheduler.add_job(
        send_funds_reminder,
        CronTrigger(hour=settings.funds_reminder_hour, minute=0, timezone=tz),
        args=[bot],
        id="funds-reminder",
    )
    scheduler.start()

    logger.info("Бот запущен")
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
