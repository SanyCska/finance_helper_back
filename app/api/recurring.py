"""Ручки подписок и постоянных трат вроде аренды."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.auth import current_user
from app.config import Settings, get_settings
from app.db import get_db
from app.models import RecurringExpense, Transaction, User
from app.schemas import (
    RecurringIn,
    RecurringListOut,
    RecurringOut,
    RecurringPatch,
    RecurringRunOut,
)
from app.services import recurring
from app.services.fx import FxService

router = APIRouter(prefix="/api/recurring", tags=["recurring"])


def _own_item(db: Session, user: User, item_id: int) -> RecurringExpense:
    item = db.get(RecurringExpense, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Подписка не найдена")
    return item


def _out(item: RecurringExpense, monthly_base: dict[int, Decimal | None]) -> RecurringOut:
    return RecurringOut(
        id=item.id,
        kind=item.kind,
        title=item.title,
        amount=item.amount,
        currency=item.currency,
        period_months=item.period_months,
        charge_day=item.charge_day,
        category_name=item.category_name,
        active=item.active,
        starts_on=item.starts_on,
        monthly_amount=recurring.monthly_amount(item),
        monthly_amount_base=monthly_base.get(item.id),
    )


def _monthly_base(db: Session, items: list[RecurringExpense]) -> dict[int, Decimal | None]:
    """Месячные доли в базовой валюте — одним походом за курсами."""
    if not items:
        return {}
    today = dt.date.today()
    fx = FxService(db)
    fx.ensure_rates((today, item.currency) for item in items)
    result: dict[int, Decimal | None] = {}
    for item in items:
        amount_base, _, _ = fx.convert(recurring.monthly_amount(item), item.currency, today)
        result[item.id] = amount_base
    return result


@router.get("", response_model=RecurringListOut)
def list_recurring(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RecurringListOut:
    # чтение списка — удобный момент дочислить пропущенные месяцы
    generated = recurring.run(db, user)
    items = recurring.list_items(db, user)
    monthly_base = _monthly_base(db, items)
    return RecurringListOut(
        items=[_out(item, monthly_base) for item in items],
        base_currency=settings.base_currency,
        monthly_total_base=recurring.monthly_total_base(db, user, items),
        generated=generated,
    )


@router.post("", response_model=RecurringOut, status_code=status.HTTP_201_CREATED)
def create_recurring(
    payload: RecurringIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RecurringOut:
    starts_on = (payload.starts_on or dt.date.today()).replace(day=1)
    item = RecurringExpense(
        user_id=user.id,
        kind=payload.kind,
        title=payload.title.strip(),
        amount=payload.amount,
        currency=payload.currency,
        period_months=payload.period_months,
        charge_day=payload.charge_day,
        category_name=(payload.category_name or "").strip()
        or recurring.DEFAULT_CATEGORY[payload.kind],
        active=True,
        starts_on=starts_on,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _out(item, _monthly_base(db, [item]))


@router.patch("/{item_id}", response_model=RecurringOut)
def update_recurring(
    item_id: int,
    payload: RecurringPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RecurringOut:
    item = _own_item(db, user, item_id)
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "currency" in updates:
        updates["currency"] = str(updates["currency"]).strip().upper()
    if "title" in updates:
        updates["title"] = str(updates["title"]).strip()
    if "category_name" in updates:
        updates["category_name"] = (
            str(updates["category_name"]).strip() or recurring.DEFAULT_CATEGORY[item.kind]
        )
    if "starts_on" in updates:
        updates["starts_on"] = updates["starts_on"].replace(day=1)

    for field, value in updates.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return _out(item, _monthly_base(db, [item]))


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recurring(
    item_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """Удаляет подписку вместе с её начислениями.

    Иначе начисления остались бы висеть в тратах без связи, а новая подписка
    с тем же названием начислила бы те же месяцы заново.
    """
    item = _own_item(db, user, item_id)
    db.execute(delete(Transaction).where(Transaction.recurring_id == item.id))
    db.delete(item)
    db.commit()


@router.post("/run", response_model=RecurringRunOut)
def run_recurring(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RecurringRunOut:
    return RecurringRunOut(generated=recurring.run(db, user))
