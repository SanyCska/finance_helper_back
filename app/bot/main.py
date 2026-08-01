"""Запуск бота: long polling плюс напоминание в конце месяца."""

from __future__ import annotations

import asyncio
import calendar
import datetime as dt
import logging

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.bot import texts
from app.bot.handlers import router, webapp_keyboard
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def is_reminder_day(today: dt.date) -> bool:
    """Напоминаем за день до конца месяца."""
    last_day = calendar.monthrange(today.year, today.month)[1]
    return today.day == last_day - 1


async def send_reminder(bot: Bot) -> None:
    if not is_reminder_day(dt.date.today()):
        return
    for telegram_id in get_settings().allowed_telegram_ids:
        try:
            await bot.send_message(telegram_id, texts.REMINDER, reply_markup=webapp_keyboard())
        except Exception:
            logger.exception("Не удалось отправить напоминание %s", telegram_id)


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
    scheduler.start()

    logger.info("Бот запущен")
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
