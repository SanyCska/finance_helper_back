"""Тесты переноса остатков, введённых в первых числах, на конец месяца."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import FundSource, User
from app.services import funds
from scripts import close_month_balances as script


def make_source(db: Session, user: User) -> FundSource:
    item = FundSource(user_id=user.id, title="Сербия", currency="USD", position=0)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def run(db: Session, monkeypatch, *args: str) -> int:
    # скрипт открывает свою сессию; подсовываем тестовую
    monkeypatch.setattr(script, "SessionLocal", lambda: db)
    return script.main(list(args))


def test_переносит_снимок_первых_чисел_на_конец_месяца(db, user, monkeypatch, capsys):
    source = make_source(db, user)
    funds.set_balance(db, user, source, Decimal("13354"), dt.date(2026, 9, 2))

    assert run(db, monkeypatch, "2026-08", "--apply") == 0

    balance = funds.latest_balance(db, source.id)
    assert balance is not None
    assert balance.date == dt.date(2026, 8, 31)
    # остаток теперь виден августу: сверка августа считает его концом месяца
    assert funds.total_base(db, user, dt.date(2026, 8, 31)) == Decimal("13354.00")


def test_без_apply_ничего_не_меняет(db, user, monkeypatch, capsys):
    source = make_source(db, user)
    funds.set_balance(db, user, source, Decimal("13354"), dt.date(2026, 9, 2))

    assert run(db, monkeypatch, "2026-08") == 0

    balance = funds.latest_balance(db, source.id)
    assert balance is not None and balance.date == dt.date(2026, 9, 2)
    assert "вхолостую" in capsys.readouterr().out


def test_не_трогает_снимки_позже_окна(db, user, monkeypatch):
    source = make_source(db, user)
    funds.set_balance(db, user, source, Decimal("100"), dt.date(2026, 9, 2))
    funds.set_balance(db, user, source, Decimal("90"), dt.date(2026, 9, 20))

    run(db, monkeypatch, "2026-08", "--apply")

    dates = sorted(item.date for item in funds.history(db, source.id))
    assert dates == [dt.date(2026, 8, 31), dt.date(2026, 9, 20)]


def test_окно_настраивается(db, user, monkeypatch):
    source = make_source(db, user)
    funds.set_balance(db, user, source, Decimal("100"), dt.date(2026, 9, 7))

    run(db, monkeypatch, "2026-08", "--days", "10", "--apply")

    balance = funds.latest_balance(db, source.id)
    assert balance is not None and balance.date == dt.date(2026, 8, 31)


def test_на_пустом_окне_сообщает_и_не_падает(db, user, monkeypatch, capsys):
    make_source(db, user)

    assert run(db, monkeypatch, "2026-08") == 0
    assert "переносить нечего" in capsys.readouterr().out
