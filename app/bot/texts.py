"""Тексты бота. Вынесены отдельно, чтобы покрыть форматирование тестами."""

from __future__ import annotations

from decimal import Decimal

from app.services.importer import ImportReport

MONTHS_NOMINATIVE = [
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]

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


def month_title(month: str) -> str:
    """`'2026-07'` → `'Июль 2026'`."""
    year, _, index = month.partition("-")
    try:
        return f"{MONTHS_NOMINATIVE[int(index) - 1]} {year}"
    except (ValueError, IndexError):
        return month


def _money(value: Decimal) -> str:
    """Округлённая до доллара сумма с разделителем разрядов и знаком минуса."""
    rounded = int(round(float(value)))
    body = f"{abs(rounded):,}".replace(",", " ")
    return f"−${body}" if rounded < 0 else f"${body}"


def _amount(value: Decimal, currency: str) -> str:
    """Сумма в своей валюте: «650 EUR», «15.99 USD»."""
    quantized = Decimal(value).normalize()
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    whole, _, fraction = text.partition(".")
    grouped = f"{int(whole):,}".replace(",", " ")
    return f"{grouped}{'.' + fraction if fraction else ''} {currency}"


def format_funds_reminder(
    month: str,
    sources: list[tuple[str, Decimal, str, str | None]],
    charges: list[tuple[str, Decimal, str]],
) -> str:
    """Напоминание свести средства в последний день месяца.

    `sources` — название, последняя сумма, валюта и дата обновления;
    `charges` — что списывают в этом месяце: их легко забыть, когда
    переписываешь остатки со счетов.
    """
    lines = [
        f"{month_title(month)} закрывается сегодня. "
        "Обнови суммы по счетам во вкладке «Средства».",
    ]

    if sources:
        lines.append("")
        lines.append("Сейчас записано:")
        for title, amount, currency, updated in sources:
            # без даты обновления суммы нет вовсе — показывать ноль было бы враньём
            body = f"{_amount(amount, currency)} · {updated}" if updated else "сумма не вводилась"
            lines.append(f"· {title} — {body}")

    if charges:
        lines.append("")
        lines.append("Не забудь про списания этого месяца:")
        for title, amount, currency in charges:
            lines.append(f"· {title} — {_amount(amount, currency)}")

    lines.append("")
    lines.append("Первого числа посчитаю итог месяца и покажу расхождение с учётом.")
    return "\n".join(lines)


def format_month_result(
    month: str,
    income: Decimal,
    outcome: Decimal,
    saldo: Decimal,
    needs_check: bool,
) -> str:
    """Итог закрывшегося месяца и просьба сверить остатки."""
    lines = [
        f"{month_title(month)} закрыт.",
        "",
        f"Доход: {_money(income)}",
        f"Траты: {_money(outcome)}",
        f"Сальдо: {_money(saldo)}",
    ]
    if needs_check:
        lines += [
            "",
            "Загляни во вкладку «Средства» и обнови суммы по счетам — "
            "покажу реальное сальдо и расхождение с учётом.",
        ]
    return "\n".join(lines)
