"""Тексты бота. Вынесены отдельно, чтобы покрыть форматирование тестами."""

from __future__ import annotations

from app.services.importer import ImportReport

GREETING = (
    "Привет. Пришли сюда CSV-выгрузку из Дзен-мани — разберу и запишу в базу.\n\n"
    "Открыть аппу можно кнопкой ниже."
)

FOREIGN_USER = "Это личный бот, доступ только у владельца."

NOT_A_CSV = "Жду файл .csv из Дзен-мани. Этот формат я не понимаю."

REMINDER = (
    "Месяц заканчивается. Выгрузи CSV из Дзен-мани и пришли сюда — "
    "посчитаю сальдо и помогу расписать план на следующий месяц."
)


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def format_import_report(report: ImportReport) -> str:
    rows = [
        f"Готово. Новых операций: {report.rows_new}, "
        f"{_plural(report.rows_duplicate, 'дубль', 'дубля', 'дублей')}: {report.rows_duplicate}."
    ]

    if report.rows_error:
        rows.append(
            f"Не разобрал {report.rows_error} "
            f"{_plural(report.rows_error, 'строку', 'строки', 'строк')}:"
        )
        rows.extend(f"· {error}" for error in report.errors[:5])

    if report.skipped_transfers:
        rows.append(f"Переводов между счетами пропущено: {report.skipped_transfers}.")

    if report.pending_fx:
        rows.append(
            f"Для {report.pending_fx} "
            f"{_plural(report.pending_fx, 'операции', 'операций', 'операций')} "
            "не нашлись курсы валют — их можно догрузить в аппе."
        )

    return "\n".join(rows)


def format_error(message: str) -> str:
    return f"Не получилось: {message}"
