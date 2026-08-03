"""Тесты подписок и постоянных трат."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RecurringExpense, RecurringKind, Transaction, TxSource, User
from app.services import recurring

TODAY = dt.date(2026, 8, 3)


def add_item(
    db: Session,
    user: User,
    title: str = "Netflix",
    amount: str = "120",
    period_months: int = 12,
    starts_on: dt.date = dt.date(2026, 6, 1),
    kind: RecurringKind = RecurringKind.SUBSCRIPTION,
    currency: str = "USD",
    active: bool = True,
) -> RecurringExpense:
    item = RecurringExpense(
        user_id=user.id,
        kind=kind,
        title=title,
        amount=Decimal(amount),
        currency=currency,
        period_months=period_months,
        charge_on=starts_on,
        category_name=recurring.DEFAULT_CATEGORY[kind],
        active=active,
        starts_on=starts_on,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def charges(db: Session, user: User) -> list[Transaction]:
    return list(
        db.scalars(
            select(Transaction)
            .where(Transaction.user_id == user.id, Transaction.source == TxSource.RECURRING)
            .order_by(Transaction.date)
        )
    )


def test_yearly_subscription_is_split_by_twelve(db: Session, user: User):
    item = add_item(db, user, amount="120", period_months=12)

    assert recurring.monthly_amount(item) == Decimal("10.00")


def test_monthly_subscription_keeps_amount(db: Session, user: User):
    item = add_item(db, user, amount="9.99", period_months=1)

    assert recurring.monthly_amount(item) == Decimal("9.99")


def test_run_charges_only_closed_months(db: Session, user: User):
    add_item(db, user, starts_on=dt.date(2026, 6, 1))

    recurring.run(db, user, TODAY)

    # июнь и июль закрыты, август ещё идёт
    assert [item.date for item in charges(db, user)] == [
        dt.date(2026, 6, 30),
        dt.date(2026, 7, 31),
    ]


def test_run_is_idempotent(db: Session, user: User):
    add_item(db, user)

    recurring.run(db, user, TODAY)
    created_again = recurring.run(db, user, TODAY)

    assert created_again == 0
    assert len(charges(db, user)) == 2


def test_months_before_start_are_skipped(db: Session, user: User):
    add_item(db, user, starts_on=dt.date(2026, 7, 1))

    recurring.run(db, user, TODAY)

    assert [item.date for item in charges(db, user)] == [dt.date(2026, 7, 31)]


def test_inactive_subscription_is_not_charged(db: Session, user: User):
    add_item(db, user, active=False)

    assert recurring.run(db, user, TODAY) == 0


def test_amount_change_applies_to_future_months_only(db: Session, user: User):
    item = add_item(db, user, amount="120", period_months=12, starts_on=dt.date(2026, 6, 1))
    recurring.run(db, user, dt.date(2026, 7, 3))  # начислен только июнь

    item.amount = Decimal("240")
    db.commit()
    recurring.run(db, user, TODAY)

    assert [item.amount_original for item in charges(db, user)] == [
        Decimal("10.0000"),
        Decimal("20.0000"),
    ]


def test_rent_uses_its_own_category(db: Session, user: User):
    add_item(db, user, title="Квартира", amount="700", period_months=1, kind=RecurringKind.RENT)

    recurring.run(db, user, TODAY)

    charge = charges(db, user)[0]
    assert charge.category_name == "Аренда квартиры"
    assert charge.payee == "Квартира"
    assert charge.direction.value == "outcome"


def test_charge_lands_on_last_day_of_month(db: Session, user: User):
    add_item(db, user, period_months=1, starts_on=dt.date(2026, 2, 1))

    recurring.run(db, user, dt.date(2026, 3, 5))

    assert charges(db, user)[0].date == dt.date(2026, 2, 28)


def test_backfill_is_capped(db: Session, user: User):
    item = add_item(db, user, starts_on=dt.date(2010, 1, 1))

    months = recurring.due_months(item, TODAY)

    assert len(months) == recurring.MAX_BACKFILL_MONTHS
    assert months[-1] == dt.date(2026, 7, 1)


def test_monthly_total_counts_active_only(db: Session, user: User):
    add_item(db, user, amount="120", period_months=12)
    add_item(db, user, title="Spotify", amount="10", period_months=1, active=False)

    items = recurring.list_items(db, user)

    assert recurring.monthly_total_base(db, user, items) == Decimal("10.00")


def test_other_kind_leaves_category_empty(db: Session, user: User):
    item = add_item(db, user, title="Страховка", period_months=1, kind=RecurringKind.OTHER)
    item.category_name = ""
    db.commit()

    recurring.run(db, user, TODAY)

    # пустая категория в интерфейсе показывается как «Без категории»
    assert charges(db, user)[0].category_name == ""


def test_next_charge_stays_in_future(db: Session, user: User):
    item = add_item(db, user, period_months=1, starts_on=dt.date(2026, 6, 15))
    item.charge_on = dt.date(2026, 6, 15)
    db.commit()

    assert recurring.next_charge(item, TODAY) == dt.date(2026, 8, 15)


def test_next_charge_of_yearly_keeps_its_month(db: Session, user: User):
    item = add_item(db, user, period_months=12)
    item.charge_on = dt.date(2026, 3, 5)
    db.commit()

    assert recurring.next_charge(item, TODAY) == dt.date(2027, 3, 5)


def test_next_charge_today_is_not_moved(db: Session, user: User):
    item = add_item(db, user, period_months=1)
    item.charge_on = TODAY
    db.commit()

    assert recurring.next_charge(item, TODAY) == TODAY


def test_charge_day_is_clamped_to_short_month(db: Session, user: User):
    item = add_item(db, user, period_months=1)
    item.charge_on = dt.date(2026, 1, 31)
    db.commit()

    # 31 января плюс месяц — конец февраля, а не 3 марта
    assert recurring.next_charge(item, dt.date(2026, 2, 1)) == dt.date(2026, 2, 28)
