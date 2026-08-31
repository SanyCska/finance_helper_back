"""Импорт выгрузки Дзен-мани в базу.

Дедупликация — по отпечатку содержания операции (`ParsedRow.dedup_key`)
с учётом кратности: одинаковые операции в один день бывают настоящими,
поэтому строка считается дублем, только если таких же в базе уже не
меньше, чем встретилось в файле до неё.

По `createdDate` дедуплицировать нельзя: Дзен отдаёт его в таймзоне
устройства на момент выгрузки, и смена пояса сдвигает ключи всей
истории разом.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func, select
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

    counts_in_db: dict[str, int] = {
        key: count
        for key, count in db.execute(
            select(Transaction.dedup_key, func.count())
            .where(
                Transaction.user_id == user.id,
                Transaction.dedup_key.is_not(None),
            )
            .group_by(Transaction.dedup_key)
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

    seen_in_file: Counter[str] = Counter()
    to_insert: list[Transaction] = []
    for row in parsed.rows:
        key = row.dedup_key
        seq = seen_in_file[key]
        seen_in_file[key] += 1

        # строка уже есть в базе, если её порядковый номер укладывается
        # в число сохранённых операций с тем же отпечатком
        if seq < counts_in_db.get(key, 0):
            report.rows_duplicate += 1
            continue

        transaction = _build_transaction(row, user, fx, seq)
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


def _build_transaction(row: ParsedRow, user: User, fx: FxService, seq: int) -> Transaction:
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
        dedup_key=row.dedup_key,
        dedup_seq=seq,
        zen_created_at=row.zen_created_at,
        zen_changed_at=row.zen_changed_at,
    )
