"""Схемы запросов и ответов API."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Direction, FxStatus, RecurringKind, TxSource


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


class DynamicsOut(BaseModel):
    """Ряд по месяцам: для всех трат или для одной категории."""

    points: list[MonthPointOut]
    average: Decimal
    total: Decimal
    delta_pct: Decimal | None


class CategoryDynamicsOut(DynamicsOut):
    category: str


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
    #: `saved` — задан на месяц, `carried` — перенесён с прошлого, `default` — из настроек
    source: str = "saved"
    from_month: str | None = None


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
    currency: str = "USD"
    #: категории трат, по которым строка сверяется с фактом
    category_names: list[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return (value or "USD").strip().upper() or "USD"

    @field_validator("category_names")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        """Пустые имена и повторы выкидываем, порядок сохраняем."""
        seen: list[str] = []
        for item in value:
            name = (item or "").strip()
            if name and name not in seen:
                seen.append(name)
        return seen


class PlanLineOut(BaseModel):
    id: int
    title: str
    amount: Decimal
    currency: str
    #: сумма в базовой валюте — по ней подводятся итоги
    amount_base: Decimal
    category_names: list[str]
    position: int


class PlanOut(BaseModel):
    month: str
    lines: list[PlanLineOut]
    total: Decimal
    income: Decimal
    expected_saldo: Decimal
    base_currency: str
    #: `saved` — план сохранён, `previous` — черновик из прошлого месяца, `empty` — пусто
    source: str = "empty"


class PlanIn(BaseModel):
    lines: list[PlanLineIn]


class PlanLineFactOut(PlanLineOut):
    """Строка плана вместе с фактом по связанным категориям."""

    #: сумма трат по всем связанным категориям; `None` — категории не выбраны
    fact: Decimal | None
    diff: Decimal | None


class PlanVsFactOut(BaseModel):
    month: str
    plan_total: Decimal
    fact_total: Decimal
    diff: Decimal
    fact_share_of_plan: Decimal | None
    plan_saldo: Decimal
    fact_saldo: Decimal
    accuracy: Decimal | None
    lines: list[PlanLineFactOut]
    categories: list[CategorySliceOut]
    #: факт по категориям, которых в плане не было
    unplanned: list[CategorySliceOut]
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


# --- средства -------------------------------------------------------------


class FundSourceOut(BaseModel):
    id: int
    title: str
    currency: str
    position: int
    archived: bool
    #: последняя записанная сумма в валюте источника
    amount_original: Decimal
    amount_base: Decimal | None
    updated_on: dt.date | None


class BalancePointOut(BaseModel):
    month: str
    amount: Decimal


class FundsOut(BaseModel):
    base_currency: str
    total_base: Decimal
    sources: list[FundSourceOut]
    #: итог на конец каждого месяца окна
    history: list[BalancePointOut]
    #: месяц, который пора сверить, если такой есть
    pending_check: str | None
    #: источники, для которых не нашлось курса: их сумма в долларах неизвестна
    pending_fx: int = 0


class FundSourceIn(BaseModel):
    title: str = Field(min_length=1)
    currency: str = "USD"
    #: сумма, с которой источник заводится
    amount: Decimal = Field(default=Decimal(0), ge=0)

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        code = (value or "").strip().upper()
        if not code:
            raise ValueError("Не указана валюта")
        return code


class FundSourcePatch(BaseModel):
    title: str | None = None
    currency: str | None = None
    archived: bool | None = None
    position: int | None = None


class BalanceIn(BaseModel):
    amount: Decimal = Field(ge=0)
    date: dt.date | None = None
    note: str | None = None


class FundBalanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: dt.date
    amount_original: Decimal
    currency: str
    amount_base: Decimal | None
    note: str | None


class MonthCheckOut(BaseModel):
    month: str
    #: изменение суммы всех источников за месяц
    real_saldo: Decimal
    #: сальдо по введённым доходам и тратам
    tracked_saldo: Decimal
    #: погрешность ведения: реальное минус учтённое
    discrepancy: Decimal
    #: итог по источникам на начало и конец месяца; у сохранённых сверок не хранится
    opening: Decimal | None = None
    closing: Decimal | None = None
    is_saved: bool
    #: есть ли остаток на начало месяца; без него сверять не с чем
    comparable: bool = True
    note: str | None = None


class MonthCheckIn(BaseModel):
    note: str | None = None


class BalancePatch(BaseModel):
    amount: Decimal | None = Field(default=None, ge=0)
    date: dt.date | None = None
    note: str | None = None


# --- подписки и постоянные траты ------------------------------------------


class RecurringOut(BaseModel):
    id: int
    kind: RecurringKind
    title: str
    amount: Decimal
    currency: str
    period_months: int
    #: дата списания, как её ввёл пользователь
    charge_on: dt.date
    #: ближайшее списание не раньше сегодняшнего дня
    next_charge: dt.date
    category_name: str
    active: bool
    starts_on: dt.date
    #: доля списания, попадающая в траты каждого месяца
    monthly_amount: Decimal
    monthly_amount_base: Decimal | None


class RecurringListOut(BaseModel):
    items: list[RecurringOut]
    base_currency: str
    monthly_total_base: Decimal
    #: сколько начислений создано при этом запросе
    generated: int


class RecurringIn(BaseModel):
    kind: RecurringKind = RecurringKind.SUBSCRIPTION
    title: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    currency: str = "USD"
    period_months: int = Field(default=1, ge=1, le=12)
    #: дата списания; по умолчанию — сегодня
    charge_on: dt.date | None = None
    category_name: str | None = None
    starts_on: dt.date | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        code = (value or "").strip().upper()
        if not code:
            raise ValueError("Не указана валюта")
        return code


class RecurringPatch(BaseModel):
    title: str | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    currency: str | None = None
    period_months: int | None = Field(default=None, ge=1, le=12)
    charge_on: dt.date | None = None
    category_name: str | None = None
    active: bool | None = None
    starts_on: dt.date | None = None


class RecurringRunOut(BaseModel):
    generated: int
