"""Доход месяца и планы трат."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MonthlyIncome, Plan, PlanLine, User

ZERO = Decimal(0)


def get_income(db: Session, user: User, month: dt.date) -> tuple[Decimal, str | None, bool]:
    """Доход месяца. Третий элемент — признак «взят из значения по умолчанию»."""
    record = db.scalar(
        select(MonthlyIncome).where(
            MonthlyIncome.user_id == user.id,
            MonthlyIncome.month == month,
        )
    )
    if record is not None:
        return record.amount, record.note, False
    return (user.default_monthly_income or ZERO), None, True


def set_income(
    db: Session,
    user: User,
    month: dt.date,
    amount: Decimal,
    note: str | None,
    save_as_default: bool = False,
) -> MonthlyIncome:
    record = db.scalar(
        select(MonthlyIncome).where(
            MonthlyIncome.user_id == user.id,
            MonthlyIncome.month == month,
        )
    )
    if record is None:
        record = MonthlyIncome(user_id=user.id, month=month, amount=amount, note=note)
        db.add(record)
    else:
        record.amount = amount
        record.note = note

    if save_as_default:
        user.default_monthly_income = amount

    db.commit()
    return record


def get_plan(db: Session, user: User, month: dt.date) -> Plan | None:
    return db.scalar(
        select(Plan).where(Plan.user_id == user.id, Plan.month == month)
    )


def save_plan(
    db: Session, user: User, month: dt.date, lines: list[tuple[str, Decimal]]
) -> Plan:
    plan = get_plan(db, user, month)
    if plan is None:
        plan = Plan(user_id=user.id, month=month)
        db.add(plan)
        db.flush()

    plan.lines.clear()
    db.flush()
    for position, (title, amount) in enumerate(lines):
        plan.lines.append(PlanLine(title=title, amount=amount, position=position))

    db.commit()
    db.refresh(plan)
    return plan


def plan_total(plan: Plan | None) -> Decimal:
    if plan is None:
        return ZERO
    return sum((line.amount for line in plan.lines), ZERO)
