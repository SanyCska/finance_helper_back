"""Тесты зарплаты и планов."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import FxRate, User
from app.services import budget

JULY = dt.date(2026, 7, 1)
AUGUST = dt.date(2026, 8, 1)
SEPTEMBER = dt.date(2026, 9, 1)


# --- зарплата -------------------------------------------------------------


def test_income_carries_forward(db: Session, user: User):
    budget.set_income(db, user, JULY, Decimal("3000"), None)

    state = budget.income_state(db, user, SEPTEMBER)

    assert state.amount == Decimal("3000.00")
    assert state.source == "carried"
    assert state.from_month == JULY


def test_own_month_beats_carried(db: Session, user: User):
    budget.set_income(db, user, JULY, Decimal("3000"), None)
    budget.set_income(db, user, AUGUST, Decimal("3500"), None)

    assert budget.income_state(db, user, AUGUST).amount == Decimal("3500.00")
    assert budget.income_state(db, user, AUGUST).source == "saved"


def test_change_spreads_to_later_months(db: Session, user: User):
    budget.set_income(db, user, JULY, Decimal("3000"), None)
    budget.set_income(db, user, AUGUST, Decimal("3500"), None)

    # сентябрь без своей записи берёт последнюю введённую — августовскую
    assert budget.income_state(db, user, SEPTEMBER).amount == Decimal("3500.00")


def test_past_months_are_not_touched_by_later_income(db: Session, user: User):
    budget.set_income(db, user, AUGUST, Decimal("3500"), None)

    state = budget.income_state(db, user, JULY)

    assert state.amount == Decimal(0)
    assert state.source == "default"


def test_default_income_is_the_last_resort(db: Session, user: User):
    user.default_monthly_income = Decimal("2000")
    db.commit()

    state = budget.income_state(db, user, JULY)

    assert state.amount == Decimal("2000")
    assert state.source == "default"


# --- планы ----------------------------------------------------------------


def test_plan_draft_falls_back_to_previous_month(db: Session, user: User):
    budget.save_plan(
        db,
        user,
        JULY,
        [budget.DraftLine("Аренда", Decimal("700"), "USD", ["Аренда"])],
    )

    lines, source = budget.plan_draft(db, user, AUGUST)

    assert source == "previous"
    assert [line.title for line in lines] == ["Аренда"]
    assert lines[0].category_names == ["Аренда"]


def test_saved_plan_wins_over_draft(db: Session, user: User):
    budget.save_plan(db, user, JULY, [budget.DraftLine("Аренда", Decimal("700"))])
    budget.save_plan(db, user, AUGUST, [budget.DraftLine("Кофе", Decimal("50"))])

    lines, source = budget.plan_draft(db, user, AUGUST)

    assert source == "saved"
    assert [line.title for line in lines] == ["Кофе"]


def test_empty_history_gives_empty_draft(db: Session, user: User):
    lines, source = budget.plan_draft(db, user, AUGUST)

    assert (lines, source) == ([], "empty")


def test_euro_line_is_converted_to_base(db: Session, user: User):
    day = budget.plan_rate_day(JULY)
    db.add(FxRate(date=day, currency="EUR", rate_to_base=Decimal("1.1")))
    db.commit()

    lines = [
        budget.DraftLine("Аренда", Decimal("500"), "EUR"),
        budget.DraftLine("Кофе", Decimal("50")),
    ]

    assert budget.lines_in_base(db, lines, JULY) == [Decimal("550.00"), Decimal("50.00")]


def test_rate_day_never_runs_ahead_of_today(db: Session, user: User):
    far_future = dt.date(2030, 5, 1)

    assert budget.plan_rate_day(far_future, dt.date(2026, 8, 3)) == dt.date(2026, 8, 3)
    assert budget.plan_rate_day(JULY, dt.date(2026, 8, 3)) == dt.date(2026, 7, 31)
