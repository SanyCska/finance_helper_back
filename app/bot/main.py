"""Запуск бота: long polling плюс напоминание в конце месяца."""

from __future__ import annotations

import asyncio
import calendar
import datetime as dt
import logging

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

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        send_reminder,
        CronTrigger(hour=settings.reminder_hour, minute=0),
        args=[bot],
        id="month-end-reminder",
    )
    scheduler.add_job(
        send_month_result,
        CronTrigger(hour=settings.reminder_hour, minute=10),
        args=[bot],
        id="month-result",
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
