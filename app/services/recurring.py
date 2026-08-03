"""Подписки и другие постоянные траты.

Одно списание может покрывать несколько месяцев (годовая подписка — двенадцать),
поэтому в траты попадает не сам платёж, а его месячная доля: так месяц с оплатой
годовой подписки не выглядит провальным.

Начисление создаётся последним днём месяца и только за завершившиеся месяцы.
Генератор идемпотентен: пара `(recurring_id, recurring_month)` уникальна, повторный
запуск ничего не добавляет. Поэтому его можно звать откуда угодно — из планировщика
бота, из ручки и просто при чтении сводки месяца.

Правка суммы действует на текущий и будущие месяцы: уже начисленные месяцы
генератор не трогает.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Direction,
    RecurringExpense,
    RecurringKind,
    Transaction,
    TxSource,
    User,
)
from app.services import stats
from app.services.fx import FxService

ZERO = Decimal(0)
_CENTS = Decimal("0.01")

#: сколько месяцев назад генератор готов достроить за один прогон
MAX_BACKFILL_MONTHS = 36

#: категории по умолчанию — чтобы начисления сразу попадали в разбивку
DEFAULT_CATEGORY = {
    RecurringKind.SUBSCRIPTION: "Подписки",
    RecurringKind.RENT: "Аренда",
}

#: подпись счёта у начислений: отличает их в списке операций и в фильтрах
ACCOUNT_LABEL = {
    RecurringKind.SUBSCRIPTION: "Подписки",
    RecurringKind.RENT: "Аренда",
}


def monthly_amount(item: RecurringExpense) -> Decimal:
    """Месячная доля списания в валюте подписки."""
    period = max(1, item.period_months)
    return (Decimal(item.amount) / period).quantize(_CENTS)


def last_closed_month(today: dt.date | None = None) -> dt.date:
    """Последний завершившийся месяц: текущий начисляем только после его конца."""
    today = today or dt.date.today()
    return stats.shift_month(today.replace(day=1), -1)


def due_months(item: RecurringExpense, today: dt.date | None = None) -> list[dt.date]:
    """Месяцы, за которые подписке положено начисление."""
    if not item.active:
        return []

    end = last_closed_month(today)
    start = item.starts_on.replace(day=1)
    floor = stats.shift_month(end, -(MAX_BACKFILL_MONTHS - 1))
    if start < floor:
        start = floor

    months = []
    cursor = start
    while cursor <= end:
        months.append(cursor)
        cursor = stats.shift_month(cursor, 1)
    return months


def list_items(
    db: Session, user: User, *, only_active: bool = False
) -> list[RecurringExpense]:
    query = select(RecurringExpense).where(RecurringExpense.user_id == user.id)
    if only_active:
        query = query.where(RecurringExpense.active.is_(True))
    return list(db.scalars(query.order_by(RecurringExpense.kind, RecurringExpense.id)))


def charged_months(db: Session, item: RecurringExpense) -> set[dt.date]:
    return {
        month
        for (month,) in db.execute(
            select(Transaction.recurring_month).where(Transaction.recurring_id == item.id)
        )
        if month is not None
    }


def run(db: Session, user: User, today: dt.date | None = None) -> int:
    """Создать недостающие начисления. Возвращает число новых операций."""
    items = list_items(db, user, only_active=True)
    if not items:
        return 0

    fx = FxService(db)
    created = 0
    pending: list[Transaction] = []

    for item in items:
        already = charged_months(db, item)
        missing = [month for month in due_months(item, today) if month not in already]
        if not missing:
            continue

        amount = monthly_amount(item)
        if amount <= 0:
            continue

        days = [stats.month_bounds(month)[1] for month in missing]
        fx.ensure_rates((day, item.currency) for day in days)

        for month, day in zip(missing, days, strict=True):
            amount_base, rate, fx_status = fx.convert(amount, item.currency, day)
            pending.append(
                Transaction(
                    user_id=user.id,
                    date=day,
                    category_name=item.category_name or DEFAULT_CATEGORY[item.kind],
                    account_name=ACCOUNT_LABEL[item.kind],
                    payee=item.title,
                    comment=_comment(item),
                    direction=Direction.OUTCOME,
                    amount_original=amount,
                    currency=item.currency,
                    amount_base=amount_base,
                    fx_rate=rate,
                    fx_status=fx_status,
                    source=TxSource.RECURRING,
                    recurring_id=item.id,
                    recurring_month=month,
                )
            )
            created += 1

    if pending:
        db.add_all(pending)
        db.commit()
    return created


def _comment(item: RecurringExpense) -> str:
    """Подпись операции: в списке трат по ней видно, какая это подписка."""
    if item.period_months <= 1:
        return item.title
    return f"{item.title}: доля за месяц от списания раз в {item.period_months} мес."


def monthly_total_base(db: Session, user: User, items: list[RecurringExpense]) -> Decimal:
    """Сколько подписки стоят в месяц в базовой валюте."""
    fx = FxService(db)
    today = dt.date.today()
    fx.ensure_rates((today, item.currency) for item in items if item.active)

    total = ZERO
    for item in items:
        if not item.active:
            continue
        amount_base, _, _ = fx.convert(monthly_amount(item), item.currency, today)
        total += amount_base or ZERO
    return total.quantize(_CENTS)
