"""Схемы запросов и ответов API."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Direction, FxStatus, TxSource


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: dt.date
    category_name: str
    account_name: str
    payee: str | None
    comment: str | None
    direction: Direction
    amount_original: Decimal
    currency: str
    amount_base: Decimal | None
    fx_status: FxStatus
    source: TxSource


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    total: int
    has_more: bool


class TransactionCreate(BaseModel):
    date: dt.date
    category_name: str = ""
    account_name: str = ""
    amount_original: Decimal = Field(gt=0)
    currency: str = "USD"
    direction: Direction = Direction.OUTCOME
    comment: str | None = None
    payee: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        code = (value or "").strip().upper()
        if not code:
            raise ValueError("Не указана валюта")
        return code


class TransactionUpdate(BaseModel):
    date: dt.date | None = None
    category_name: str | None = None
    account_name: str | None = None
    amount_original: Decimal | None = Field(default=None, gt=0)
    currency: str | None = None
    direction: Direction | None = None
    comment: str | None = None
    payee: str | None = None


class CategorySliceOut(BaseModel):
    category: str
    amount: Decimal
    share: Decimal
    delta_pct: Decimal | None
    tx_count: int


class MonthSummaryOut(BaseModel):
    month: str
    income_manual: Decimal
    income_from_transactions: Decimal
    income_total: Decimal
    outcome_total: Decimal
    saldo: Decimal
    spent_share: Decimal | None
    tx_count: int
    pending_count: int
    base_currency: str
    categories: list[CategorySliceOut]
    recent: list[TransactionOut]


class MonthPointOut(BaseModel):
    month: str
    amount: Decimal
    tx_count: int


class CategoryDynamicsOut(BaseModel):
    category: str
    points: list[MonthPointOut]
    average: Decimal
    total: Decimal
    delta_pct: Decimal | None


class CategoryDiffOut(BaseModel):
    category: str
    amount_a: Decimal
    amount_b: Decimal
    diff: Decimal
    diff_pct: Decimal | None


class CompareOut(BaseModel):
    month_a: str
    month_b: str
    total_a: Decimal
    total_b: Decimal
    saldo_a: Decimal
    saldo_b: Decimal
    categories: list[CategoryDiffOut]


class MonthsOut(BaseModel):
    months: list[str]
    current: str


class IncomeOut(BaseModel):
    month: str
    amount: Decimal
    note: str | None = None
    is_default: bool = False


class IncomeIn(BaseModel):
    amount: Decimal = Field(ge=0)
    note: str | None = None
    save_as_default: bool = False


class SettingsOut(BaseModel):
    base_currency: str
    default_monthly_income: Decimal | None
    excluded_categories: list[str]


class SettingsIn(BaseModel):
    default_monthly_income: Decimal | None = Field(default=None, ge=0)


class PlanLineIn(BaseModel):
    title: str = ""
    amount: Decimal = Field(ge=0)


class PlanLineOut(BaseModel):
    id: int
    title: str
    amount: Decimal
    position: int


class PlanOut(BaseModel):
    month: str
    lines: list[PlanLineOut]
    total: Decimal
    income: Decimal
    expected_saldo: Decimal


class PlanIn(BaseModel):
    lines: list[PlanLineIn]


class PlanVsFactOut(BaseModel):
    month: str
    plan_total: Decimal
    fact_total: Decimal
    diff: Decimal
    fact_share_of_plan: Decimal | None
    plan_saldo: Decimal
    fact_saldo: Decimal
    accuracy: Decimal | None
    lines: list[PlanLineOut]
    categories: list[CategorySliceOut]
    has_plan: bool


class ImportReportOut(BaseModel):
    rows_total: int
    rows_new: int
    rows_duplicate: int
    rows_error: int
    skipped_transfers: int
    pending_fx: int
    errors: list[str]


class BackfillOut(BaseModel):
    filled: int
    pending_left: int


class CategoryOut(BaseModel):
    name: str
    tx_count: int


class SuggestionOut(BaseModel):
    """Строка плана, предложенная по средним тратам за прошлые месяцы."""

    title: str
    amount: Decimal
