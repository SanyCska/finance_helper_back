# Замена ключа дедупа импорта — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дедуп импорта перестаёт зависеть от `createdDate` и переживает смену таймзоны.

**Architecture:** Ключом становится sha256 содержательных полей операции плюс порядковый номер среди одинаковых строк. Импорт превращается в разность мультимножеств: вставляем `max(0, в_файле - в_базе)` строк на каждый ключ.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, Alembic, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-08-31-import-dedup-design.md`

## Global Constraints

- Команда тестов: `uv run pytest`. Линтер: `uv run ruff check app tests scripts migrations`.
- Комментарии и сообщения коммитов — по-русски, как в остальном репозитории.
- `zen_created_at` и `zen_changed_at` остаются в модели как справочные поля. Не удалять.
- Колонки `dedup_key` и `dedup_seq` — nullable: у ручных операций и начислений подписок ключа нет.
- Прод трогает только задача 5, и только после подтверждения владельца.

---

### Task 1: Контентный ключ у разобранной строки

**Files:**
- Modify: `app/services/zen_csv.py`
- Test: `tests/test_zen_csv.py`

**Interfaces:**
- Produces: `ParsedRow.dedup_key` — property, возвращает `str` из 64 hex-символов.

- [ ] **Step 1: Write the failing test**

В конец `tests/test_zen_csv.py`:

```python
def test_dedup_key_ignores_created_date():
    """Ключ не зависит от времени создания: оно едет вместе с таймзоной."""
    early = parse_zen_csv(build(row("2026-07-01", "Кофе", "300", "2026-07-01 09:00:00")))
    late = parse_zen_csv(build(row("2026-07-01", "Кофе", "300", "2026-07-01 10:00:00")))

    assert early.rows[0].dedup_key == late.rows[0].dedup_key


def test_dedup_key_differs_on_amount():
    a = parse_zen_csv(build(row("2026-07-01", "Кофе", "300", "2026-07-01 09:00:00")))
    b = parse_zen_csv(build(row("2026-07-01", "Кофе", "301", "2026-07-01 09:00:00")))

    assert a.rows[0].dedup_key != b.rows[0].dedup_key
```

Если в `tests/test_zen_csv.py` нет хелперов `row`/`build`, скопируй их из `tests/test_importer.py` (строки 21-28).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_zen_csv.py -k dedup_key -v`
Expected: FAIL, `AttributeError: 'ParsedRow' object has no attribute 'dedup_key'`

- [ ] **Step 3: Write minimal implementation**

В `app/services/zen_csv.py` добавь импорт `import hashlib` к остальным импортам, затем внутрь `class ParsedRow` (после поля `zen_changed_at`):

```python
    @property
    def dedup_key(self) -> str:
        """Отпечаток операции по содержанию.

        Время создания в него не входит: Дзен отдаёт `createdDate` в
        таймзоне устройства на момент выгрузки, и при смене пояса оно
        едет у всей истории разом.
        """
        parts = (
            self.date.isoformat(),
            self.category_name,
            self.account_name,
            self.payee or "",
            self.comment or "",
            self.direction.value,
            format(self.amount_original.normalize(), "f"),
            self.currency,
        )
        # normalize(): в базе сумма лежит как 2000.5000, и миграция срезает
        # хвостовые нули — без нормализации отпечатки разойдутся
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_zen_csv.py -v`
Expected: PASS, старые тесты файла тоже зелёные.

- [ ] **Step 5: Commit**

```bash
git add app/services/zen_csv.py tests/test_zen_csv.py
git commit -m "feat: контентный отпечаток строки выгрузки"
```

---

### Task 2: Колонки ключа в модели

**Files:**
- Modify: `app/models/__init__.py:75-115`
- Test: `tests/test_importer.py`

**Interfaces:**
- Consumes: `ParsedRow.dedup_key` из задачи 1.
- Produces: `Transaction.dedup_key: str | None`, `Transaction.dedup_seq: int | None`, констрейнт `uq_tx_user_dedup`.

- [ ] **Step 1: Write the failing test**

В конец `tests/test_importer.py`:

```python
def test_import_fills_dedup_columns(db: Session, user):
    import_csv(db, user, "zen.csv", build(*ROWS), provider=provider_with_rates())

    saved = db.query(Transaction).order_by(Transaction.date).all()
    assert all(t.dedup_key for t in saved)
    assert all(t.dedup_seq == 0 for t in saved)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_importer.py::test_import_fills_dedup_columns -v`
Expected: FAIL, `AttributeError: type object 'Transaction' has no attribute 'dedup_key'`

- [ ] **Step 3: Write minimal implementation**

В `app/models/__init__.py`, в `class Transaction`, замени блок `__table_args__` на:

```python
    __table_args__ = (
        UniqueConstraint("user_id", "dedup_key", "dedup_seq", name="uq_tx_user_dedup"),
        # одно начисление на подписку и месяц: генератор идемпотентен
        UniqueConstraint("recurring_id", "recurring_month", name="uq_tx_recurring_month"),
        Index("ix_tx_user_date", "user_id", "date"),
        Index("ix_tx_user_category", "user_id", "category_name"),
    )
```

Рядом с полями `zen_created_at` / `zen_changed_at` добавь:

```python
    #: отпечаток содержания операции, см. ParsedRow.dedup_key
    dedup_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: порядковый номер среди операций с тем же отпечатком
    dedup_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

В `app/services/importer.py`, в `_build_transaction`, добавь два аргумента и передай их в `Transaction(...)`:

```python
def _build_transaction(row: ParsedRow, user: User, fx: FxService, seq: int) -> Transaction:
```

и внутри конструктора, рядом с `zen_created_at=row.zen_created_at`:

```python
        dedup_key=row.dedup_key,
        dedup_seq=seq,
```

Во всех вызовах `_build_transaction(row, user, fx)` в этом файле подставь `_build_transaction(row, user, fx, 0)`. Настоящий счётчик появится в задаче 3.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_importer.py -v`
Expected: `test_import_fills_dedup_columns` PASS. Остальные тесты файла тоже зелёные — старый дедуп по `zen_created_at` ещё работает.

- [ ] **Step 5: Commit**

```bash
git add app/models/__init__.py app/services/importer.py tests/test_importer.py
git commit -m "feat: колонки отпечатка у транзакции"
```

---

### Task 3: Импорт как разность мультимножеств

Это ядро фикса и регрессионный тест на исходный баг.

**Files:**
- Modify: `app/services/importer.py:33-92`
- Test: `tests/test_importer.py`

**Interfaces:**
- Consumes: `Transaction.dedup_key`, `Transaction.dedup_seq` из задачи 2.
- Produces: `import_csv` больше не читает `zen_created_at`.

- [ ] **Step 1: Write the failing test**

В `tests/test_importer.py`. Первый тест воспроизводит прод-баг: та же история, все `createdDate` сдвинуты на час.

```python
def shift_hour(csv_row: str) -> str:
    """Сдвинуть время создания на час, как при смене таймзоны устройства."""
    import re

    def bump(match: re.Match[str]) -> str:
        hour = int(match.group(2))
        return f"{match.group(1)} {hour + 1:02d}:{match.group(3)}"

    return re.sub(r"(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}:\d{2})", bump, csv_row)


def test_timezone_shift_does_not_duplicate(db: Session, user):
    """Прод-баг: выгрузка из другого пояса дублировала всю историю."""
    import_csv(db, user, "aug.csv", build(*ROWS), provider=provider_with_rates())

    shifted = build(*(shift_hour(r) for r in ROWS))
    report = import_csv(db, user, "sep.csv", shifted, provider=provider_with_rates())

    assert report.rows_new == 0
    assert report.rows_duplicate == 3
    assert db.query(Transaction).count() == 3


def test_repeated_identical_operations_all_imported(db: Session, user):
    """Четыре поездки по 75 за день — это четыре операции, а не одна."""
    same = [row("2026-07-05", "Транспорт", "75", f"2026-07-05 0{n}:00:00") for n in range(4)]
    report = import_csv(db, user, "zen.csv", build(*same), provider=provider_with_rates())

    assert report.rows_new == 4
    assert db.query(Transaction).count() == 4
    assert sorted(t.dedup_seq for t in db.query(Transaction)) == [0, 1, 2, 3]


def test_partial_overlap_adds_only_missing(db: Session, user):
    """В базе одна поездка, в файле две — добавится ровно одна."""
    one = row("2026-07-05", "Транспорт", "75", "2026-07-05 01:00:00")
    two = row("2026-07-05", "Транспорт", "75", "2026-07-05 02:00:00")
    import_csv(db, user, "a.csv", build(one), provider=provider_with_rates())

    report = import_csv(db, user, "b.csv", build(one, two), provider=provider_with_rates())

    assert report.rows_new == 1
    assert report.rows_duplicate == 1
    assert db.query(Transaction).count() == 2


def test_new_operations_still_arrive(db: Session, user):
    """Дозаливка: старое опознано, новое добавлено."""
    import_csv(db, user, "a.csv", build(*ROWS), provider=provider_with_rates())
    extra = row("2026-07-10", "Кофе", "350", "2026-07-10 09:00:00")

    report = import_csv(db, user, "b.csv", build(*ROWS, extra), provider=provider_with_rates())

    assert report.rows_new == 1
    assert report.rows_duplicate == 3
    assert db.query(Transaction).count() == 4


def test_manual_transaction_is_not_matched(db: Session, user):
    """Ручная операция не гасит строку CSV: у неё нет отпечатка."""
    db.add(
        Transaction(
            user_id=user.id,
            date=dt.date(2026, 7, 1),
            category_name="Кофе",
            account_name="Сербия ",
            direction=Direction.OUTCOME,
            amount_original=Decimal("300"),
            currency="RSD",
            source=TxSource.MANUAL,
            fx_status=FxStatus.PENDING,
        )
    )
    db.commit()

    report = import_csv(db, user, "zen.csv", build(*ROWS), provider=provider_with_rates())

    assert report.rows_new == 3
    assert db.query(Transaction).count() == 4
```

Добавь `Direction` в импорт из `app.models` в шапке файла.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_importer.py -k "timezone_shift or repeated_identical or partial_overlap" -v`
Expected: `test_timezone_shift_does_not_duplicate` FAIL — `rows_new == 3` вместо 0. Это и есть воспроизведённый прод-баг.

- [ ] **Step 3: Write minimal implementation**

В `app/services/importer.py` замени блок от `existing = {` до `report.rows_new = len(to_insert)` на:

```python
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
```

В шапку файла добавь `from collections import Counter` и допиши `func` в импорт: `from sqlalchemy import func, select`.

Обнови докстринг модуля — он всё ещё описывает дедуп по `createdDate`:

```python
"""Импорт выгрузки Дзен-мани в базу.

Дедупликация — по отпечатку содержания операции (`ParsedRow.dedup_key`)
с учётом кратности: одинаковые операции в один день бывают настоящими,
поэтому строка считается дублем, только если таких же в базе уже не
меньше, чем встретилось в файле до неё.

По `createdDate` дедуплицировать нельзя: Дзен отдаёт его в таймзоне
устройства на момент выгрузки, и смена пояса сдвигает ключи всей
истории разом.
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_importer.py -v`
Expected: все PASS, включая `test_second_import_of_same_file_adds_nothing`.

Затем весь набор: `uv run pytest`
Expected: PASS. Тесты используют `Base.metadata.create_all`, миграции им не нужны.

- [ ] **Step 5: Commit**

```bash
git add app/services/importer.py tests/test_importer.py
git commit -m "fix: дедуп импорта по содержанию вместо времени создания"
```

---

### Task 4: Миграция схемы с заполнением ключей

**Files:**
- Create: `migrations/versions/<hash>_отпечаток_дедупа.py`

**Interfaces:**
- Consumes: колонки из задачи 2.

- [ ] **Step 1: Создать пустую ревизию**

```bash
cd /Users/s2pac/projects/personal/finance_helper_back
uv run alembic revision -m "отпечаток дедупа"
```

Открой созданный файл в `migrations/versions/`.

- [ ] **Step 2: Написать upgrade и downgrade**

Отпечаток считается в SQL теми же полями и тем же разделителем `\x1f`, что и в `ParsedRow.dedup_key`. Сумма приводится к тексту без хвостовых нулей, чтобы совпасть с `format(Decimal, "f")`.

```python
def upgrade() -> None:
    op.add_column("transactions", sa.Column("dedup_key", sa.String(length=64), nullable=True))
    op.add_column("transactions", sa.Column("dedup_seq", sa.Integer(), nullable=True))
    op.create_index("ix_transactions_dedup_key", "transactions", ["dedup_key"])

    op.execute(
        """
        update transactions set dedup_key = encode(sha256(convert_to(concat_ws(
            chr(31),
            to_char(date, 'YYYY-MM-DD'),
            category_name,
            account_name,
            coalesce(payee, ''),
            coalesce(comment, ''),
            direction::text,
            trim(trailing '.' from trim(trailing '0' from amount_original::text)),
            currency
        ), 'UTF8')), 'hex')
        where source::text = 'csv'
        """
    )
    op.execute(
        """
        update transactions t set dedup_seq = s.seq from (
            select id, row_number() over (
                partition by user_id, dedup_key order by id
            ) - 1 as seq
            from transactions where dedup_key is not null
        ) s where t.id = s.id
        """
    )

    op.drop_constraint("uq_tx_user_zen_created", "transactions", type_="unique")
    op.create_unique_constraint(
        "uq_tx_user_dedup", "transactions", ["user_id", "dedup_key", "dedup_seq"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_tx_user_dedup", "transactions", type_="unique")
    op.create_unique_constraint(
        "uq_tx_user_zen_created", "transactions", ["user_id", "zen_created_at"]
    )
    op.drop_index("ix_transactions_dedup_key", table_name="transactions")
    op.drop_column("transactions", "dedup_seq")
    op.drop_column("transactions", "dedup_key")
```

- [ ] **Step 3: Проверить, что отпечатки из SQL и из Python совпадают**

Расхождение здесь тихо продублирует историю при следующей заливке, поэтому проверяем явно. Создай `scripts/check_dedup_backfill.py`:

```python
"""Сверка отпечатков: посчитанные миграцией против посчитанных парсером."""

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
```

Запускается после миграции на копии прод-базы (см. задачу 5, шаг 1). Ожидаемый вывод: `расхождений 0`.

- [ ] **Step 4: Прогнать миграцию локально**

```bash
uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head
```
Expected: без ошибок в обе стороны.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions scripts/check_dedup_backfill.py
git commit -m "feat: миграция отпечатка дедупа"
```

---

### Task 5: Чистка дублей в проде

**НЕ ВЫПОЛНЯТЬ без явного подтверждения владельца.** Удаление необратимо.

Уникальный индекс `uq_tx_user_dedup` не встанет, пока в базе лежат дубли, поэтому чистка идёт до миграции из задачи 4.

**Files:**
- Create: `scripts/dedupe_transactions.py`

- [ ] **Step 1: Снять дамп**

```bash
ssh 147.45.238.246 'docker exec finance-postgres-1 pg_dump -U finance -d finance -t transactions' > ~/finance_transactions_backup.sql
wc -l ~/finance_transactions_backup.sql
```
Expected: файл непустой, порядка 20 тысяч строк данных.

- [ ] **Step 2: Написать скрипт с сухим прогоном по умолчанию**

```python
"""Удаление дублей CSV-операций, оставшихся от дедупа по createdDate.

По умолчанию только считает. Удаляет с флагом --apply.
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from app.db import SessionLocal

FIND_DUPLICATES = text(
    """
    select id from (
        select id, row_number() over (
            partition by user_id, date, category_name, account_name,
                         coalesce(payee, ''), coalesce(comment, ''),
                         direction, amount_original, currency
            order by id
        ) as rn
        from transactions where source::text = 'csv'
    ) t where rn > 1
    """
)


def main() -> int:
    apply = "--apply" in sys.argv
    with SessionLocal() as db:
        ids = [row_id for (row_id,) in db.execute(FIND_DUPLICATES)]
        total = db.execute(text("select count(*) from transactions")).scalar_one()
        print(f"всего операций {total}, лишних {len(ids)}, останется {total - len(ids)}")

        if not apply:
            print("сухой прогон, ничего не удалено; для удаления добавь --apply")
            return 0

        db.execute(
            text("delete from transactions where id = any(:ids)"), {"ids": ids}
        )
        db.commit()
        print(f"удалено {len(ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Сухой прогон на проде**

```bash
ssh 147.45.238.246 'docker exec finance-api-1 python -m scripts.dedupe_transactions'
```
Expected: `всего операций 20024, лишних 6688, останется 13336`.

Из них 6683 останется у `user_id=2` и 6608 у `user_id=1` плюс 45 ручных. Если числа не сошлись — остановись и разберись, не запуская удаление.

- [ ] **Step 4: Удаление**

Только после подтверждения владельца.

```bash
ssh 147.45.238.246 'docker exec finance-api-1 python -m scripts.dedupe_transactions --apply'
```

- [ ] **Step 5: Проверить результат**

```bash
ssh 147.45.238.246 "docker exec finance-postgres-1 psql -U finance -d finance -c \"select user_id, source, count(*) from transactions group by user_id, source order by user_id\""
```
Expected: у `user_id=2` 6683 строки с `source = csv`.

- [ ] **Step 6: Накатить миграцию и сверить отпечатки**

```bash
ssh 147.45.238.246 'docker exec finance-api-1 alembic upgrade head'
ssh 147.45.238.246 'docker exec finance-api-1 python -m scripts.check_dedup_backfill'
```
Expected: `расхождений 0`.

- [ ] **Step 7: Проверить на живом файле**

Залей боту тот же дамп от 31.08 ещё раз.
Expected: «Новых операций: 0, дублей: 6763».

- [ ] **Step 8: Commit**

```bash
git add scripts/dedupe_transactions.py
git commit -m "chore: скрипт чистки дублей импорта"
```

---

## Порядок выката

Задачи 1-4 — обычная разработка, коммиты локальные. На прод порядок такой:

1. Чистка дублей (задача 5, шаги 1-5) — до миграции, иначе уникальный индекс не встанет.
2. Деплой кода задач 1-4.
3. Миграция и сверка (задача 5, шаг 6).
4. Контрольная заливка дампа (задача 5, шаг 7).
