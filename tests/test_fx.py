"""Тесты сервиса курсов валют."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Direction, FxRate, FxStatus, Transaction, TxSource
from app.services.fx import FxService

D = dt.date


class FakeProvider:
    """Провайдер без сети: отдаёт заранее заданные ряды и считает вызовы."""

    def __init__(self, series: dict[str, dict[dt.date, str]], *, fail: bool = False):
        self.series = series
        self.fail = fail
        self.calls: list[tuple[str, dt.date, dt.date]] = []

    def fetch_series(self, currency, start, end):
        self.calls.append((currency, start, end))
        if self.fail:
            raise ConnectionError("сеть недоступна")
        data = self.series.get(currency, {})
        return {
            day: Decimal(value)
            for day, value in data.items()
            if start <= day <= end
        }


def service(db: Session, provider) -> FxService:
    return FxService(db, provider=provider)


def test_usd_rate_is_one_without_network(db: Session):
    provider = FakeProvider({})
    fx = service(db, provider)

    amount, rate, status = fx.convert(Decimal("10"), "USD", D(2023, 5, 1))

    assert amount == Decimal("10")
    assert rate == Decimal("1")
    assert status is FxStatus.OK
    assert provider.calls == []


def test_exact_date_match(db: Session):
    provider = FakeProvider({"RSD": {D(2026, 7, 1): "0.0095"}})
    fx = service(db, provider)
    fx.ensure_rates([(D(2026, 7, 1), "RSD")])

    amount, rate, status = fx.convert(Decimal("1000"), "RSD", D(2026, 7, 1))

    assert rate == Decimal("0.0095")
    assert amount == Decimal("9.5000")
    assert status is FxStatus.OK


def test_carries_forward_last_close_over_weekend(db: Session):
    # 4 июля 2026 — суббота, торгов нет, берётся закрытие пятницы
    provider = FakeProvider({"RSD": {D(2026, 7, 3): "0.0095"}})
    fx = service(db, provider)
    fx.ensure_rates([(D(2026, 7, 4), "RSD")])

    _, rate, status = fx.convert(Decimal("100"), "RSD", D(2026, 7, 4))

    assert rate == Decimal("0.0095")
    assert status is FxStatus.OK


def test_gap_beyond_carry_forward_is_marked_approx(db: Session):
    # шесть дней без котировок: за пределами переноса закрытия, но в окне поиска
    provider = FakeProvider({"RSD": {D(2026, 6, 14): "0.0095"}})
    fx = service(db, provider)
    fx.ensure_rates([(D(2026, 6, 20), "RSD")])

    _, rate, status = fx.convert(Decimal("100"), "RSD", D(2026, 6, 20))

    assert rate == Decimal("0.0095")
    assert status is FxStatus.APPROX


def test_missing_rate_beyond_window_is_pending(db: Session):
    provider = FakeProvider({})
    fx = service(db, provider)
    fx.ensure_rates([(D(2026, 6, 20), "RSD")])

    amount, rate, status = fx.convert(Decimal("100"), "RSD", D(2026, 6, 20))

    assert amount is None
    assert rate is None
    assert status is FxStatus.PENDING


def test_network_failure_does_not_raise(db: Session):
    provider = FakeProvider({}, fail=True)
    fx = service(db, provider)

    fx.ensure_rates([(D(2026, 6, 20), "RSD")])

    _, _, status = fx.convert(Decimal("100"), "RSD", D(2026, 6, 20))
    assert status is FxStatus.PENDING


def test_pegged_currency_derived_from_anchor(db: Session):
    # BAM жёстко привязана к евро: 1 EUR = 1.95583 BAM
    provider = FakeProvider({"EUR": {D(2026, 7, 1): "1.10"}})
    fx = service(db, provider)
    fx.ensure_rates([(D(2026, 7, 1), "BAM")])

    _, rate, status = fx.convert(Decimal("100"), "BAM", D(2026, 7, 1))

    assert rate is not None
    assert abs(rate - Decimal("1.10") / Decimal("1.95583")) < Decimal("0.0000001")
    assert status is FxStatus.APPROX


def test_usdt_falls_back_to_one(db: Session):
    provider = FakeProvider({})
    fx = service(db, provider)
    fx.ensure_rates([(D(2023, 4, 1), "USDT")])

    amount, rate, status = fx.convert(Decimal("10"), "USDT", D(2023, 4, 1))

    assert rate == Decimal("1")
    assert amount == Decimal("10")
    assert status is FxStatus.APPROX


def test_ensure_rates_fetches_each_currency_once_for_whole_range(db: Session):
    days = {D(2026, 7, day) for day in range(1, 20)}
    provider = FakeProvider({"RSD": {day: "0.0095" for day in days}})
    fx = service(db, provider)

    fx.ensure_rates([(day, "RSD") for day in days])

    assert len(provider.calls) == 1
    currency, start, end = provider.calls[0]
    assert currency == "RSD"
    assert start <= D(2026, 7, 1)
    assert end >= D(2026, 7, 19)


def test_cached_rates_are_not_refetched(db: Session):
    provider = FakeProvider({"RSD": {D(2026, 7, 1): "0.0095"}})
    fx = service(db, provider)
    fx.ensure_rates([(D(2026, 7, 1), "RSD")])
    fx.ensure_rates([(D(2026, 7, 1), "RSD")])

    assert len(provider.calls) == 1
    assert db.query(FxRate).count() == 1


def test_backfill_fills_pending_transactions(db: Session, user):
    tx = Transaction(
        user_id=user.id,
        date=D(2026, 7, 1),
        category_name="Кофе",
        account_name="Сербия",
        direction=Direction.OUTCOME,
        amount_original=Decimal("1000"),
        currency="RSD",
        amount_base=None,
        fx_status=FxStatus.PENDING,
        source=TxSource.CSV,
        zen_created_at=dt.datetime(2026, 7, 1, 10, 0, 0),
    )
    db.add(tx)
    db.commit()

    provider = FakeProvider({"RSD": {D(2026, 7, 1): "0.0095"}})
    filled = service(db, provider).backfill()

    db.refresh(tx)
    assert filled == 1
    assert tx.amount_base == Decimal("9.5000")
    assert tx.fx_status is FxStatus.OK
