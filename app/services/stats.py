"""Агрегации по транзакциям.

Все функции — чистые: принимают список транзакций и возвращают структуры данных.
Объём (около 150 операций в месяц) позволяет считать на стороне Python, а не в SQL,
и покрывать логику тестами без базы.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Direction, FxStatus, Transaction

ZERO = Decimal(0)


# --- вспомогательное ------------------------------------------------------


def parse_month(value: str) -> dt.date:
    """`'2026-07'` или `'2026-07-01'` → первое число месяца."""
    text = (value or "").strip()
    try:
        if len(text) == 7:
            return dt.date.fromisoformat(text + "-01")
        return dt.date.fromisoformat(text).replace(day=1)
    except ValueError as exc:
        raise ValueError(f"Некорректный месяц: {value!r}") from exc


def format_month(month: dt.date) -> str:
    return month.strftime("%Y-%m")


def month_bounds(month: dt.date) -> tuple[dt.date, dt.date]:
    first = month.replace(day=1)
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    return first, last


def shift_month(month: dt.date, delta: int) -> dt.date:
    index = month.year * 12 + (month.month - 1) + delta
    return dt.date(index // 12, index % 12 + 1, 1)


def is_excluded(category_name: str) -> bool:
    return get_settings().is_excluded_category(category_name)


def _countable(transaction: Transaction) -> bool:
    """Учитывается ли операция в денежных итогах."""
    return (
        not is_excluded(transaction.category_name)
        and transaction.fx_status is not FxStatus.PENDING
        and transaction.amount_base is not None
    )


def _outcomes(transactions: Iterable[Transaction]) -> list[Transaction]:
    return [
        item
        for item in transactions
        if item.direction is Direction.OUTCOME and _countable(item)
    ]


# --- сводка месяца --------------------------------------------------------


@dataclass
class MonthSummary:
    income_manual: Decimal
    income_from_transactions: Decimal
    income_total: Decimal
    outcome_total: Decimal
    saldo: Decimal
    spent_share: Decimal | None
    tx_count: int
    pending_count: int


def month_summary(
    transactions: Sequence[Transaction], income_manual: Decimal
) -> MonthSummary:
    income_manual = Decimal(income_manual or 0)
    outcomes = _outcomes(transactions)
    incomes = [
        item
        for item in transactions
        if item.direction is Direction.INCOME and _countable(item)
    ]

    outcome_total = sum((item.amount_base for item in outcomes), ZERO)
    income_from_transactions = sum((item.amount_base for item in incomes), ZERO)
    income_total = income_manual + income_from_transactions
    pending_count = sum(
        1
        for item in transactions
        if item.fx_status is FxStatus.PENDING and not is_excluded(item.category_name)
    )

    return MonthSummary(
        income_manual=income_manual,
        income_from_transactions=income_from_transactions,
        income_total=income_total,
        outcome_total=outcome_total,
        saldo=income_total - outcome_total,
        spent_share=(outcome_total / income_total) if income_total > 0 else None,
        tx_count=len(outcomes) + len(incomes),
        pending_count=pending_count,
    )


# --- категории ------------------------------------------------------------


@dataclass
class CategorySlice:
    category: str
    amount: Decimal
    share: Decimal
    delta_pct: Decimal | None
    tx_count: int


def _totals_by_category(transactions: Iterable[Transaction]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for item in _outcomes(transactions):
        totals[item.category_name] += item.amount_base
    return dict(totals)


def category_breakdown(
    transactions: Sequence[Transaction], previous: Sequence[Transaction]
) -> list[CategorySlice]:
    totals = _totals_by_category(transactions)
    previous_totals = _totals_by_category(previous)
    counts: dict[str, int] = defaultdict(int)
    for item in _outcomes(transactions):
        counts[item.category_name] += 1

    grand_total = sum(totals.values(), ZERO)
    slices = []
    for category, amount in totals.items():
        before = previous_totals.get(category, ZERO)
        slices.append(
            CategorySlice(
                category=category,
                amount=amount,
                share=(amount / grand_total) if grand_total > 0 else ZERO,
                delta_pct=((amount - before) / before) if before > 0 else None,
                tx_count=counts[category],
            )
        )
    slices.sort(key=lambda item: (-item.amount, item.category))
    return slices


def category_averages(
    transactions: Sequence[Transaction], months: Sequence[dt.date]
) -> dict[str, Decimal]:
    """Средняя трата по категориям за перечисленные месяцы."""
    if not months:
        return {}
    wanted = {month.replace(day=1) for month in months}
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for item in _outcomes(transactions):
        if item.date.replace(day=1) in wanted:
            totals[item.category_name] += item.amount_base
    divisor = Decimal(len(wanted))
    return {category: amount / divisor for category, amount in totals.items()}


# --- динамика -------------------------------------------------------------


@dataclass
class MonthPoint:
    month: dt.date
    amount: Decimal
    tx_count: int


def category_dynamics(
    transactions: Sequence[Transaction], category: str, months: Sequence[dt.date]
) -> list[MonthPoint]:
    totals: dict[dt.date, Decimal] = {month.replace(day=1): ZERO for month in months}
    counts: dict[dt.date, int] = dict.fromkeys(totals, 0)

    for item in _outcomes(transactions):
        if item.category_name != category:
            continue
        key = item.date.replace(day=1)
        if key in totals:
            totals[key] += item.amount_base
            counts[key] += 1

    return [
        MonthPoint(month=month, amount=totals[month], tx_count=counts[month])
        for month in sorted(totals)
    ]


def monthly_totals(
    transactions: Sequence[Transaction], months: Sequence[dt.date]
) -> list[MonthPoint]:
    """Суммарные траты по месяцам — для графика на экране сравнения."""
    totals: dict[dt.date, Decimal] = {month.replace(day=1): ZERO for month in months}
    counts: dict[dt.date, int] = dict.fromkeys(totals, 0)
    for item in _outcomes(transactions):
        key = item.date.replace(day=1)
        if key in totals:
            totals[key] += item.amount_base
            counts[key] += 1
    return [
        MonthPoint(month=month, amount=totals[month], tx_count=counts[month])
        for month in sorted(totals)
    ]


# --- сравнение месяцев ----------------------------------------------------


@dataclass
class CategoryDiff:
    category: str
    amount_a: Decimal
    amount_b: Decimal
    diff: Decimal
    diff_pct: Decimal | None


def compare_months(
    transactions_a: Sequence[Transaction], transactions_b: Sequence[Transaction]
) -> list[CategoryDiff]:
    totals_a = _totals_by_category(transactions_a)
    totals_b = _totals_by_category(transactions_b)

    diffs = []
    for category in set(totals_a) | set(totals_b):
        amount_a = totals_a.get(category, ZERO)
        amount_b = totals_b.get(category, ZERO)
        diffs.append(
            CategoryDiff(
                category=category,
                amount_a=amount_a,
                amount_b=amount_b,
                diff=amount_b - amount_a,
                diff_pct=((amount_b - amount_a) / amount_a) if amount_a > 0 else None,
            )
        )
    diffs.sort(key=lambda item: (-max(item.amount_a, item.amount_b), item.category))
    return diffs


# --- выборки из базы ------------------------------------------------------


def fetch_month(db: Session, user_id: int, month: dt.date) -> list[Transaction]:
    first, last = month_bounds(month)
    return list(
        db.scalars(
            select(Transaction)
            .where(
                Transaction.user_id == user_id,
                Transaction.date >= first,
                Transaction.date <= last,
            )
            .order_by(Transaction.date.desc(), Transaction.id.desc())
        )
    )


def fetch_range(db: Session, user_id: int, start: dt.date, end: dt.date) -> list[Transaction]:
    return list(
        db.scalars(
            select(Transaction)
            .where(
                Transaction.user_id == user_id,
                Transaction.date >= start,
                Transaction.date <= end,
            )
            .order_by(Transaction.date.desc(), Transaction.id.desc())
        )
    )


def available_months(db: Session, user_id: int) -> list[dt.date]:
    """Месяцы, в которых есть операции, от свежих к старым."""
    dates = db.scalars(select(Transaction.date).where(Transaction.user_id == user_id))
    return sorted({value.replace(day=1) for value in dates}, reverse=True)
