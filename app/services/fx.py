"""Курсы валют и пересчёт сумм в базовую валюту.

Курсы тянутся рядами по каждой валюте (один запрос на валюту за весь диапазон дат),
кэшируются в таблице `fx_rates` и больше не запрашиваются.

Статусы пересчёта:
- `ok` — курс на саму дату либо ближайшее предыдущее закрытие в пределах `CARRY_FORWARD_DAYS`
  (стандартный перенос последнего закрытия на выходные и праздники);
- `approx` — курс пришлось искать дальше по времени или он выведен из привязки к другой валюте;
- `pending` — курса нет, сумма в базовой валюте неизвестна.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable
from decimal import Decimal
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import FxRate, FxStatus, Transaction

logger = logging.getLogger(__name__)

#: перенос последнего закрытия вперёд считается нормой (выходные, праздники)
CARRY_FORWARD_DAYS = 4

#: валюты с фиксированной привязкой: курс выводится из якорной валюты
PEGS: dict[str, tuple[str, Decimal]] = {
    # валютный комитет Боснии: 1 EUR = 1.95583 BAM
    "BAM": ("EUR", Decimal(1) / Decimal("1.95583")),
    # стейблкоин, привязан к доллару
    "USDT": ("USD", Decimal(1)),
}

_QUANT = Decimal("0.0001")


class FxProvider(Protocol):
    def fetch_series(self, currency: str, start: dt.date, end: dt.date) -> dict[dt.date, Decimal]:
        """Курсы `currency → USD` по датам. Может бросить исключение при сбое сети."""


class YahooFxProvider:
    """Дневные курсы из графиков Yahoo Finance.

    Один запрос отдаёт весь диапазон по паре `<CUR>USD=X`. История доступна
    существенно глубже, чем у бесплатных курсовых API без ключа.
    """

    BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

    def __init__(self, timeout: float = 20.0) -> None:
        self._timeout = timeout

    def fetch_series(self, currency: str, start: dt.date, end: dt.date) -> dict[dt.date, Decimal]:
        symbol = f"{currency.upper()}USD=X"
        params = {
            "period1": int(dt.datetime.combine(start, dt.time.min).timestamp()),
            # запас в сутки, чтобы последний день попал в диапазон
            "period2": int(dt.datetime.combine(end + dt.timedelta(days=2), dt.time.min).timestamp()),
            "interval": "1d",
        }
        headers = {"User-Agent": "Mozilla/5.0 (compatible; finance-helper/1.0)"}
        with httpx.Client(timeout=self._timeout, headers=headers) as client:
            response = client.get(f"{self.BASE}/{symbol}", params=params)
            response.raise_for_status()
            payload = response.json()

        results = (payload.get("chart") or {}).get("result") or []
        if not results:
            return {}
        result = results[0]
        timestamps = result.get("timestamp") or []
        quotes = (result.get("indicators") or {}).get("quote") or [{}]
        closes = quotes[0].get("close") or []

        series: dict[dt.date, Decimal] = {}
        for timestamp, close in zip(timestamps, closes, strict=False):
            if close is None:
                continue
            day = dt.datetime.fromtimestamp(timestamp, dt.UTC).date()
            series[day] = Decimal(str(close))
        return series


class FxService:
    def __init__(
        self,
        db: Session,
        provider: FxProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.provider = provider or YahooFxProvider(timeout=self.settings.fx_http_timeout_s)
        self.base_currency = self.settings.base_currency.upper()
        self._cache: dict[str, dict[dt.date, Decimal]] = {}

    # --- загрузка курсов -------------------------------------------------

    def ensure_rates(self, pairs: Iterable[tuple[dt.date, str]]) -> None:
        """Догрузить недостающие курсы для пар «дата, валюта»."""
        wanted: dict[str, set[dt.date]] = {}
        for day, currency in pairs:
            code = currency.upper()
            if code == self.base_currency:
                continue
            wanted.setdefault(code, set()).add(day)

        # валюты с привязкой требуют якорную валюту
        for code in list(wanted):
            if code in PEGS:
                anchor, _ = PEGS[code]
                if anchor != self.base_currency:
                    wanted.setdefault(anchor, set()).update(wanted[code])

        for currency, days in wanted.items():
            self._ensure_currency(currency, days)

    def _ensure_currency(self, currency: str, days: set[dt.date]) -> None:
        window = self.settings.fx_lookback_days
        start = min(days) - dt.timedelta(days=window + CARRY_FORWARD_DAYS)
        end = max(days) + dt.timedelta(days=window)

        known = self._known_days(currency, start, end)
        # если на каждую нужную дату уже есть курс в пределах окна переноса — не ходим в сеть
        if known and all(self._nearest_known(known, day)[0] is not None for day in days):
            return

        try:
            series = self.provider.fetch_series(currency, start, end)
        except Exception as exc:  # сеть, таймаут, некорректный ответ
            logger.warning("Не удалось загрузить курсы %s: %s", currency, exc)
            return

        if not series:
            logger.warning("Провайдер не вернул курсов для %s", currency)
            return

        existing = {
            day
            for (day,) in self.db.execute(
                select(FxRate.date).where(
                    FxRate.currency == currency,
                    FxRate.date >= start,
                    FxRate.date <= end,
                )
            )
        }
        new_rates = [
            FxRate(date=day, currency=currency, rate_to_base=rate, source="yahoo")
            for day, rate in sorted(series.items())
            if day not in existing and rate > 0
        ]
        if new_rates:
            self.db.add_all(new_rates)
            self.db.flush()
        self._cache.pop(currency, None)

    def _known_days(self, currency: str, start: dt.date, end: dt.date) -> dict[dt.date, Decimal]:
        cached = self._cache.get(currency)
        if cached is None:
            cached = {
                day: rate
                for day, rate in self.db.execute(
                    select(FxRate.date, FxRate.rate_to_base).where(FxRate.currency == currency)
                )
            }
            self._cache[currency] = cached
        return {day: rate for day, rate in cached.items() if start <= day <= end}

    # --- пересчёт --------------------------------------------------------

    @staticmethod
    def _nearest_known(
        known: dict[dt.date, Decimal], day: dt.date
    ) -> tuple[Decimal | None, FxStatus]:
        """Курс на дату: точный, перенос последнего закрытия или ближайший в окне."""
        if day in known:
            return known[day], FxStatus.OK

        for back in range(1, CARRY_FORWARD_DAYS + 1):
            candidate = day - dt.timedelta(days=back)
            if candidate in known:
                return known[candidate], FxStatus.OK
        return None, FxStatus.PENDING

    def _lookup(self, currency: str, day: dt.date) -> tuple[Decimal | None, FxStatus]:
        window = self.settings.fx_lookback_days
        known = self._known_days(
            currency,
            day - dt.timedelta(days=window + 400),
            day + dt.timedelta(days=window),
        )
        rate, status = self._nearest_known(known, day)
        if rate is not None:
            return rate, status

        # расширенный поиск: дальше назад, затем вперёд — это уже приближение
        for offset in range(CARRY_FORWARD_DAYS + 1, window + 1):
            for candidate in (day - dt.timedelta(days=offset), day + dt.timedelta(days=offset)):
                if candidate in known:
                    return known[candidate], FxStatus.APPROX
        return None, FxStatus.PENDING

    def rate_for(self, currency: str, day: dt.date) -> tuple[Decimal | None, FxStatus]:
        code = (currency or "").upper()
        if not code:
            return None, FxStatus.PENDING
        if code == self.base_currency:
            return Decimal(1), FxStatus.OK

        rate, status = self._lookup(code, day)
        if rate is not None:
            return rate, status

        peg = PEGS.get(code)
        if peg is not None:
            anchor, factor = peg
            if anchor == self.base_currency:
                return factor, FxStatus.APPROX
            anchor_rate, _ = self._lookup(anchor, day)
            if anchor_rate is not None:
                return anchor_rate * factor, FxStatus.APPROX
        return None, FxStatus.PENDING

    def convert(
        self, amount: Decimal, currency: str, day: dt.date
    ) -> tuple[Decimal | None, Decimal | None, FxStatus]:
        rate, status = self.rate_for(currency, day)
        if rate is None:
            return None, None, FxStatus.PENDING
        return (Decimal(amount) * rate).quantize(_QUANT), rate, status

    # --- досчёт незаполненного -------------------------------------------

    def backfill(self, user_id: int | None = None) -> int:
        """Догрузить курсы и пересчитать транзакции без суммы в базовой валюте."""
        query = select(Transaction).where(Transaction.amount_base.is_(None))
        if user_id is not None:
            query = query.where(Transaction.user_id == user_id)
        pending = list(self.db.scalars(query))
        if not pending:
            return 0

        self.ensure_rates((tx.date, tx.currency) for tx in pending)

        filled = 0
        for tx in pending:
            amount, rate, status = self.convert(tx.amount_original, tx.currency, tx.date)
            if amount is None:
                continue
            tx.amount_base = amount
            tx.fx_rate = rate
            tx.fx_status = status
            filled += 1
        self.db.commit()
        return filled
