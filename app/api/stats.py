"""Ручки статистики."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import current_user
from app.config import Settings, get_settings
from app.db import get_db
from app.models import User
from app.schemas import (
    CategoryDiffOut,
    CategoryDynamicsOut,
    CategorySliceOut,
    CompareOut,
    DynamicsOut,
    MonthPointOut,
    MonthsOut,
    MonthSummaryOut,
    TransactionOut,
)
from app.services import budget, recurring, stats

router = APIRouter(prefix="/api", tags=["stats"])

RECENT_LIMIT = 3
ZERO = Decimal(0)


def _month(value: str) -> dt.date:
    try:
        return stats.parse_month(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


def _slice_out(item: stats.CategorySlice) -> CategorySliceOut:
    return CategorySliceOut(
        category=item.category,
        amount=item.amount,
        share=item.share,
        delta_pct=item.delta_pct,
        tx_count=item.tx_count,
    )


@router.get("/meta/months", response_model=MonthsOut)
def months(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MonthsOut:
    available = stats.available_months(db, user.id)
    today = dt.date.today().replace(day=1)
    if today not in available:
        available = sorted({*available, today}, reverse=True)
    return MonthsOut(
        months=[stats.format_month(month) for month in available],
        current=stats.format_month(today),
    )


@router.get("/stats/month", response_model=MonthSummaryOut)
def month_summary(
    month: str = Query(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MonthSummaryOut:
    target = _month(month)
    # подписки начисляются лениво: планировщик мог не отработать
    recurring.run_safely(db, user)

    transactions = stats.fetch_month(db, user.id, target)
    previous = stats.fetch_month(db, user.id, stats.shift_month(target, -1))

    income, _, _ = budget.get_income(db, user, target)
    summary = stats.month_summary(transactions, income)
    categories = stats.category_breakdown(transactions, previous)

    recent = [
        item
        for item in transactions
        if not stats.is_excluded(item.category_name)
    ][:RECENT_LIMIT]

    return MonthSummaryOut(
        month=stats.format_month(target),
        income_manual=summary.income_manual,
        income_from_transactions=summary.income_from_transactions,
        income_total=summary.income_total,
        outcome_total=summary.outcome_total,
        saldo=summary.saldo,
        spent_share=summary.spent_share,
        tx_count=summary.tx_count,
        pending_count=summary.pending_count,
        base_currency=settings.base_currency,
        categories=[_slice_out(item) for item in categories],
        recent=[TransactionOut.model_validate(item) for item in recent],
    )


@router.get("/stats/categories", response_model=list[CategorySliceOut])
def categories(
    month: str = Query(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[CategorySliceOut]:
    target = _month(month)
    transactions = stats.fetch_month(db, user.id, target)
    previous = stats.fetch_month(db, user.id, stats.shift_month(target, -1))
    return [_slice_out(item) for item in stats.category_breakdown(transactions, previous)]


def _window(months_count: int, until: str | None) -> list[dt.date]:
    last = _month(until) if until else dt.date.today().replace(day=1)
    return [stats.shift_month(last, -offset) for offset in reversed(range(months_count))]


Series = tuple[list[MonthPointOut], Decimal, Decimal, Decimal | None]


def _series(points: list[stats.MonthPoint]) -> Series:
    amounts = [point.amount for point in points]
    total = sum(amounts, ZERO)
    average = total / len(points) if points else ZERO

    delta = None
    if len(amounts) >= 2 and amounts[-2] > 0:
        delta = (amounts[-1] - amounts[-2]) / amounts[-2]

    out = [
        MonthPointOut(
            month=stats.format_month(point.month),
            amount=point.amount,
            tx_count=point.tx_count,
        )
        for point in points
    ]
    return out, average, total, delta


@router.get("/stats/dynamics", response_model=DynamicsOut)
def total_dynamics(
    months_count: int = Query(default=12, ge=2, le=48, alias="months"),
    until: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DynamicsOut:
    """Общие траты по месяцам, без разбивки по категориям."""
    window = _window(months_count, until)
    start, _ = stats.month_bounds(window[0])
    _, end = stats.month_bounds(window[-1])
    transactions = stats.fetch_range(db, user.id, start, end)

    points, average, total, delta = _series(stats.monthly_totals(transactions, window))
    return DynamicsOut(points=points, average=average, total=total, delta_pct=delta)


@router.get("/stats/category-dynamics", response_model=CategoryDynamicsOut)
def category_dynamics(
    category: str = Query(...),
    months_count: int = Query(default=12, ge=2, le=48, alias="months"),
    until: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CategoryDynamicsOut:
    window = _window(months_count, until)
    start, _ = stats.month_bounds(window[0])
    _, end = stats.month_bounds(window[-1])
    transactions = stats.fetch_range(db, user.id, start, end)

    points, average, total, delta = _series(
        stats.category_dynamics(transactions, category, window)
    )
    return CategoryDynamicsOut(
        category=category,
        points=points,
        average=average,
        total=total,
        delta_pct=delta,
    )


@router.get("/stats/compare", response_model=CompareOut)
def compare(
    a: str = Query(...),
    b: str = Query(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CompareOut:
    month_a, month_b = _month(a), _month(b)
    transactions_a = stats.fetch_month(db, user.id, month_a)
    transactions_b = stats.fetch_month(db, user.id, month_b)

    income_a, _, _ = budget.get_income(db, user, month_a)
    income_b, _, _ = budget.get_income(db, user, month_b)
    summary_a = stats.month_summary(transactions_a, income_a)
    summary_b = stats.month_summary(transactions_b, income_b)

    diffs = stats.compare_months(transactions_a, transactions_b)
    return CompareOut(
        month_a=stats.format_month(month_a),
        month_b=stats.format_month(month_b),
        total_a=summary_a.outcome_total,
        total_b=summary_b.outcome_total,
        saldo_a=summary_a.saldo,
        saldo_b=summary_b.saldo,
        categories=[
            CategoryDiffOut(
                category=item.category,
                amount_a=item.amount_a,
                amount_b=item.amount_b,
                diff=item.diff,
                diff_pct=item.diff_pct,
            )
            for item in diffs
        ],
    )
