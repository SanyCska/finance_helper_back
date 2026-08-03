"""Ручки текущих средств и месячной сверки."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import current_user
from app.config import Settings, get_settings
from app.db import get_db
from app.models import FundBalance, FundSource, User
from app.schemas import (
    BalanceIn,
    BalancePatch,
    BalancePointOut,
    FundBalanceOut,
    FundSourceIn,
    FundSourceOut,
    FundSourcePatch,
    FundsOut,
    MonthCheckIn,
    MonthCheckOut,
)
from app.services import funds, stats

router = APIRouter(prefix="/api/funds", tags=["funds"])

#: сколько месяцев показывает график общего баланса
HISTORY_MONTHS = 12


def _month(value: str) -> dt.date:
    try:
        return stats.parse_month(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


def _source_out(state: funds.SourceState) -> FundSourceOut:
    return FundSourceOut(
        id=state.source.id,
        title=state.source.title,
        currency=state.source.currency,
        position=state.source.position,
        archived=state.source.archived,
        amount_original=state.amount_original,
        amount_base=state.amount_base,
        updated_on=state.updated_on,
    )


def _own_source(db: Session, user: User, source_id: int) -> FundSource:
    source = db.get(FundSource, source_id)
    if source is None or source.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Источник не найден")
    return source


def _check_out(result: funds.MonthCheckResult) -> MonthCheckOut:
    return MonthCheckOut(
        month=stats.format_month(result.month),
        real_saldo=result.real_saldo,
        tracked_saldo=result.tracked_saldo,
        discrepancy=result.discrepancy,
        opening=result.opening,
        closing=result.closing,
        is_saved=result.saved is not None,
        comparable=result.comparable,
        note=result.saved.note if result.saved else None,
    )


def _own_balance(db: Session, user: User, source_id: int, balance_id: int) -> FundBalance:
    balance = db.get(FundBalance, balance_id)
    if balance is None or balance.user_id != user.id or balance.source_id != source_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Запись баланса не найдена")
    return balance


@router.get("", response_model=FundsOut)
def overview(
    months: int = Query(default=HISTORY_MONTHS, ge=2, le=48),
    include_archived: bool = Query(default=False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FundsOut:
    states = funds.source_states(db, user, include_archived=include_archived)
    window = [
        stats.shift_month(dt.date.today().replace(day=1), -offset)
        for offset in reversed(range(months))
    ]
    return FundsOut(
        base_currency=settings.base_currency,
        total_base=funds.total_base(db, user),
        sources=[_source_out(state) for state in states],
        history=[
            BalancePointOut(month=stats.format_month(point.month), amount=point.amount)
            for point in funds.balance_history(db, user, window)
        ],
        pending_check=(
            stats.format_month(month)
            if (month := funds.pending_check_month(db, user))
            else None
        ),
    )


@router.post("", response_model=FundSourceOut, status_code=status.HTTP_201_CREATED)
def create_source(
    payload: FundSourceIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FundSourceOut:
    source = FundSource(
        user_id=user.id,
        title=payload.title.strip(),
        currency=payload.currency,
        position=funds.next_position(db, user),
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    balance = None
    if payload.amount > 0:
        balance = funds.set_balance(db, user, source, payload.amount, dt.date.today())
    return _source_out(funds.SourceState(source=source, balance=balance))


@router.patch("/{source_id}", response_model=FundSourceOut)
def update_source(
    source_id: int,
    payload: FundSourcePatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FundSourceOut:
    source = _own_source(db, user, source_id)
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "currency" in updates:
        updates["currency"] = str(updates["currency"]).strip().upper()
    if "title" in updates:
        updates["title"] = str(updates["title"]).strip()
    for field, value in updates.items():
        setattr(source, field, value)
    db.commit()
    db.refresh(source)
    balance = funds.latest_balance(db, source.id)
    return _source_out(funds.SourceState(source=source, balance=balance))


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    source_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    source = _own_source(db, user, source_id)
    db.delete(source)
    db.commit()


@router.put("/{source_id}/balance", response_model=FundSourceOut)
def put_balance(
    source_id: int,
    payload: BalanceIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FundSourceOut:
    source = _own_source(db, user, source_id)
    day = payload.date or dt.date.today()
    if day > dt.date.today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Дата в будущем")
    balance = funds.set_balance(db, user, source, payload.amount, day, payload.note)
    return _source_out(funds.SourceState(source=source, balance=balance))


@router.patch("/{source_id}/balance/{balance_id}", response_model=FundBalanceOut)
def patch_balance(
    source_id: int,
    balance_id: int,
    payload: BalancePatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FundBalanceOut:
    """Правка записи баланса: опечатался в сумме или в дате."""
    balance = _own_balance(db, user, source_id, balance_id)
    if payload.date is not None and payload.date > dt.date.today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Дата в будущем")

    fields = payload.model_dump(exclude_unset=True)
    updated = funds.update_balance(
        db,
        balance,
        amount=payload.amount,
        day=payload.date,
        note=payload.note,
        note_set="note" in fields,
    )
    return FundBalanceOut.model_validate(updated)


@router.delete("/{source_id}/balance/{balance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_balance(
    source_id: int,
    balance_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    funds.delete_balance(db, _own_balance(db, user, source_id, balance_id))


@router.get("/{source_id}/history", response_model=list[FundBalanceOut])
def source_history(
    source_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[FundBalanceOut]:
    source = _own_source(db, user, source_id)
    return [FundBalanceOut.model_validate(item) for item in funds.history(db, source.id)]


@router.get("/checks", response_model=list[MonthCheckOut])
def list_checks(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[MonthCheckOut]:
    return [
        MonthCheckOut(
            month=stats.format_month(item.month),
            real_saldo=item.real_saldo,
            tracked_saldo=item.tracked_saldo,
            discrepancy=item.discrepancy,
            is_saved=True,
            note=item.note,
        )
        for item in funds.list_checks(db, user)
    ]


@router.get("/checks/{month}", response_model=MonthCheckOut)
def get_check(
    month: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MonthCheckOut:
    return _check_out(funds.month_check(db, user, _month(month)))


@router.post("/checks/{month}", response_model=MonthCheckOut)
def post_check(
    month: str,
    payload: MonthCheckIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MonthCheckOut:
    target = _month(month)
    if not funds.has_opening(db, user, target):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Это первый месяц учёта средств: остатка на его начало нет, "
            "сверять не с чем. Сверка станет возможна со следующего месяца.",
        )
    funds.save_check(db, user, target, payload.note)
    return _check_out(funds.month_check(db, user, target))
