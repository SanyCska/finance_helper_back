"""Импорт выгрузки Дзен-мани в базу.

Дедупликация — по `createdDate` из выгрузки: в реальных дампах это поле уникально
для каждой операции, поэтому повторная заливка полного дампа не создаёт дублей.
Строки без `createdDate` дедуплицировать нечем, они вставляются всегда.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FxStatus, ImportBatch, Transaction, TxSource, User
from app.services.fx import FxProvider, FxService
from app.services.zen_csv import ParsedRow, parse_zen_csv

#: сколько ошибок разбора показывать в отчёте
MAX_REPORTED_ERRORS = 10


@dataclass
class ImportReport:
    rows_total: int = 0
    rows_new: int = 0
    rows_duplicate: int = 0
    rows_error: int = 0
    skipped_transfers: int = 0
    pending_fx: int = 0
    errors: list[str] = field(default_factory=list)


def import_csv(
    db: Session,
    user: User,
    filename: str,
    content: bytes,
    provider: FxProvider | None = None,
) -> ImportReport:
    parsed = parse_zen_csv(content)

    fx = FxService(db, provider=provider)
    fx.ensure_rates((row.date, row.currency) for row in parsed.rows)

    existing = {
        created_at
        for (created_at,) in db.execute(
            select(Transaction.zen_created_at).where(
                Transaction.user_id == user.id,
                Transaction.zen_created_at.is_not(None),
            )
        )
    }

    report = ImportReport(
        rows_total=len(parsed.rows),
        rows_error=len(parsed.errors),
        skipped_transfers=parsed.skipped_transfers,
        errors=[f"строка {error.line_no}: {error.message}" for error in parsed.errors][
            :MAX_REPORTED_ERRORS
        ],
    )

    seen_in_file: set = set()
    to_insert: list[Transaction] = []
    for row in parsed.rows:
        key = row.zen_created_at
        if key is not None and (key in existing or key in seen_in_file):
            report.rows_duplicate += 1
            continue
        if key is not None:
            seen_in_file.add(key)

        transaction = _build_transaction(row, user, fx)
        if transaction.fx_status is FxStatus.PENDING:
            report.pending_fx += 1
        to_insert.append(transaction)

    db.add_all(to_insert)
    report.rows_new = len(to_insert)

    db.add(
        ImportBatch(
            user_id=user.id,
            filename=filename,
            rows_total=report.rows_total,
            rows_new=report.rows_new,
            rows_duplicate=report.rows_duplicate,
            rows_error=report.rows_error,
            skipped_transfers=report.skipped_transfers,
            errors=report.errors or None,
        )
    )
    db.commit()
    return report


def _build_transaction(row: ParsedRow, user: User, fx: FxService) -> Transaction:
    amount_base, rate, status = fx.convert(row.amount_original, row.currency, row.date)
    return Transaction(
        user_id=user.id,
        date=row.date,
        category_name=row.category_name,
        account_name=row.account_name,
        payee=row.payee,
        comment=row.comment,
        direction=row.direction,
        amount_original=row.amount_original,
        currency=row.currency,
        amount_base=amount_base,
        fx_rate=rate,
        fx_status=status,
        source=TxSource.CSV,
        zen_created_at=row.zen_created_at,
        zen_changed_at=row.zen_changed_at,
    )
