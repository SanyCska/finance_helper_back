"""Удаление заливки, задвоившей историю при дедупе по createdDate.

Дзен отдаёт `createdDate` в таймзоне устройства на момент выгрузки,
поэтому выгрузка из другого пояса заезжала как новая история целиком.

Схлопывать одинаковые строки нельзя: повторы в один день бывают
настоящими (четыре поездки по 75 в дампе — это четыре операции).
Поэтому скрипт удаляет строки старой заливки только там, где их
покрывает более поздняя, и ровно по кратности.

Использование:
    python -m scripts.dedupe_transactions --user 2 --before 2026-08-15
    python -m scripts.dedupe_transactions --user 2 --before 2026-08-15 --apply
"""

from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import text

from app.db import SessionLocal
from app.models import Transaction, TxSource

#: те же поля, что в ParsedRow.dedup_key
KEY_FIELDS = (
    "date",
    "category_name",
    "account_name",
    "payee",
    "comment",
    "direction",
    "amount_original",
    "currency",
)


def content_key(tx: Transaction) -> tuple:
    return tuple(getattr(tx, field) or "" for field in KEY_FIELDS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", type=int, required=True)
    parser.add_argument("--before", required=True, help="граница заливки, YYYY-MM-DD")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as db:
        rows = (
            db.query(Transaction)
            .filter(Transaction.user_id == args.user, Transaction.source == TxSource.CSV)
            .order_by(Transaction.id)
            .all()
        )
        old = [t for t in rows if t.created_at.isoformat() < args.before]
        new = [t for t in rows if t.created_at.isoformat() >= args.before]
        print(f"старая заливка {len(old)} строк, поздняя {len(new)}")

        if not old or not new:
            print("нечего сравнивать: одна из заливок пуста")
            return 1

        covered = Counter(content_key(t) for t in new)
        doomed: list[int] = []
        kept_tail = 0
        seen: Counter = Counter()
        for tx in old:
            key = content_key(tx)
            if seen[key] < covered[key]:
                doomed.append(tx.id)
            else:
                # операции, которой в поздней выгрузке нет: удалять нельзя
                kept_tail += 1
            seen[key] += 1

        print(f"к удалению {len(doomed)}, останется от старой заливки {kept_tail}")
        print(f"итого после чистки {len(rows) - len(doomed)}")

        if not args.apply:
            print("сухой прогон, ничего не удалено; для удаления добавь --apply")
            return 0

        db.query(Transaction).filter(Transaction.id.in_(doomed)).delete(
            synchronize_session=False
        )
        # после удаления в dedup_seq остаются дыры, а импорт нумерует
        # с нуля — без пересчёта следующая заливка упрётся в уникальный индекс
        db.execute(
            text(
                """
                update transactions t set dedup_seq = s.seq from (
                    select id, row_number() over (
                        partition by user_id, dedup_key order by id
                    ) - 1 as seq
                    from transactions where dedup_key is not null
                ) s where t.id = s.id and t.dedup_seq is distinct from s.seq
                """
            )
        )
        db.commit()
        print(f"удалено {len(doomed)}, порядковые номера пересчитаны")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
