"""Перенос остатков, введённых в первых числах, на конец закрываемого месяца.

Суммы по счетам смотрят 1-2 числа, но описывают ими состояние на конец
прошлого месяца. Снимок уходит датой ввода и попадает в новый месяц:
закрытый остаётся сведён по устаревшим суммам, а новый показывает движение,
которого не было. Скрипт переставляет такие снимки на последний день
закрываемого месяца.

Дату меняем через `funds.update_balance`, а не запросом в базу: сумма в
базовой валюте посчитана по курсу на день ввода, и на новой дате её надо
пересчитать, иначе итог поедет на величину дневного движения курса.

    python -m scripts.close_month_balances 2026-08          # что будет сделано
    python -m scripts.close_month_balances 2026-08 --apply  # сделать

На проде скрипта в образе может ещё не быть — его скармливают контейнеру
по stdin, тогда деплой не нужен:

    ssh root@147.45.238.246 \\
      'docker compose -f /opt/finance/docker-compose.yml exec -T api python - 2026-08' \\
      < scripts/close_month_balances.py
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
from decimal import Decimal

from sqlalchemy import select

from app.db import SessionLocal
from app.models import FundBalance, FundSource, User
from app.services import funds

#: столько первых дней нового месяца введённая сумма ещё читается как
#: остаток прошлого — тот же срок, что предлагает веб
DEFAULT_DAYS = 7


def month_start(value: str) -> dt.date:
    return dt.datetime.strptime(f"{value}-01", "%Y-%m-%d").date()


def last_day(month: dt.date) -> dt.date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def _money(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("month", type=month_start, help="закрываемый месяц, ГГГГ-ММ")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help="сколько первых дней следующего месяца считать остатком закрываемого",
    )
    parser.add_argument(
        "--apply", action="store_true", help="без него только показывает, что изменится"
    )
    args = parser.parse_args(argv)

    closing = last_day(args.month)
    first = closing + dt.timedelta(days=1)
    until = first + dt.timedelta(days=args.days - 1)

    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(FundBalance)
                .where(FundBalance.date >= first, FundBalance.date <= until)
                .order_by(FundBalance.date, FundBalance.id)
            )
        )
        if not rows:
            print(f"Снимков с {first} по {until} нет — переносить нечего.")
            return 0

        print(f"Снимки с {first} по {until} → {closing}:")
        for row in rows:
            source = db.get(FundSource, row.source_id)
            title = source.title if source else f"источник {row.source_id}"
            print(
                f"  #{row.id} {title}: {row.date} "
                f"{row.amount_original:.2f} {row.currency} → {_money(row.amount_base)} по курсу "
                f"{_money(row.fx_rate)}"
            )
            # снимок уже на этой дате означает, что остаток вводили дважды:
            # победит перенесённый (он старше по id), прежний останется в истории
            existing = db.scalar(
                select(FundBalance.id)
                .where(FundBalance.source_id == row.source_id, FundBalance.date == closing)
                .limit(1)
            )
            if existing is not None:
                print(f"      на {closing} уже есть снимок #{existing} — он уйдёт в тень")

        if not args.apply:
            print("\nЭто прогон вхолостую. Повтори с --apply, чтобы записать.")
            return 0

        print("\nПереношу:")
        for row in rows:
            funds.update_balance(db, row, day=closing)
            print(
                f"  #{row.id} → {row.date}, {_money(row.amount_base)} по курсу "
                f"{_money(row.fx_rate)}"
            )

        print("\nКак теперь считается сверка:")
        for user in db.scalars(select(User).order_by(User.id)):
            if not funds.list_sources(db, user):
                continue
            for month in (args.month, first.replace(day=1)):
                result = funds.month_check(db, user, month)
                frozen = " (уже зафиксирована, цифры в истории не изменились)"
                saved = frozen if result.saved else ""
                print(
                    f"  {user.username or user.telegram_id} {month:%Y-%m}: "
                    f"по счетам {result.real_saldo:+.2f}, по учёту {result.tracked_saldo:+.2f}, "
                    f"погрешность {result.discrepancy:+.2f}{saved}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
