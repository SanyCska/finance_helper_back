"""Текущие средства: источники, снимки балансов и месячная сверка.

Каждое изменение суммы пишется отдельным снимком (`FundBalance`), поэтому по
источнику видна динамика, а итог на любую дату — это сумма последних снимков
на эту дату, пересчитанных в базовую валюту.

Сверка месяца сравнивает два числа:

- *реальное сальдо* — насколько изменился итог по всем источникам за месяц;
- *учтённое сальдо* — доход минус траты по введённым данным.

Их разница и есть погрешность ведения: забытые траты, комиссии, курсовые
переоценки. Подтверждённая сверка фиксируется в `MonthCheck`, чтобы более
поздние правки балансов не переписывали уже сведённый месяц.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FundBalance, FundSource, MonthCheck, User
from app.services import stats
from app.services.fx import FxService

ZERO = Decimal(0)
_CENTS = Decimal("0.01")


@dataclass
class SourceState:
    source: FundSource
    balance: FundBalance | None

    @property
    def amount_original(self) -> Decimal:
        return self.balance.amount_original if self.balance else ZERO

    @property
    def amount_base(self) -> Decimal | None:
        return self.balance.amount_base if self.balance else ZERO

    @property
    def updated_on(self) -> dt.date | None:
        return self.balance.date if self.balance else None


@dataclass
class MonthCheckResult:
    """Расчёт сверки месяца. `saved` — уже подтверждённая запись, если есть."""

    month: dt.date
    real_saldo: Decimal
    tracked_saldo: Decimal
    discrepancy: Decimal
    opening: Decimal
    closing: Decimal
    saved: MonthCheck | None


# --- источники ------------------------------------------------------------


def list_sources(db: Session, user: User, *, include_archived: bool = False) -> list[FundSource]:
    query = select(FundSource).where(FundSource.user_id == user.id)
    if not include_archived:
        query = query.where(FundSource.archived.is_(False))
    return list(db.scalars(query.order_by(FundSource.position, FundSource.id)))


def next_position(db: Session, user: User) -> int:
    sources = list_sources(db, user, include_archived=True)
    return max((item.position for item in sources), default=-1) + 1


def latest_balance(db: Session, source_id: int, on: dt.date | None = None) -> FundBalance | None:
    """Последний снимок источника на дату (или вообще последний)."""
    query = select(FundBalance).where(FundBalance.source_id == source_id)
    if on is not None:
        query = query.where(FundBalance.date <= on)
    return db.scalar(query.order_by(FundBalance.date.desc(), FundBalance.id.desc()).limit(1))


def source_states(
    db: Session, user: User, on: dt.date | None = None, *, include_archived: bool = False
) -> list[SourceState]:
    return [
        SourceState(source=source, balance=latest_balance(db, source.id, on))
        for source in list_sources(db, user, include_archived=include_archived)
    ]


def set_balance(
    db: Session,
    user: User,
    source: FundSource,
    amount: Decimal,
    day: dt.date,
    note: str | None = None,
) -> FundBalance:
    """Записать новую сумму источника отдельным снимком."""
    fx = FxService(db)
    fx.ensure_rates([(day, source.currency)])
    amount_base, rate, fx_status = fx.convert(amount, source.currency, day)

    snapshot = FundBalance(
        user_id=user.id,
        source_id=source.id,
        date=day,
        amount_original=amount,
        currency=source.currency,
        amount_base=amount_base,
        fx_rate=rate,
        fx_status=fx_status,
        note=note,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def history(db: Session, source_id: int, limit: int = 60) -> list[FundBalance]:
    return list(
        db.scalars(
            select(FundBalance)
            .where(FundBalance.source_id == source_id)
            .order_by(FundBalance.date.desc(), FundBalance.id.desc())
            .limit(limit)
        )
    )


# --- итоги ----------------------------------------------------------------


def total_base(db: Session, user: User, on: dt.date | None = None) -> Decimal:
    """Итог по всем источникам в базовой валюте на дату.

    Архивные источники учитываются, если на дату у них ещё был баланс: иначе
    архивация счёта выглядела бы как исчезновение денег.
    """
    total = ZERO
    for state in source_states(db, user, on, include_archived=True):
        if state.balance is None:
            continue
        total += state.balance.amount_base or ZERO
    return total.quantize(_CENTS)


@dataclass
class BalancePoint:
    month: dt.date
    amount: Decimal


def balance_history(db: Session, user: User, months: list[dt.date]) -> list[BalancePoint]:
    """Итог на конец каждого месяца окна."""
    points = []
    for month in sorted(months):
        _, last = stats.month_bounds(month)
        points.append(BalancePoint(month=month, amount=total_base(db, user, last)))
    return points


# --- сверка месяца --------------------------------------------------------


def tracked_saldo(db: Session, user: User, month: dt.date) -> Decimal:
    """Сальдо месяца по введённым данным: доход минус траты."""
    from app.services import budget  # локальный импорт: budget зависит от stats, не от funds

    transactions = stats.fetch_month(db, user.id, month)
    income, _, _ = budget.get_income(db, user, month)
    return stats.month_summary(transactions, income).saldo.quantize(_CENTS)


def get_check(db: Session, user: User, month: dt.date) -> MonthCheck | None:
    return db.scalar(
        select(MonthCheck).where(MonthCheck.user_id == user.id, MonthCheck.month == month)
    )


def month_check(db: Session, user: User, month: dt.date) -> MonthCheckResult:
    """Расчёт сверки: реальное движение средств против учтённого."""
    saved = get_check(db, user, month)
    tracked = tracked_saldo(db, user, month)

    first, last = stats.month_bounds(month)
    opening = total_base(db, user, first - dt.timedelta(days=1))
    closing = total_base(db, user, last)
    real = (closing - opening).quantize(_CENTS)

    if saved is not None:
        # подтверждённая сверка не пересчитывается: балансы могли уйти вперёд
        return MonthCheckResult(
            month=month,
            real_saldo=saved.real_saldo,
            tracked_saldo=saved.tracked_saldo,
            discrepancy=saved.discrepancy,
            opening=opening,
            closing=closing,
            saved=saved,
        )

    return MonthCheckResult(
        month=month,
        real_saldo=real,
        tracked_saldo=tracked,
        discrepancy=(real - tracked).quantize(_CENTS),
        opening=opening,
        closing=closing,
        saved=None,
    )


def save_check(db: Session, user: User, month: dt.date, note: str | None = None) -> MonthCheck:
    """Зафиксировать сверку месяца по текущим балансам."""
    tracked = tracked_saldo(db, user, month)
    first, last = stats.month_bounds(month)
    opening = total_base(db, user, first - dt.timedelta(days=1))
    closing = total_base(db, user, last)
    real = (closing - opening).quantize(_CENTS)

    record = get_check(db, user, month)
    if record is None:
        record = MonthCheck(user_id=user.id, month=month)
        db.add(record)

    record.real_saldo = real
    record.tracked_saldo = tracked
    record.discrepancy = (real - tracked).quantize(_CENTS)
    record.note = note
    db.commit()
    db.refresh(record)
    return record


def list_checks(db: Session, user: User, limit: int = 24) -> list[MonthCheck]:
    return list(
        db.scalars(
            select(MonthCheck)
            .where(MonthCheck.user_id == user.id)
            .order_by(MonthCheck.month.desc())
            .limit(limit)
        )
    )


def pending_check_month(db: Session, user: User, today: dt.date | None = None) -> dt.date | None:
    """Месяц, который пора сверить: прошлый, если сверки по нему ещё нет.

    Сверять есть что только когда балансы вообще заводились — иначе разница
    была бы равна минус учтённому сальдо и пугала бы без причины.
    """
    today = today or dt.date.today()
    previous = stats.shift_month(today.replace(day=1), -1)
    if get_check(db, user, previous) is not None:
        return None
    if not list_sources(db, user):
        return None
    return previous
