"""Модели БД."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import Direction, FxStatus, RecurringKind, TxSource
from app.models.types import Money

__all__ = [
    "Direction",
    "FxStatus",
    "RecurringKind",
    "TxSource",
    "User",
    "Transaction",
    "FxRate",
    "MonthlyIncome",
    "Plan",
    "PlanLine",
    "ImportBatch",
    "FundSource",
    "FundBalance",
    "MonthCheck",
    "RecurringExpense",
]


def _enum(enum_cls) -> SAEnum:
    """Строковый enum, одинаково работающий на Postgres и SQLite."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=16,
        values_callable=lambda cls: [member.value for member in cls],
    )


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_currency: Mapped[str] = mapped_column(String(8), default="USD")
    default_monthly_income: Mapped[Decimal | None] = mapped_column(Money(14, 2), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "zen_created_at", name="uq_tx_user_zen_created"),
        # одно начисление на подписку и месяц: генератор идемпотентен
        UniqueConstraint("recurring_id", "recurring_month", name="uq_tx_recurring_month"),
        Index("ix_tx_user_date", "user_id", "date"),
        Index("ix_tx_user_category", "user_id", "category_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    date: Mapped[dt.date] = mapped_column(Date)
    #: имя категории ровно как в выгрузке, без нормализации
    category_name: Mapped[str] = mapped_column(Text, default="")
    account_name: Mapped[str] = mapped_column(Text, default="")
    payee: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    direction: Mapped[Direction] = mapped_column(_enum(Direction))
    amount_original: Mapped[Decimal] = mapped_column(Money(18, 4))
    currency: Mapped[str] = mapped_column(String(16))

    amount_base: Mapped[Decimal | None] = mapped_column(Money(18, 4), nullable=True)
    fx_rate: Mapped[Decimal | None] = mapped_column(Money(24, 10), nullable=True)
    fx_status: Mapped[FxStatus] = mapped_column(_enum(FxStatus), default=FxStatus.PENDING)

    source: Mapped[TxSource] = mapped_column(_enum(TxSource), default=TxSource.CSV)
    zen_created_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    zen_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    #: начисление подписки: ссылка на регулярную трату и месяц, за который начислено
    recurring_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurring_expenses.id", ondelete="SET NULL"), nullable=True
    )
    recurring_month: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("date", "currency", name="uq_fx_date_currency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(16), index=True)
    #: сколько единиц базовой валюты стоит одна единица `currency`
    rate_to_base: Mapped[Decimal] = mapped_column(Money(24, 10))
    source: Mapped[str] = mapped_column(String(32), default="yahoo")
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class MonthlyIncome(Base):
    __tablename__ = "monthly_income"
    __table_args__ = (UniqueConstraint("user_id", "month", name="uq_income_user_month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: первое число месяца
    month: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money(14, 2))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("user_id", "month", name="uq_plan_user_month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    month: Mapped[dt.date] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    lines: Mapped[list[PlanLine]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="PlanLine.position",
    )


class PlanLine(Base):
    __tablename__ = "plan_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    #: сумма в валюте строки; итоги подводятся в базовой валюте после пересчёта
    amount: Mapped[Decimal] = mapped_column(Money(14, 2))
    currency: Mapped[str] = mapped_column(String(16), default="USD")
    #: категории трат, по которым строка сравнивается с фактом; пусто — связи нет.
    #: Одной строке плана вроде «еда» отвечают несколько категорий выгрузки,
    #: поэтому это список, а не одно имя.
    category_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    position: Mapped[int] = mapped_column(Integer, default=0)

    plan: Mapped[Plan] = relationship(back_populates="lines")


class FundSource(Base):
    """Место, где лежат деньги: счёт, карта, наличные, брокер."""

    __tablename__ = "fund_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(16), default="USD")
    position: Mapped[int] = mapped_column(Integer, default=0)
    #: архивный источник не участвует в итоге, но история остаётся
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class FundBalance(Base):
    """Снимок суммы на источнике.

    Каждое изменение пишется новой строкой, а не правкой прежней: только так
    видна динамика. Текущий баланс — последний снимок по `(date, id)`.
    """

    __tablename__ = "fund_balances"
    __table_args__ = (Index("ix_balance_source_date", "source_id", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("fund_sources.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[dt.date] = mapped_column(Date)
    amount_original: Mapped[Decimal] = mapped_column(Money(18, 4))
    currency: Mapped[str] = mapped_column(String(16))
    amount_base: Mapped[Decimal | None] = mapped_column(Money(18, 4), nullable=True)
    fx_rate: Mapped[Decimal | None] = mapped_column(Money(24, 10), nullable=True)
    fx_status: Mapped[FxStatus] = mapped_column(_enum(FxStatus), default=FxStatus.PENDING)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class MonthCheck(Base):
    """Сверка месяца: реальное движение средств против учтённого."""

    __tablename__ = "month_checks"
    __table_args__ = (UniqueConstraint("user_id", "month", name="uq_check_user_month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    month: Mapped[dt.date] = mapped_column(Date)
    #: изменение суммы всех источников за месяц
    real_saldo: Mapped[Decimal] = mapped_column(Money(14, 2))
    #: сальдо по введённым доходам и тратам
    tracked_saldo: Mapped[Decimal] = mapped_column(Money(14, 2))
    #: погрешность ведения средств: реальное минус учтённое
    discrepancy: Mapped[Decimal] = mapped_column(Money(14, 2))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class RecurringExpense(Base):
    """Подписка или другая постоянная трата вроде аренды.

    Аренда отличается от подписки только полем `kind` и местом в интерфейсе:
    механика начисления у них одна.
    """

    __tablename__ = "recurring_expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[RecurringKind] = mapped_column(
        _enum(RecurringKind), default=RecurringKind.SUBSCRIPTION
    )
    title: Mapped[str] = mapped_column(Text)
    #: сумма одного списания в валюте подписки
    amount: Mapped[Decimal] = mapped_column(Money(14, 2))
    currency: Mapped[str] = mapped_column(String(16), default="USD")
    #: сколько месяцев покрывает одно списание: 1 — месячная, 12 — годовая
    period_months: Mapped[int] = mapped_column(Integer, default=1)
    #: день списания, для справки в интерфейсе
    charge_day: Mapped[int] = mapped_column(Integer, default=1)
    category_name: Mapped[str] = mapped_column(Text, default="Подписки")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: первый месяц начисления; прошлые месяцы не переписываются
    starts_on: Mapped[dt.date] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(Text)
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_new: Mapped[int] = mapped_column(Integer, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    rows_error: Mapped[int] = mapped_column(Integer, default=0)
    skipped_transfers: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_now, server_default=func.now()
    )
