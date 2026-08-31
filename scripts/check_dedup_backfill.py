"""Сверка отпечатков: посчитанные миграцией против посчитанных парсером.

Расхождение означает, что следующая заливка не опознает старые операции
и продублирует историю, поэтому проверять надо сразу после миграции.
"""

from __future__ import annotations

import sys

from app.db import SessionLocal
from app.models import Transaction, TxSource
from app.services.zen_csv import ParsedRow


def main() -> int:
    with SessionLocal() as db:
        rows = db.query(Transaction).filter(Transaction.source == TxSource.CSV).all()
        mismatched = 0
        for tx in rows:
            parsed = ParsedRow(
                date=tx.date,
                category_name=tx.category_name,
                account_name=tx.account_name,
                payee=tx.payee,
                comment=tx.comment,
                direction=tx.direction,
                amount_original=tx.amount_original,
                currency=tx.currency,
                zen_created_at=None,
                zen_changed_at=None,
            )
            if parsed.dedup_key != tx.dedup_key:
                mismatched += 1
                if mismatched <= 5:
                    print(f"id={tx.id} sql={tx.dedup_key} py={parsed.dedup_key}")

        print(f"проверено {len(rows)}, расхождений {mismatched}")
        return 1 if mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
