"""Тесты бота: тексты, доступ и расписание напоминаний."""

from __future__ import annotations

import datetime as dt

from app.bot import texts
from app.bot.handlers import is_allowed
from app.bot.main import is_reminder_day
from app.services.importer import ImportReport


def test_report_mentions_new_and_duplicate_counts():
    message = texts.format_import_report(ImportReport(rows_total=10, rows_new=7, rows_duplicate=3))

    assert "Новых операций: 7" in message
    assert "дубля: 3" in message


def test_report_uses_correct_plural_for_one_duplicate():
    message = texts.format_import_report(ImportReport(rows_new=1, rows_duplicate=1))

    assert "дубль: 1" in message


def test_report_uses_correct_plural_for_many_duplicates():
    message = texts.format_import_report(ImportReport(rows_new=0, rows_duplicate=11))

    assert "дублей: 11" in message


def test_report_lists_parse_errors():
    report = ImportReport(rows_new=1, rows_error=2, errors=["строка 5: битая дата", "строка 9: нули"])

    message = texts.format_import_report(report)

    assert "Не разобрал 2 строки" in message
    assert "строка 5: битая дата" in message


def test_report_mentions_pending_rates():
    message = texts.format_import_report(ImportReport(rows_new=5, pending_fx=5))

    assert "не нашлись курсы валют" in message


def test_report_without_problems_stays_short():
    message = texts.format_import_report(ImportReport(rows_total=3, rows_new=3))

    assert message.count("\n") == 0


def test_owner_is_allowed_and_stranger_is_not():
    assert is_allowed(42) is True
    assert is_allowed(999) is False


def test_reminder_fires_one_day_before_month_end():
    assert is_reminder_day(dt.date(2026, 7, 30)) is True
    assert is_reminder_day(dt.date(2026, 7, 31)) is False
    assert is_reminder_day(dt.date(2026, 7, 15)) is False
    # февраль високосного 2028 года заканчивается 29-го
    assert is_reminder_day(dt.date(2028, 2, 28)) is True
