"""Перечисления доменной модели."""

from __future__ import annotations

from enum import StrEnum


class Direction(StrEnum):
    OUTCOME = "outcome"
    INCOME = "income"


class TxSource(StrEnum):
    CSV = "csv"
    MANUAL = "manual"
    #: начисление подписки или аренды последним днём месяца
    RECURRING = "recurring"


class RecurringKind(StrEnum):
    SUBSCRIPTION = "subscription"
    #: аренда квартиры — та же механика, но отдельное место в интерфейсе
    RENT = "rent"


class FxStatus(StrEnum):
    #: курс найден на дату операции
    OK = "ok"
    #: курс взят с ближайшей доступной даты
    APPROX = "approx"
    #: курс ещё не загружен, сумма в базовой валюте неизвестна
    PENDING = "pending"
