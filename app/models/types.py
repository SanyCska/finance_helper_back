"""Портируемый денежный тип.

Postgres хранит `numeric` нативно. SQLite числового типа с произвольной точностью не имеет
и через `Numeric` возвращает float, теряя копейки, поэтому там значение хранится строкой.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Numeric, String, TypeDecorator


class Money(TypeDecorator):
    """Decimal, одинаково точный на Postgres и SQLite."""

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = 18, scale: int = 4) -> None:
        self.precision = precision
        self.scale = scale
        super().__init__(precision=precision, scale=scale, asdecimal=True)

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        value = Decimal(str(value))
        if dialect.name == "sqlite":
            return format(value, "f")
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(str(value))
