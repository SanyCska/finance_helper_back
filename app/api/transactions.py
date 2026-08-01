"""Ручки списка и правки транзакций."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.models import Transaction, TxSource, User
from app.schemas import (
    CategoryOut,
    TransactionCreate,
    TransactionOut,
    TransactionPage,
    TransactionUpdate,
)
from app.services import stats
from app.services.fx import FxService

router = APIRouter(prefix="/api", tags=["transactions"])


def _month_filter(query, month: str | None):
    if not month:
        return query
    try:
        target = stats.parse_month(month)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    first, last = stats.month_bounds(target)
    return query.where(Transaction.date >= first, Transaction.date <= last)


@router.get("/transactions", response_model=TransactionPage)
def list_transactions(
    month: str | None = Query(default=None),
    categories: list[str] | None = Query(default=None),
    accounts: list[str] | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TransactionPage:
    query = select(Transaction).where(Transaction.user_id == user.id)
    query = _month_filter(query, month)

    if categories:
        query = query.where(Transaction.category_name.in_(categories))
    if accounts:
        query = query.where(Transaction.account_name.in_(accounts))
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                Transaction.category_name.ilike(pattern),
                Transaction.comment.ilike(pattern),
                Transaction.payee.ilike(pattern),
                Transaction.account_name.ilike(pattern),
            )
        )

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        db.scalars(
            query.order_by(Transaction.date.desc(), Transaction.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )

    return TransactionPage(
        items=[TransactionOut.model_validate(row) for row in rows],
        total=total,
        has_more=offset + len(rows) < total,
    )


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(
    month: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[CategoryOut]:
    query = select(Transaction.category_name, func.count(Transaction.id)).where(
        Transaction.user_id == user.id
    )
    query = _month_filter(query, month)
    rows = db.execute(query.group_by(Transaction.category_name)).all()
    return sorted(
        (CategoryOut(name=name, tx_count=count) for name, count in rows),
        key=lambda item: -item.tx_count,
    )


@router.post("/transactions", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def create_transaction(
    payload: TransactionCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TransactionOut:
    if payload.date > dt.date.today() + dt.timedelta(days=1):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Дата в будущем")

    fx = FxService(db)
    fx.ensure_rates([(payload.date, payload.currency)])
    amount_base, rate, fx_status = fx.convert(
        payload.amount_original, payload.currency, payload.date
    )

    transaction = Transaction(
        user_id=user.id,
        date=payload.date,
        category_name=payload.category_name,
        account_name=payload.account_name,
        payee=payload.payee,
        comment=payload.comment,
        direction=payload.direction,
        amount_original=payload.amount_original,
        currency=payload.currency,
        amount_base=amount_base,
        fx_rate=rate,
        fx_status=fx_status,
        source=TxSource.MANUAL,
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return TransactionOut.model_validate(transaction)


def _own_manual_transaction(db: Session, user: User, transaction_id: int) -> Transaction:
    transaction = db.get(Transaction, transaction_id)
    if transaction is None or transaction.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Операция не найдена")
    if transaction.source is not TxSource.MANUAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Операция пришла из выгрузки: правьте её в Дзен-мани, иначе импорт вернёт прежнее значение",
        )
    return transaction


@router.patch("/transactions/{transaction_id}", response_model=TransactionOut)
def update_transaction(
    transaction_id: int,
    payload: TransactionUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TransactionOut:
    transaction = _own_manual_transaction(db, user, transaction_id)

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(transaction, field, value)

    if {"amount_original", "currency", "date"} & updates.keys():
        fx = FxService(db)
        fx.ensure_rates([(transaction.date, transaction.currency)])
        amount_base, rate, fx_status = fx.convert(
            transaction.amount_original, transaction.currency, transaction.date
        )
        transaction.amount_base = amount_base
        transaction.fx_rate = rate
        transaction.fx_status = fx_status

    db.commit()
    db.refresh(transaction)
    return TransactionOut.model_validate(transaction)


@router.delete("/transactions/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    transaction_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    transaction = _own_manual_transaction(db, user, transaction_id)
    db.delete(transaction)
    db.commit()
