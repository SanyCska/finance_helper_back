"""Тесты агрегаций."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.models import Direction, FxStatus, Transaction, TxSource
from app.services import stats


def tx(
    day: str,
    category: str,
    amount: str,
    direction: Direction = Direction.OUTCOME,
    fx_status: FxStatus = FxStatus.OK,
) -> Transaction:
    base = None if fx_status is FxStatus.PENDING else Decimal(amount)
    return Transaction(
        id=abs(hash((day, category, amount, str(direction)))) % 10**6,
        user_id=1,
        date=dt.date.fromisoformat(day),
        category_name=category,
        account_name="Сербия",
        direction=direction,
        amount_original=Decimal(amount),
        currency="USD",
        amount_base=base,
        fx_status=fx_status,
        source=TxSource.CSV,
    )


def test_month_summary_computes_saldo():
    rows = [tx("2026-07-01", "Кофе", "100"), tx("2026-07-02", "Рестики", "300")]

    summary = stats.month_summary(rows, income_manual=Decimal("1000"))

    assert summary.outcome_total == Decimal("400")
    assert summary.income_total == Decimal("1000")
    assert summary.saldo == Decimal("600")
    assert summary.tx_count == 2


def test_income_transactions_add_to_manual_income():
    rows = [
        tx("2026-07-01", "Кофе", "100"),
        tx("2026-07-05", "продажа", "50", direction=Direction.INCOME),
    ]

    summary = stats.month_summary(rows, income_manual=Decimal("1000"))

    assert summary.income_manual == Decimal("1000")
    assert summary.income_from_transactions == Decimal("50")
    assert summary.income_total == Decimal("1050")
    assert summary.saldo == Decimal("950")


def test_correction_category_is_excluded():
    rows = [tx("2026-07-01", "Кофе", "100"), tx("2026-07-02", "Correction", "999")]

    summary = stats.month_summary(rows, income_manual=Decimal("0"))

    assert summary.outcome_total == Decimal("100")


def test_correction_is_matched_ignoring_case_and_spaces():
    rows = [tx("2026-07-02", " correction ", "999")]

    assert stats.month_summary(rows, income_manual=Decimal("0")).outcome_total == Decimal("0")


def test_pending_fx_rows_are_excluded_and_counted():
    rows = [
        tx("2026-07-01", "Кофе", "100"),
        tx("2026-07-02", "Рестики", "300", fx_status=FxStatus.PENDING),
    ]

    summary = stats.month_summary(rows, income_manual=Decimal("0"))

    assert summary.outcome_total == Decimal("100")
    assert summary.pending_count == 1


def test_empty_month_gives_zeros_without_division_error():
    summary = stats.month_summary([], income_manual=Decimal("0"))

    assert summary.outcome_total == Decimal("0")
    assert summary.saldo == Decimal("0")
    assert summary.spent_share is None


def test_spent_share_is_ratio_of_income():
    rows = [tx("2026-07-01", "Кофе", "250")]

    summary = stats.month_summary(rows, income_manual=Decimal("1000"))

    assert summary.spent_share == Decimal("0.25")


def test_category_breakdown_is_sorted_and_shares_sum_to_one():
    rows = [
        tx("2026-07-01", "Кофе", "100"),
        tx("2026-07-02", "Рестики", "300"),
        tx("2026-07-03", "Рестики", "100"),
    ]

    slices = stats.category_breakdown(rows, [])

    assert [item.category for item in slices] == ["Рестики", "Кофе"]
    assert slices[0].amount == Decimal("400")
    assert sum(item.share for item in slices) == Decimal("1")


def test_category_delta_against_previous_month():
    current = [tx("2026-07-01", "Кофе", "150")]
    previous = [tx("2026-06-01", "Кофе", "100")]

    slices = stats.category_breakdown(current, previous)

    assert slices[0].delta_pct == Decimal("0.5")


def test_category_delta_is_none_when_previous_month_empty():
    slices = stats.category_breakdown([tx("2026-07-01", "Кофе", "150")], [])

    assert slices[0].delta_pct is None


def test_empty_category_keeps_empty_name():
    slices = stats.category_breakdown([tx("2026-07-01", "", "10")], [])

    assert slices[0].category == ""


def test_categories_differing_by_trailing_space_stay_separate():
    rows = [tx("2026-07-01", "Кофе", "10"), tx("2026-07-02", "Кофе ", "20")]

    slices = stats.category_breakdown(rows, [])

    assert len(slices) == 2


def test_category_dynamics_returns_point_per_month():
    rows = [
        tx("2026-05-01", "Кофе", "10"),
        tx("2026-06-01", "Кофе", "20"),
        tx("2026-06-15", "Кофе", "5"),
    ]

    points = stats.category_dynamics(rows, "Кофе", months=[dt.date(2026, 5, 1), dt.date(2026, 6, 1)])

    assert [point.amount for point in points] == [Decimal("10"), Decimal("25")]
    assert [point.month for point in points] == [dt.date(2026, 5, 1), dt.date(2026, 6, 1)]


def test_category_dynamics_fills_months_without_spending_with_zero():
    rows = [tx("2026-06-01", "Кофе", "20")]

    points = stats.category_dynamics(rows, "Кофе", months=[dt.date(2026, 5, 1), dt.date(2026, 6, 1)])

    assert points[0].amount == Decimal("0")


def test_compare_months_reports_diff_per_category():
    a = [tx("2026-06-01", "Кофе", "100"), tx("2026-06-02", "Рестики", "50")]
    b = [tx("2026-07-01", "Кофе", "150")]

    diffs = stats.compare_months(a, b)
    by_category = {item.category: item for item in diffs}

    assert by_category["Кофе"].amount_a == Decimal("100")
    assert by_category["Кофе"].amount_b == Decimal("150")
    assert by_category["Кофе"].diff == Decimal("50")
    assert by_category["Рестики"].amount_b == Decimal("0")
    assert by_category["Рестики"].diff == Decimal("-50")


def test_month_bounds():
    assert stats.month_bounds(dt.date(2026, 12, 1)) == (dt.date(2026, 12, 1), dt.date(2026, 12, 31))
    assert stats.month_bounds(dt.date(2026, 2, 1)) == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))


def test_parse_month_accepts_iso_month():
    assert stats.parse_month("2026-07") == dt.date(2026, 7, 1)


def test_average_of_last_months_ignores_current():
    rows = [
        tx("2026-04-01", "Кофе", "90"),
        tx("2026-05-01", "Кофе", "100"),
        tx("2026-06-01", "Кофе", "200"),
    ]

    averages = stats.category_averages(rows, months=[dt.date(2026, 4, 1), dt.date(2026, 5, 1), dt.date(2026, 6, 1)])

    assert averages["Кофе"] == Decimal("130")
