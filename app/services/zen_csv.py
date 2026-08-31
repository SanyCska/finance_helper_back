"""Разбор CSV-выгрузки Дзен-мани.

Формат: разделитель `;`, кавычки вокруг текстовых полей, UTF-8 (обычно с BOM).
Заголовок фиксированный, см. `REQUIRED_COLUMNS`.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.models import Direction

REQUIRED_COLUMNS = (
    "date",
    "categoryName",
    "outcomeAccountName",
    "outcome",
    "outcomeCurrencyShortTitle",
    "incomeAccountName",
    "income",
    "incomeCurrencyShortTitle",
)

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1251")


class ZenCsvError(Exception):
    """Файл не похож на выгрузку Дзен-мани."""


@dataclass(frozen=True)
class ParseError:
    line_no: int
    message: str
    raw: str


@dataclass(frozen=True)
class ParsedRow:
    date: dt.date
    category_name: str
    account_name: str
    payee: str | None
    comment: str | None
    direction: Direction
    amount_original: Decimal
    currency: str
    zen_created_at: dt.datetime | None
    zen_changed_at: dt.datetime | None

    @property
    def dedup_key(self) -> str:
        """Отпечаток операции по содержанию.

        Время создания в него не входит: Дзен отдаёт `createdDate` в
        таймзоне устройства на момент выгрузки, и при смене пояса оно
        едет у всей истории разом.
        """
        parts = (
            self.date.isoformat(),
            self.category_name,
            self.account_name,
            self.payee or "",
            self.comment or "",
            self.direction.value,
            # normalize(): в базе сумма лежит как 2000.5000, и миграция
            # срезает хвостовые нули — иначе отпечатки разойдутся
            format(self.amount_original.normalize(), "f"),
            self.currency,
        )
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


@dataclass
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    skipped_transfers: int = 0
    errors: list[ParseError] = field(default_factory=list)


def _decode(content: bytes) -> str:
    for encoding in _ENCODINGS:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ZenCsvError("Не удалось определить кодировку файла")


def _parse_amount(raw: str) -> Decimal:
    text = (raw or "").strip().replace("\xa0", "").replace(" ", "")
    if not text:
        return Decimal(0)
    # Дзен экспортирует точку, но локализованные выгрузки встречаются с запятой.
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Не удалось разобрать сумму {raw!r}") from exc


def _parse_date(raw: str) -> dt.date:
    try:
        return dt.date.fromisoformat((raw or "").strip())
    except ValueError as exc:
        raise ValueError(f"Не удалось разобрать дату {raw!r}") from exc


def _parse_datetime(raw: str) -> dt.datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def parse_zen_csv(content: bytes) -> ParseResult:
    """Разобрать выгрузку. Ошибки отдельных строк не прерывают разбор."""
    text = _decode(content)
    if not text.strip():
        raise ZenCsvError("Файл пустой")

    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if reader.fieldnames is None:
        raise ZenCsvError("В файле нет заголовка")

    header = {name.strip().lstrip("﻿") for name in reader.fieldnames}
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise ZenCsvError("В выгрузке нет колонок: " + ", ".join(missing))

    result = ParseResult()
    for line_no, raw_row in enumerate(reader, start=2):
        raw_text = ";".join(str(value or "") for value in raw_row.values())
        try:
            parsed = _parse_row(raw_row)
        except ValueError as exc:
            result.errors.append(ParseError(line_no, str(exc), raw_text))
            continue

        if parsed is None:
            result.skipped_transfers += 1
            continue
        result.rows.append(parsed)

    return result


def _parse_row(row: dict[str, str]) -> ParsedRow | None:
    """Разобрать одну строку. `None` — перевод между счетами, его пропускаем."""
    outcome = _parse_amount(row.get("outcome", ""))
    income = _parse_amount(row.get("income", ""))

    if outcome > 0 and income > 0:
        return None
    if outcome == 0 and income == 0:
        raise ValueError("Обе суммы нулевые")

    if outcome > 0:
        direction = Direction.OUTCOME
        amount = outcome
        currency = (row.get("outcomeCurrencyShortTitle") or "").strip()
        account = row.get("outcomeAccountName") or ""
    else:
        direction = Direction.INCOME
        amount = income
        currency = (row.get("incomeCurrencyShortTitle") or "").strip()
        account = row.get("incomeAccountName") or ""

    if not currency:
        raise ValueError("Не указана валюта")

    return ParsedRow(
        date=_parse_date(row.get("date", "")),
        # категория сохраняется дословно, включая хвостовые пробелы и запятые
        category_name=row.get("categoryName") or "",
        account_name=account,
        payee=_clean(row.get("payee")),
        comment=_clean(row.get("comment")),
        direction=direction,
        amount_original=amount,
        currency=currency.upper(),
        zen_created_at=_parse_datetime(row.get("createdDate", "")),
        zen_changed_at=_parse_datetime(row.get("changedDate", "")),
    )
