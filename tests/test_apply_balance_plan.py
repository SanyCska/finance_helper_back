"""Тесты точечной правки истории остатков по плану."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import FundSource, User
from app.services import funds
from scripts import apply_balance_plan as script


def make_source(db: Session, user: User, title: str = "Сербия") -> FundSource:
    item = FundSource(user_id=user.id, title=title, currency="USD", position=0)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def run(db: Session, monkeypatch, tmp_path, plan: dict, *args: str) -> int:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(script, "SessionLocal", lambda: db)
    return script.main([str(path), *args])


def test_переносит_и_дописывает(db, user, monkeypatch, tmp_path):
    source = make_source(db, user)
    balance = funds.set_balance(db, user, source, Decimal("249"), dt.date(2026, 9, 3))

    run(
        db,
        monkeypatch,
        tmp_path,
        {
            "redate": [{"balance_id": balance.id, "date": "2026-08-31"}],
            "insert": [
                {
                    "source_id": source.id,
                    "amount": "112.45",
                    "date": "2026-07-31",
                    "note": "стартовый остаток",
                }
            ],
        },
        "--apply",
    )

    history = sorted(funds.history(db, source.id), key=lambda item: item.date)
    assert [(item.date, item.amount_original) for item in history] == [
        (dt.date(2026, 7, 31), Decimal("112.4500")),
        (dt.date(2026, 8, 31), Decimal("249.0000")),
    ]
    assert history[0].note == "стартовый остаток"


def test_без_apply_не_пишет(db, user, monkeypatch, tmp_path, capsys):
    source = make_source(db, user)
    balance = funds.set_balance(db, user, source, Decimal("249"), dt.date(2026, 9, 3))

    run(db, monkeypatch, tmp_path, {"redate": [{"balance_id": balance.id, "date": "2026-08-31"}]})

    assert funds.latest_balance(db, source.id).date == dt.date(2026, 9, 3)
    assert "вхолостую" in capsys.readouterr().out


def test_пропускает_несуществующее(db, user, monkeypatch, tmp_path, capsys):
    make_source(db, user)

    code = run(
        db,
        monkeypatch,
        tmp_path,
        {"redate": [{"balance_id": 999, "date": "2026-08-31"}], "insert": []},
        "--apply",
    )

    assert code == 0
    assert "не найден" in capsys.readouterr().out
