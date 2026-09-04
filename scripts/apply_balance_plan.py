"""Точечная правка истории остатков по плану из JSON.

Нужна, когда историю надо починить руками: перенести снимок на другую дату
или дописать пропавший. Правки идут через `funds.update_balance` и
`funds.set_balance`, чтобы суммы в базовой валюте пересчитались по курсам на
нужные даты, а не остались посчитанными по старым.

Формат плана:

    {
      "redate": [{"balance_id": 2, "date": "2026-08-31"}],
      "insert": [{"source_id": 2, "amount": "4457.78", "date": "2026-07-31",
                  "note": "стартовый остаток"}]
    }

    python -m scripts.apply_balance_plan plan.json          # что будет сделано
    python -m scripts.apply_balance_plan plan.json --apply  # сделать

На проде скрипт скармливают контейнеру по stdin, а план кладут файлом:

    COMPOSE='docker compose -f /opt/finance/docker-compose.yml'
    ssh root@147.45.238.246 "$COMPOSE exec -T api sh -c 'cat > /tmp/plan.json'" < plan.json
    ssh root@147.45.238.246 "$COMPOSE exec -T api python - /tmp/plan.json" \\
      < scripts/apply_balance_plan.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from decimal import Decimal

from app.db import SessionLocal
from app.models import FundBalance, FundSource, User
from app.services import funds


def _money(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", help="файл с планом правок")
    parser.add_argument(
        "--apply", action="store_true", help="без него только показывает, что изменится"
    )
    args = parser.parse_args(argv)

    with open(args.plan) as file:
        plan = json.load(file)

    with SessionLocal() as db:
        moves: list[tuple[FundBalance, dt.date]] = []
        for item in plan.get("redate", []):
            balance = db.get(FundBalance, item["balance_id"])
            if balance is None:
                print(f"  #{item['balance_id']}: снимок не найден — пропускаю")
                continue
            moves.append((balance, dt.date.fromisoformat(item["date"])))

        additions: list[tuple[FundSource, Decimal, dt.date, str | None]] = []
        for item in plan.get("insert", []):
            source = db.get(FundSource, item["source_id"])
            if source is None:
                print(f"  источник {item['source_id']}: не найден — пропускаю")
                continue
            additions.append(
                (
                    source,
                    Decimal(item["amount"]),
                    dt.date.fromisoformat(item["date"]),
                    item.get("note"),
                )
            )

        print(f"Перенести на другую дату: {len(moves)}")
        for balance, day in moves:
            source = db.get(FundSource, balance.source_id)
            title = source.title if source else f"источник {balance.source_id}"
            print(
                f"  #{balance.id} {title}: {balance.date} → {day}, "
                f"{balance.amount_original:.2f} {balance.currency}"
            )

        print(f"\nДописать снимки: {len(additions)}")
        for source, amount, day, _ in additions:
            print(f"  {source.title}: {day}, {amount:.2f} {source.currency}")

        if not args.apply:
            print("\nЭто прогон вхолостую. Повтори с --apply, чтобы записать.")
            return 0

        print("\nПишу:")
        for balance, day in moves:
            funds.update_balance(db, balance, day=day)
            print(f"  #{balance.id} → {balance.date}, {_money(balance.amount_base)} в базовой")
        for source, amount, day, note in additions:
            user = db.get(User, source.user_id)
            created = funds.set_balance(db, user, source, amount, day, note)
            print(
                f"  {source.title} #{created.id} → {created.date}, "
                f"{_money(created.amount_base)} в базовой"
            )

        owners = {balance.user_id for balance, _ in moves} | {
            source.user_id for source, *_ in additions
        }
        days = sorted({day for _, day in moves} | {day for _, _, day, _ in additions})
        print("\nИтог по всем источникам на затронутые даты:")
        for user_id in sorted(owners):
            user = db.get(User, user_id)
            who = user.username or user.telegram_id
            for day in days:
                print(f"  {who} на {day}: {funds.total_base(db, user, day)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
