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
    PlanLineFactOut,
    PlanLineOut,
    PlanOut,
    PlanVsFactOut,
    SettingsIn,
    SettingsOut,
    SuggestionOut,
)
from app.services import budget, recurring, stats

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


def _income_out(month: dt.date, state: budget.IncomeState) -> IncomeOut:
    return IncomeOut(
        month=stats.format_month(month),
        amount=state.amount,
        note=state.note,
        is_default=state.is_default,
        source=state.source,
        from_month=stats.format_month(state.from_month) if state.from_month else None,
    )


@router.get("/income/{month}", response_model=IncomeOut)
def get_income(
    month: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IncomeOut:
    target = _month(month)
    return _income_out(target, budget.income_state(db, user, target))


@router.put("/income/{month}", response_model=IncomeOut)
def put_income(
    month: str,
    payload: IncomeIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IncomeOut:
    target = _month(month)
    budget.set_income(db, user, target, payload.amount, payload.note, payload.save_as_default)
    return _income_out(target, budget.income_state(db, user, target))


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


# --- планы ----------------------------------------------------------------


def _plan_lines_out(
    db: Session, month: dt.date, lines: list[budget.DraftLine], ids: list[int]
) -> list[PlanLineOut]:
    bases = budget.lines_in_base(db, lines, month)
    return [
        PlanLineOut(
            id=line_id,
            title=line.title,
            amount=line.amount,
            currency=line.currency,
            amount_base=base,
            category_names=list(line.category_names),
            position=position,
        )
        for position, (line_id, line, base) in enumerate(zip(ids, lines, bases, strict=True))
    ]


def _draft_ids(db: Session, user: User, month: dt.date, source: str) -> list[int]:
    """Идентификаторы строк: свои у сохранённого плана, прошлого месяца — у черновика."""
    plan = budget.get_plan(db, user, month if source == "saved" else stats.shift_month(month, -1))
    return [line.id for line in (plan.lines if plan else [])]


@router.get("/plans/{month}", response_model=PlanOut)
def get_plan(
    month: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlanOut:
    target = _month(month)
    lines, source = budget.plan_draft(db, user, target)
    out = _plan_lines_out(db, target, lines, _draft_ids(db, user, target, source))
    total = sum((item.amount_base for item in out), ZERO)
    income, _, _ = budget.get_income(db, user, target)

    return PlanOut(
        month=stats.format_month(target),
        lines=out,
        total=total,
        income=income,
        expected_saldo=income - total,
        base_currency=settings.base_currency,
        source=source,
    )


@router.put("/plans/{month}", response_model=PlanOut)
def put_plan(
    month: str,
    payload: PlanIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlanOut:
    target = _month(month)
    lines = [
        budget.DraftLine(
            title=line.title.strip(),
            amount=line.amount,
            currency=line.currency,
            category_names=line.category_names,
        )
        for line in payload.lines
        if line.title.strip() or line.amount > 0
    ]
    plan = budget.save_plan(db, user, target, lines)

    drafts = [
        budget.DraftLine(item.title, item.amount, item.currency, list(item.category_names or []))
        for item in plan.lines
    ]
    out = _plan_lines_out(db, target, drafts, [item.id for item in plan.lines])
    total = sum((item.amount_base for item in out), ZERO)
    income, _, _ = budget.get_income(db, user, target)

    return PlanOut(
        month=stats.format_month(target),
        lines=out,
        total=total,
        income=income,
        expected_saldo=income - total,
        base_currency=settings.base_currency,
        source="saved",
    )


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
    # завершившийся месяц мог остаться без начислений подписок — дочисляем
    recurring.run_safely(db, user)

    plan = budget.get_plan(db, user, target)
    plan_lines = list(plan.lines) if plan else []
    drafts = [
        budget.DraftLine(item.title, item.amount, item.currency, list(item.category_names or []))
        for item in plan_lines
    ]
    lines_out = _plan_lines_out(db, target, drafts, [item.id for item in plan_lines])
    plan_sum = sum((item.amount_base for item in lines_out), ZERO)

    transactions = stats.fetch_month(db, user.id, target)
    previous = stats.fetch_month(db, user.id, stats.shift_month(target, -1))
    income, _, _ = budget.get_income(db, user, target)
    summary = stats.month_summary(transactions, income)
    categories = stats.category_breakdown(transactions, previous)
    fact_by_category = {item.category: item.amount for item in categories}

    diff = summary.outcome_total - plan_sum
    share = (summary.outcome_total / plan_sum) if plan_sum > 0 else None
    # точность: насколько факт близок к плану, 100% — идеальное попадание
    accuracy = None
    if plan_sum > 0:
        accuracy = max(ZERO, Decimal(1) - abs(diff) / plan_sum)

    with_fact = []
    linked: set[str] = set()
    for item in lines_out:
        fact = None
        if item.category_names:
            linked.update(item.category_names)
            # строка вроде «еда» покрывает сразу несколько категорий выгрузки
            fact = sum(
                (fact_by_category.get(name, ZERO) for name in item.category_names), ZERO
            )
        with_fact.append(
            PlanLineFactOut(
                **item.model_dump(),
                fact=fact,
                diff=(fact - item.amount_base) if fact is not None else None,
            )
        )

    return PlanVsFactOut(
        month=stats.format_month(target),
        plan_total=plan_sum,
        fact_total=summary.outcome_total,
        diff=diff,
        fact_share_of_plan=share,
        plan_saldo=income - plan_sum,
        fact_saldo=summary.saldo,
        accuracy=accuracy,
        lines=with_fact,
        categories=[_slice_out(item) for item in categories],
        unplanned=[_slice_out(item) for item in categories if item.category not in linked],
        has_plan=plan is not None,
    )


def _slice_out(item: stats.CategorySlice) -> CategorySliceOut:
    return CategorySliceOut(
        category=item.category,
        amount=item.amount,
        share=item.share,
        delta_pct=item.delta_pct,
        tx_count=item.tx_count,
    )
