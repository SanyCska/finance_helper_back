"""Заливка выгрузки Дзен-мани из командной строки.

    uv run python scripts/import_csv.py ~/Downloads/zen_dump.csv --telegram-id 42
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from app.bot.handlers import get_or_create_user
from app.db import SessionLocal
from app.services.importer import import_csv


def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт выгрузки Дзен-мани")
    parser.add_argument("path", type=pathlib.Path)
    parser.add_argument("--telegram-id", type=int, required=True)
    args = parser.parse_args()

    content = args.path.read_bytes()
    with SessionLocal() as db:
        user = get_or_create_user(db, args.telegram_id, None)
        report = import_csv(db, user, args.path.name, content)

    print(
        f"всего строк: {report.rows_total}\n"
        f"новых: {report.rows_new}\n"
        f"дублей: {report.rows_duplicate}\n"
        f"ошибок: {report.rows_error}\n"
        f"без курса: {report.pending_fx}"
    )
    for error in report.errors:
        print(" ", error, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
