"""Тесты учёта средств и месячной сверки."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models import Direction, FundSource, FxRate, FxStatus, Transaction, TxSource, User
from app.services import budget, funds


@pytest.fixture
def source(db: Session, user: User) -> FundSource:
    item = FundSource(user_id=user.id, title="Сербия", currency="USD", position=0)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def add_source(db: Session, user: User, title: str, currency: str) -> FundSource:
    item = FundSource(user_id=user.id, title=title, currency=currency, position=1)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def add_tx(db: Session, user: User, day: str, amount: str, direction: Direction) -> None:
    db.add(
        Transaction(
            user_id=user.id,
            date=dt.date.fromisoformat(day),
            category_name="Кофе",
            account_name="Сербия",
            direction=direction,
            amount_original=Decimal(amount),
            currency="USD",
            amount_base=Decimal(amount),
            fx_rate=Decimal(1),
            fx_status=FxStatus.OK,
            source=TxSource.MANUAL,
        )
    )
    db.commit()


def test_balance_snapshots_keep_history(db: Session, user: User, source: FundSource):
    funds.set_balance(db, user, source, Decimal("100"), dt.date(2026, 6, 30))
    funds.set_balance(db, user, source, Decimal("150"), dt.date(2026, 7, 31))

    assert [item.amount_original for item in funds.history(db, source.id)] == [
        Decimal("150"),
        Decimal("100"),
    ]


def test_latest_balance_respects_date(db: Session, user: User, source: FundSource):
    funds.set_balance(db, user, source, Decimal("100"), dt.date(2026, 6, 30))
    funds.set_balance(db, user, source, Decimal("150"), dt.date(2026, 7, 31))

    on_june = funds.latest_balance(db, source.id, dt.date(2026, 6, 30))

    assert on_june.amount_original == Decimal("100")


def test_total_sums_sources_in_base_currency(db: Session, user: User, source: FundSource):
    # 1 EUR = 1.1 USD на дату снимка
    db.add(FxRate(date=dt.date(2026, 7, 31), currency="EUR", rate_to_base=Decimal("1.1")))
    db.commit()
    euro = add_source(db, user, "Европа", "EUR")

    funds.set_balance(db, user, source, Decimal("100"), dt.date(2026, 7, 31))
    funds.set_balance(db, user, euro, Decimal("200"), dt.date(2026, 7, 31))

    assert funds.total_base(db, user, dt.date(2026, 7, 31)) == Decimal("320.00")


def test_balance_history_gives_month_ends(db: Session, user: User, source: FundSource):
    funds.set_balance(db, user, source, Decimal("100"), dt.date(2026, 6, 15))
    funds.set_balance(db, user, source, Decimal("180"), dt.date(2026, 7, 20))

    points = funds.balance_history(db, user, [dt.date(2026, 6, 1), dt.date(2026, 7, 1)])

    assert [point.amount for point in points] == [Decimal("100.00"), Decimal("180.00")]


def test_month_check_shows_gap_between_real_and_tracked(
    db: Session, user: User, source: FundSource
):
    funds.set_balance(db, user, source, Decimal("1000"), dt.date(2026, 6, 30))
    funds.set_balance(db, user, source, Decimal("1200"), dt.date(2026, 7, 31))
    budget.set_income(db, user, dt.date(2026, 7, 1), Decimal("500"), None)
    add_tx(db, user, "2026-07-10", "250", Direction.OUTCOME)

    result = funds.month_check(db, user, dt.date(2026, 7, 1))

    assert result.real_saldo == Decimal("200.00")
    assert result.tracked_saldo == Decimal("250.00")
    # реально отложилось на 50 меньше, чем следует из введённых данных
    assert result.discrepancy == Decimal("-50.00")


def test_saved_check_is_not_recalculated(db: Session, user: User, source: FundSource):
    funds.set_balance(db, user, source, Decimal("1000"), dt.date(2026, 6, 30))
    funds.set_balance(db, user, source, Decimal("1200"), dt.date(2026, 7, 31))
    funds.save_check(db, user, dt.date(2026, 7, 1))

    # балансы поехали дальше, но подтверждённая сверка остаётся прежней
    funds.set_balance(db, user, source, Decimal("5000"), dt.date(2026, 8, 5))
    result = funds.month_check(db, user, dt.date(2026, 7, 1))

    assert result.real_saldo == Decimal("200.00")
    assert result.saved is not None


def test_pending_check_appears_only_with_sources(db: Session, user: User):
    today = dt.date(2026, 8, 3)

    assert funds.pending_check_month(db, user, today) is None

    add_source(db, user, "Наличные", "USD")

    assert funds.pending_check_month(db, user, today) == dt.date(2026, 7, 1)


def test_pending_check_clears_after_save(db: Session, user: User, source: FundSource):
    today = dt.date(2026, 8, 3)
    funds.save_check(db, user, dt.date(2026, 7, 1))

    assert funds.pending_check_month(db, user, today) is None
