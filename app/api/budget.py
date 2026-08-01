"""Ручки дохода, настроек и планов."""

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
    CategorySliceOut,
    IncomeIn,
    IncomeOut,
    PlanIn,
    PlanLineOut,
    PlanOut,
    PlanVsFactOut,
    SettingsIn,
    SettingsOut,
    SuggestionOut,
)
from app.services import budget, stats

router = APIRouter(prefix="/api", tags=["budget"])

ZERO = Decimal(0)
#: сколько прошлых месяцев усредняем при автозаполнении плана
AVERAGE_WINDOW = 3
#: сколько строк предлагать
SUGGESTION_LIMIT = 8


def _month(value: str) -> dt.date:
    try:
        return stats.parse_month(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


def _plan_out(month: dt.date, plan, income: Decimal) -> PlanOut:
    lines = [
        PlanLineOut(id=line.id, title=line.title, amount=line.amount, position=line.position)
        for line in (plan.lines if plan else [])
    ]
    total = budget.plan_total(plan)
    return PlanOut(
        month=stats.format_month(month),
        lines=lines,
        total=total,
        income=income,
        expected_saldo=income - total,
    )


@router.get("/income/{month}", response_model=IncomeOut)
def get_income(
    month: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IncomeOut:
    target = _month(month)
    amount, note, is_default = budget.get_income(db, user, target)
    return IncomeOut(
        month=stats.format_month(target),
        amount=amount,
        note=note,
        is_default=is_default,
    )


@router.put("/income/{month}", response_model=IncomeOut)
def put_income(
    month: str,
    payload: IncomeIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IncomeOut:
    target = _month(month)
    record = budget.set_income(
        db, user, target, payload.amount, payload.note, payload.save_as_default
    )
    return IncomeOut(
        month=stats.format_month(target),
        amount=record.amount,
        note=record.note,
        is_default=False,
    )


@router.get("/settings", response_model=SettingsOut)
def get_user_settings(
    user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> SettingsOut:
    return SettingsOut(
        base_currency=settings.base_currency,
        default_monthly_income=user.default_monthly_income,
        excluded_categories=settings.excluded_categories,
    )


@router.put("/settings", response_model=SettingsOut)
def put_user_settings(
    payload: SettingsIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SettingsOut:
    user.default_monthly_income = payload.default_monthly_income
    db.commit()
    return SettingsOut(
        base_currency=settings.base_currency,
        default_monthly_income=user.default_monthly_income,
        excluded_categories=settings.excluded_categories,
    )


@router.get("/plans/{month}", response_model=PlanOut)
def get_plan(
    month: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PlanOut:
    target = _month(month)
    income, _, _ = budget.get_income(db, user, target)
    return _plan_out(target, budget.get_plan(db, user, target), income)


@router.put("/plans/{month}", response_model=PlanOut)
def put_plan(
    month: str,
    payload: PlanIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PlanOut:
    target = _month(month)
    lines = [
        (line.title.strip(), line.amount)
        for line in payload.lines
        if line.title.strip() or line.amount > 0
    ]
    plan = budget.save_plan(db, user, target, lines)
    income, _, _ = budget.get_income(db, user, target)
    return _plan_out(target, plan, income)


@router.get("/plans/{month}/suggestions", response_model=list[SuggestionOut])
def plan_suggestions(
    month: str,
    window: int = Query(default=AVERAGE_WINDOW, ge=1, le=12),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[SuggestionOut]:
    """Строки плана по средним тратам за прошлые месяцы."""
    target = _month(month)
    months = [stats.shift_month(target, -offset) for offset in range(1, window + 1)]
    start, _ = stats.month_bounds(min(months))
    _, end = stats.month_bounds(max(months))

    transactions = stats.fetch_range(db, user.id, start, end)
    averages = stats.category_averages(transactions, months)

    ordered = sorted(averages.items(), key=lambda item: -item[1])[:SUGGESTION_LIMIT]
    return [
        SuggestionOut(
            title=title or "Без категории",
            amount=amount.quantize(Decimal("1")),
        )
        for title, amount in ordered
        if amount > 0
    ]


@router.get("/plans/{month}/vs-fact", response_model=PlanVsFactOut)
def plan_vs_fact(
    month: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PlanVsFactOut:
    target = _month(month)
    plan = budget.get_plan(db, user, target)
    plan_sum = budget.plan_total(plan)

    transactions = stats.fetch_month(db, user.id, target)
    previous = stats.fetch_month(db, user.id, stats.shift_month(target, -1))
    income, _, _ = budget.get_income(db, user, target)
    summary = stats.month_summary(transactions, income)

    diff = summary.outcome_total - plan_sum
    share = (summary.outcome_total / plan_sum) if plan_sum > 0 else None
    # точность: насколько факт близок к плану, 100% — идеальное попадание
    accuracy = None
    if plan_sum > 0:
        accuracy = max(ZERO, Decimal(1) - abs(diff) / plan_sum)

    return PlanVsFactOut(
        month=stats.format_month(target),
        plan_total=plan_sum,
        fact_total=summary.outcome_total,
        diff=diff,
        fact_share_of_plan=share,
        plan_saldo=income - plan_sum,
        fact_saldo=summary.saldo,
        accuracy=accuracy,
        lines=[
            PlanLineOut(id=line.id, title=line.title, amount=line.amount, position=line.position)
            for line in (plan.lines if plan else [])
        ],
        categories=[
            CategorySliceOut(
                category=item.category,
                amount=item.amount,
                share=item.share,
                delta_pct=item.delta_pct,
                tx_count=item.tx_count,
            )
            for item in stats.category_breakdown(transactions, previous)
        ],
        has_plan=plan is not None,
    )
