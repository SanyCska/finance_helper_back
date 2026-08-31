"""Тесты парсера выгрузки Дзен-мани."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.models import Direction
from app.services.zen_csv import ZenCsvError, parse_zen_csv

HEADER = (
    "date;categoryName;payee;comment;outcomeAccountName;outcome;outcomeCurrencyShortTitle;"
    "incomeAccountName;income;incomeCurrencyShortTitle;createdDate;changedDate;qrCode"
)


def build(*rows: str, bom: bool = True) -> bytes:
    text = "\n".join([HEADER, *rows]) + "\n"
    return ("﻿" + text if bom else text).encode("utf-8")


OUTCOME = (
    '2026-08-01;"Доставки еды";;;"Сербия ";"2500";RSD;"Сербия ";"0";RSD;'
    '"2026-08-01 14:41:02";"2026-08-01 14:41:03";'
)
INCOME = (
    '2026-07-03;"продажа";;;"Сербия ";"0";RSD;"Сербия ";"1500";RSD;'
    '"2026-07-03 10:00:00";"2026-07-03 10:00:01";'
)


def test_parses_outcome_row_with_bom():
    result = parse_zen_csv(build(OUTCOME))

    assert result.errors == []
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.date == dt.date(2026, 8, 1)
    assert row.category_name == "Доставки еды"
    assert row.account_name == "Сербия "
    assert row.direction == Direction.OUTCOME
    assert row.amount_original == Decimal("2500")
    assert row.currency == "RSD"
    assert row.zen_created_at == dt.datetime(2026, 8, 1, 14, 41, 2)


def test_parses_income_row_from_income_columns():
    result = parse_zen_csv(build(INCOME))

    row = result.rows[0]
    assert row.direction == Direction.INCOME
    assert row.amount_original == Decimal("1500")
    assert row.currency == "RSD"


def test_skips_transfer_between_accounts():
    transfer = (
        '2026-07-05;"";;;"Сербия ";"100";RSD;"Евро";"1";EUR;'
        '"2026-07-05 10:00:00";"2026-07-05 10:00:01";'
    )
    result = parse_zen_csv(build(transfer))

    assert result.rows == []
    assert result.skipped_transfers == 1
    assert result.errors == []


def test_zero_amount_row_is_an_error():
    zero = (
        '2026-07-05;"Разное";;;"Сербия ";"0";RSD;"Сербия ";"0";RSD;'
        '"2026-07-05 10:00:00";"2026-07-05 10:00:01";'
    )
    result = parse_zen_csv(build(zero))

    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0].line_no == 2


def test_keeps_category_verbatim_including_trailing_space():
    row = (
        '2026-07-05;"Продукты в магазинах ";;;"Сербия ";"1670";RSD;"Сербия ";"0";RSD;'
        '"2026-07-05 10:00:00";"2026-07-05 10:00:01";'
    )
    result = parse_zen_csv(build(row))

    assert result.rows[0].category_name == "Продукты в магазинах "


def test_empty_category_stays_empty_string():
    row = (
        '2026-07-05;;;;"Сербия ";"1670";RSD;"Сербия ";"0";RSD;'
        '"2026-07-05 10:00:00";"2026-07-05 10:00:01";'
    )
    result = parse_zen_csv(build(row))

    assert result.rows[0].category_name == ""


def test_parses_fractional_amount_without_float_loss():
    row = (
        '2026-07-05;"Кофе";;;"Евро";"1234.56";EUR;"Евро";"0";EUR;'
        '"2026-07-05 10:00:00";"2026-07-05 10:00:01";'
    )
    result = parse_zen_csv(build(row))

    assert result.rows[0].amount_original == Decimal("1234.56")


def test_amount_with_comma_decimal_separator():
    row = (
        '2026-07-05;"Кофе";;;"Евро";"12,50";EUR;"Евро";"0";EUR;'
        '"2026-07-05 10:00:00";"2026-07-05 10:00:01";'
    )
    result = parse_zen_csv(build(row))

    assert result.rows[0].amount_original == Decimal("12.50")


def test_broken_date_reports_line_number_and_keeps_other_rows():
    broken = (
        'не-дата;"Кофе";;;"Евро";"10";EUR;"Евро";"0";EUR;'
        '"2026-07-05 10:00:00";"2026-07-05 10:00:01";'
    )
    result = parse_zen_csv(build(broken, OUTCOME))

    assert len(result.rows) == 1
    assert len(result.errors) == 1
    assert result.errors[0].line_no == 2


def test_payee_and_comment_are_none_when_blank():
    result = parse_zen_csv(build(OUTCOME))

    assert result.rows[0].payee is None
    assert result.rows[0].comment is None


def test_missing_required_column_raises():
    content = b"date;categoryName\n2026-08-01;Kofe\n"

    with pytest.raises(ZenCsvError):
        parse_zen_csv(content)


def test_empty_file_raises():
    with pytest.raises(ZenCsvError):
        parse_zen_csv(b"")


def test_windows_1251_content_is_decoded():
    text = "\n".join([HEADER, OUTCOME]) + "\n"
    result = parse_zen_csv(text.encode("cp1251"))

    assert result.rows[0].category_name == "Доставки еды"


SHIFTED = (
    '2026-08-01;"Доставки еды";;;"Сербия ";"2500";RSD;"Сербия ";"0";RSD;'
    '"2026-08-01 15:41:02";"2026-08-01 15:41:03";'
)
OTHER_AMOUNT = (
    '2026-08-01;"Доставки еды";;;"Сербия ";"2501";RSD;"Сербия ";"0";RSD;'
    '"2026-08-01 14:41:02";"2026-08-01 14:41:03";'
)


def test_dedup_key_ignores_created_date():
    """Ключ не зависит от времени создания: оно едет вместе с таймзоной."""
    early = parse_zen_csv(build(OUTCOME))
    late = parse_zen_csv(build(SHIFTED))

    assert early.rows[0].dedup_key == late.rows[0].dedup_key


def test_dedup_key_differs_on_amount():
    a = parse_zen_csv(build(OUTCOME))
    b = parse_zen_csv(build(OTHER_AMOUNT))

    assert a.rows[0].dedup_key != b.rows[0].dedup_key
