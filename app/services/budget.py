"""Доход месяца и планы трат."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MonthlyIncome, Plan, PlanLine, User
from app.services import stats
from app.services.fx import FxService

ZERO = Decimal(0)
_CENTS = Decimal("0.01")


@dataclass
class IncomeState:
    amount: Decimal
    note: str | None
    #: `saved` — задан на этот месяц, `carried` — перенесён с прошлого, `default` — из настроек
    source: str
    #: месяц, из которого перенесено значение
    from_month: dt.date | None = None

    @property
    def is_default(self) -> bool:
        return self.source != "saved"


def income_state(db: Session, user: User, month: dt.date) -> IncomeState:
    """Зарплата месяца.

    Своя запись месяца важнее всего. Если её нет, берётся последняя запись
    прошлых месяцев: введённая однажды зарплата продолжает действовать вперёд,
    пока пользователь не введёт новую. Правка месяца автоматически
    распространяется на будущие месяцы без собственной записи.
    """
    record = db.scalar(
        select(MonthlyIncome).where(
            MonthlyIncome.user_id == user.id,
            MonthlyIncome.month == month,
        )
    )
    if record is not None:
        return IncomeState(record.amount, record.note, "saved", month)

    previous = db.scalar(
        select(MonthlyIncome)
        .where(MonthlyIncome.user_id == user.id, MonthlyIncome.month < month)
        .order_by(MonthlyIncome.month.desc())
        .limit(1)
    )
    if previous is not None:
        return IncomeState(previous.amount, previous.note, "carried", previous.month)

    return IncomeState(user.default_monthly_income or ZERO, None, "default")


def get_income(db: Session, user: User, month: dt.date) -> tuple[Decimal, str | None, bool]:
    """Совместимая обёртка: сумма, заметка, признак «не задано на этот месяц»."""
    state = income_state(db, user, month)
    return state.amount, state.note, state.is_default


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


# --- планы ----------------------------------------------------------------


def get_plan(db: Session, user: User, month: dt.date) -> Plan | None:
    return db.scalar(select(Plan).where(Plan.user_id == user.id, Plan.month == month))


@dataclass
class DraftLine:
    """Строка плана до сохранения: и черновик из прошлого месяца, и вход ручки."""

    title: str
    amount: Decimal
    currency: str = "USD"
    #: категории трат, по которым строка сверяется с фактом
    category_names: list[str] = field(default_factory=list)


def save_plan(db: Session, user: User, month: dt.date, lines: list[DraftLine]) -> Plan:
    plan = get_plan(db, user, month)
    if plan is None:
        plan = Plan(user_id=user.id, month=month)
        db.add(plan)
        db.flush()

    plan.lines.clear()
    db.flush()
    for position, line in enumerate(lines):
        plan.lines.append(
            PlanLine(
                title=line.title,
                amount=line.amount,
                currency=(line.currency or "USD").upper(),
                category_names=list(line.category_names),
                position=position,
            )
        )

    db.commit()
    db.refresh(plan)
    return plan


def plan_draft(db: Session, user: User, month: dt.date) -> tuple[list[DraftLine], str]:
    """Строки плана и то, откуда они взялись.

    Пустой месяц наследует план прошлого: заново набирать те же десять строк
    каждый месяц незачем, а суммы всё равно правятся на месте.
    """
    plan = get_plan(db, user, month)
    if plan is not None and plan.lines:
        return [
            DraftLine(line.title, line.amount, line.currency, list(line.category_names or []))
            for line in plan.lines
        ], "saved"

    previous = get_plan(db, user, stats.shift_month(month, -1))
    if previous is not None and previous.lines:
        return [
            DraftLine(line.title, line.amount, line.currency, list(line.category_names or []))
            for line in previous.lines
        ], "previous"

    return [], "empty"


# --- пересчёт строк плана в базовую валюту --------------------------------


def plan_rate_day(month: dt.date, today: dt.date | None = None) -> dt.date:
    """Дата курса для плана: конец месяца плана, но не позже сегодняшнего дня.

    План на будущее считается по текущему курсу, прошлый месяц — по своему.
    """
    today = today or dt.date.today()
    _, last = stats.month_bounds(month)
    return min(last, today)


def to_base(
    db: Session, amounts: list[tuple[Decimal, str]], month: dt.date
) -> list[Decimal | None]:
    """Пересчитать суммы строк плана в базовую валюту."""
    if not amounts:
        return []
    day = plan_rate_day(month)
    fx = FxService(db)
    fx.ensure_rates((day, currency) for _, currency in amounts)
    result = []
    for amount, currency in amounts:
        converted, _, _ = fx.convert(amount, currency, day)
        result.append(converted.quantize(_CENTS) if converted is not None else None)
    return result


def lines_in_base(db: Session, lines: list[DraftLine], month: dt.date) -> list[Decimal]:
    """Суммы строк в базовой валюте; строка без курса считается нулём."""
    converted = to_base(db, [(line.amount, line.currency) for line in lines], month)
    return [value if value is not None else ZERO for value in converted]


def plan_total(plan: Plan | None) -> Decimal:
    """Сумма строк без пересчёта — только для планов целиком в базовой валюте."""
    if plan is None:
        return ZERO
    return sum((line.amount for line in plan.lines), ZERO)
