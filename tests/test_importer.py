"""Тесты импорта выгрузки."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Direction, FxStatus, ImportBatch, Transaction, TxSource
from app.services.importer import import_csv
from tests.test_fx import FakeProvider

HEADER = (
    "date;categoryName;payee;comment;outcomeAccountName;outcome;outcomeCurrencyShortTitle;"
    "incomeAccountName;income;incomeCurrencyShortTitle;createdDate;changedDate;qrCode"
)


def row(date: str, category: str, amount: str, created: str, currency: str = "RSD") -> str:
    return (
        f'{date};"{category}";;;"Сербия ";"{amount}";{currency};"Сербия ";"0";{currency};'
        f'"{created}";"{created}";'
    )


def build(*rows: str) -> bytes:
    return ("﻿" + "\n".join([HEADER, *rows]) + "\n").encode("utf-8")


ROWS = (
    row("2026-07-01", "Кофе", "300", "2026-07-01 09:00:00"),
    row("2026-07-02", "Рестики", "2500", "2026-07-02 20:00:00"),
    row("2026-07-03", "Транспорт", "150", "2026-07-03 08:00:00"),
)


def provider_with_rates() -> FakeProvider:
    days = {dt.date(2026, 6, 20) + dt.timedelta(days=n) for n in range(40)}
    return FakeProvider({"RSD": {day: "0.01" for day in days}})


def test_imports_all_rows(db: Session, user):
    report = import_csv(db, user, "zen.csv", build(*ROWS), provider=provider_with_rates())

    assert report.rows_total == 3
    assert report.rows_new == 3
    assert report.rows_duplicate == 0
    assert db.query(Transaction).count() == 3


def test_second_import_of_same_file_adds_nothing(db: Session, user):
    import_csv(db, user, "zen.csv", build(*ROWS), provider=provider_with_rates())
    report = import_csv(db, user, "zen.csv", build(*ROWS), provider=provider_with_rates())

    assert report.rows_new == 0
    assert report.rows_duplicate == 3
    assert db.query(Transaction).count() == 3


def test_import_converts_to_base_currency(db: Session, user):
    import_csv(db, user, "zen.csv", build(ROWS[0]), provider=provider_with_rates())

    tx = db.query(Transaction).one()
    assert tx.amount_base == Decimal("3.0000")
    assert tx.fx_status is FxStatus.OK
    assert tx.source is TxSource.CSV


def test_import_without_rates_keeps_rows_as_pending(db: Session, user):
    report = import_csv(db, user, "zen.csv", build(ROWS[0]), provider=FakeProvider({}, fail=True))

    tx = db.query(Transaction).one()
    assert report.rows_new == 1
    assert report.pending_fx == 1
    assert tx.amount_base is None
    assert tx.fx_status is FxStatus.PENDING


def test_manual_transactions_are_untouched(db: Session, user):
    manual = Transaction(
        user_id=user.id,
        date=dt.date(2026, 7, 1),
        category_name="Наличные",
        account_name="Cash",
        direction="outcome",
        amount_original=Decimal("50"),
        currency="USD",
        amount_base=Decimal("50"),
        fx_status=FxStatus.OK,
        source=TxSource.MANUAL,
    )
    db.add(manual)
    db.commit()

    import_csv(db, user, "zen.csv", build(*ROWS), provider=provider_with_rates())

    assert db.query(Transaction).filter_by(source=TxSource.MANUAL).count() == 1
    assert db.query(Transaction).count() == 4


def test_broken_rows_do_not_block_the_rest(db: Session, user):
    broken = row("не-дата", "Кофе", "300", "2026-07-04 09:00:00")
    report = import_csv(db, user, "zen.csv", build(broken, *ROWS), provider=provider_with_rates())

    assert report.rows_new == 3
    assert report.rows_error == 1
    assert len(report.errors) == 1


def test_import_batch_is_recorded(db: Session, user):
    import_csv(db, user, "zen.csv", build(*ROWS), provider=provider_with_rates())

    batch = db.query(ImportBatch).one()
    assert batch.filename == "zen.csv"
    assert batch.rows_total == 3
    assert batch.rows_new == 3
    assert batch.user_id == user.id


def test_rows_without_created_at_are_deduplicated(db: Session, user):
    """Пустой createdDate больше не отключает дедуп: ключ есть у всех строк."""
    no_created = (
        '2026-07-09;"Кофе";;;"Сербия ";"300";RSD;"Сербия ";"0";RSD;;;'
    )
    import_csv(db, user, "zen.csv", build(no_created), provider=provider_with_rates())
    report = import_csv(db, user, "zen.csv", build(no_created), provider=provider_with_rates())

    assert report.rows_new == 0
    assert db.query(Transaction).count() == 1


def test_import_fills_dedup_columns(db: Session, user):
    import_csv(db, user, "zen.csv", build(*ROWS), provider=provider_with_rates())

    saved = db.query(Transaction).order_by(Transaction.date).all()
    assert all(t.dedup_key for t in saved)
    assert all(t.dedup_seq == 0 for t in saved)


def shift_hour(csv_row: str) -> str:
    """Сдвинуть время создания на час, как при смене таймзоны устройства."""

    def bump(match: re.Match[str]) -> str:
        return f"{match.group(1)} {int(match.group(2)) + 1:02d}:{match.group(3)}"

    return re.sub(r"(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}:\d{2})", bump, csv_row)


def test_timezone_shift_does_not_duplicate(db: Session, user):
    """Прод-баг: выгрузка из другого пояса дублировала всю историю."""
    import_csv(db, user, "aug.csv", build(*ROWS), provider=provider_with_rates())

    shifted = build(*(shift_hour(r) for r in ROWS))
    report = import_csv(db, user, "sep.csv", shifted, provider=provider_with_rates())

    assert report.rows_new == 0
    assert report.rows_duplicate == 3
    assert db.query(Transaction).count() == 3


def test_repeated_identical_operations_all_imported(db: Session, user):
    """Четыре поездки по 75 за день — это четыре операции, а не одна."""
    same = [row("2026-07-05", "Транспорт", "75", f"2026-07-05 0{n}:00:00") for n in range(4)]
    report = import_csv(db, user, "zen.csv", build(*same), provider=provider_with_rates())

    assert report.rows_new == 4
    assert db.query(Transaction).count() == 4
    assert sorted(t.dedup_seq for t in db.query(Transaction)) == [0, 1, 2, 3]


def test_partial_overlap_adds_only_missing(db: Session, user):
    """В базе одна поездка, в файле две — добавится ровно одна."""
    one = row("2026-07-05", "Транспорт", "75", "2026-07-05 01:00:00")
    two = row("2026-07-05", "Транспорт", "75", "2026-07-05 02:00:00")
    import_csv(db, user, "a.csv", build(one), provider=provider_with_rates())

    report = import_csv(db, user, "b.csv", build(one, two), provider=provider_with_rates())

    assert report.rows_new == 1
    assert report.rows_duplicate == 1
    assert db.query(Transaction).count() == 2


def test_new_operations_still_arrive(db: Session, user):
    """Дозаливка: старое опознано, новое добавлено."""
    import_csv(db, user, "a.csv", build(*ROWS), provider=provider_with_rates())
    extra = row("2026-07-10", "Кофе", "350", "2026-07-10 09:00:00")

    report = import_csv(db, user, "b.csv", build(*ROWS, extra), provider=provider_with_rates())

    assert report.rows_new == 1
    assert report.rows_duplicate == 3
    assert db.query(Transaction).count() == 4


def test_manual_transaction_is_not_matched(db: Session, user):
    """Ручная операция не гасит строку CSV: у неё нет отпечатка."""
    db.add(
        Transaction(
            user_id=user.id,
            date=dt.date(2026, 7, 1),
            category_name="Кофе",
            account_name="Сербия ",
            direction=Direction.OUTCOME,
            amount_original=Decimal("300"),
            currency="RSD",
            source=TxSource.MANUAL,
            fx_status=FxStatus.PENDING,
        )
    )
    db.commit()

    report = import_csv(db, user, "zen.csv", build(*ROWS), provider=provider_with_rates())

    assert report.rows_new == 3
    assert db.query(Transaction).count() == 4
