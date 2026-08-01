"""Модели БД."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import Direction, FxStatus, TxSource
from app.models.types import Money

__all__ = [
    "Direction",
    "FxStatus",
    "TxSource",
    "User",
    "Transaction",
    "FxRate",
    "MonthlyIncome",
    "Plan",
    "PlanLine",
    "ImportBatch",
]


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

    direction: Mapped[Direction] = mapped_column(String(16))
    amount_original: Mapped[Decimal] = mapped_column(Money(18, 4))
    currency: Mapped[str] = mapped_column(String(16))

    amount_base: Mapped[Decimal | None] = mapped_column(Money(18, 4), nullable=True)
    fx_rate: Mapped[Decimal | None] = mapped_column(Money(24, 10), nullable=True)
    fx_status: Mapped[FxStatus] = mapped_column(String(16), default=FxStatus.PENDING)

    source: Mapped[TxSource] = mapped_column(String(16), default=TxSource.CSV)
    zen_created_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    zen_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

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
    source: Mapped[str] = mapped_column(String(32), default="fawazahmed0")
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
    amount: Mapped[Decimal] = mapped_column(Money(14, 2))
    position: Mapped[int] = mapped_column(Integer, default=0)

    plan: Mapped[Plan] = relationship(back_populates="lines")


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
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, server_default=func.now())
