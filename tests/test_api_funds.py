"""Тесты ручек средств, подписок и обновлённых планов."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.main import app
from app.models import Direction, FxRate, FxStatus, Transaction, TxSource, User


@pytest.fixture
def client(db: Session, user: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def add_tx(db: Session, user: User, day: str, category: str, amount: str) -> None:
    db.add(
        Transaction(
            user_id=user.id,
            date=dt.date.fromisoformat(day),
            category_name=category,
            account_name="Сербия",
            direction=Direction.OUTCOME,
            amount_original=Decimal(amount),
            currency="USD",
            amount_base=Decimal(amount),
            fx_rate=Decimal(1),
            fx_status=FxStatus.OK,
            source=TxSource.MANUAL,
        )
    )
    db.commit()


# --- средства -------------------------------------------------------------


def test_source_is_created_with_starting_amount(client: TestClient):
    response = client.post("/api/funds", json={"title": "Сербия", "amount": "1200"})

    assert response.status_code == 201
    assert response.json()["amount_original"] == "1200.0000"


def test_overview_sums_sources(client: TestClient):
    client.post("/api/funds", json={"title": "Сербия", "amount": "1200"})
    client.post("/api/funds", json={"title": "Наличные", "amount": "300"})

    payload = client.get("/api/funds").json()

    assert payload["total_base"] == "1500.00"
    assert len(payload["sources"]) == 2
    assert len(payload["history"]) == 12


def test_new_balance_replaces_previous(client: TestClient):
    source_id = client.post("/api/funds", json={"title": "Сербия", "amount": "100"}).json()["id"]

    client.put(f"/api/funds/{source_id}/balance", json={"amount": "250"})
    history = client.get(f"/api/funds/{source_id}/history").json()

    assert client.get("/api/funds").json()["total_base"] == "250.00"
    assert len(history) == 2


def test_balance_in_future_is_rejected(client: TestClient):
    source_id = client.post("/api/funds", json={"title": "Сербия"}).json()["id"]
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()

    response = client.put(f"/api/funds/{source_id}/balance", json={"amount": "1", "date": tomorrow})

    assert response.status_code == 422


def test_archived_source_disappears_from_list(client: TestClient):
    source_id = client.post("/api/funds", json={"title": "Старый счёт"}).json()["id"]

    client.patch(f"/api/funds/{source_id}", json={"archived": True})

    assert client.get("/api/funds").json()["sources"] == []


def test_foreign_source_is_not_found(client: TestClient, db: Session):
    other = User(telegram_id=99, base_currency="USD")
    db.add(other)
    db.commit()

    assert client.get("/api/funds/999/history").status_code == 404


def test_month_check_reports_discrepancy(client: TestClient, db: Session, user: User):
    source_id = client.post("/api/funds", json={"title": "Сербия"}).json()["id"]
    client.put(
        f"/api/funds/{source_id}/balance", json={"amount": "1000", "date": "2026-06-30"}
    )
    client.put(
        f"/api/funds/{source_id}/balance", json={"amount": "1200", "date": "2026-07-31"}
    )
    client.put("/api/income/2026-07", json={"amount": "500"})
    add_tx(db, user, "2026-07-10", "Кофе", "250")

    payload = client.get("/api/funds/checks/2026-07").json()

    assert payload["real_saldo"] == "200.00"
    assert payload["tracked_saldo"] == "250.00"
    assert payload["discrepancy"] == "-50.00"
    assert payload["is_saved"] is False


def test_saved_check_shows_up_in_history(client: TestClient):
    source_id = client.post("/api/funds", json={"title": "Сербия"}).json()["id"]
    client.put(f"/api/funds/{source_id}/balance", json={"amount": "500", "date": "2026-07-31"})

    saved = client.post("/api/funds/checks/2026-07", json={"note": "сошлось"}).json()
    history = client.get("/api/funds/checks").json()

    assert saved["is_saved"] is True
    assert [item["month"] for item in history] == ["2026-07"]
    assert history[0]["note"] == "сошлось"


# --- подписки -------------------------------------------------------------


def test_subscription_is_created_with_monthly_share(client: TestClient):
    response = client.post(
        "/api/recurring",
        json={"title": "Netflix", "amount": "120", "period_months": 12, "charge_day": 5},
    )

    assert response.status_code == 201
    assert response.json()["monthly_amount"] == "10.00"
    assert response.json()["category_name"] == "Подписки"


def test_list_generates_missing_charges(client: TestClient, db: Session, user: User):
    start = (dt.date.today().replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    client.post(
        "/api/recurring",
        json={
            "title": "Квартира",
            "kind": "rent",
            "amount": "700",
            "period_months": 1,
            "starts_on": start.isoformat(),
        },
    )

    payload = client.get("/api/recurring").json()
    charges = db.query(Transaction).filter(Transaction.source == TxSource.RECURRING).all()

    assert payload["generated"] == 1
    assert payload["monthly_total_base"] == "700.00"
    assert len(charges) == 1
    assert charges[0].category_name == "Аренда"


def test_deleting_subscription_removes_its_charges(client: TestClient, db: Session):
    start = (dt.date.today().replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    item_id = client.post(
        "/api/recurring",
        json={"title": "Netflix", "amount": "10", "starts_on": start.isoformat()},
    ).json()["id"]
    client.get("/api/recurring")

    client.delete(f"/api/recurring/{item_id}")

    assert db.query(Transaction).filter(Transaction.source == TxSource.RECURRING).count() == 0


def test_recurring_charge_cannot_be_edited_directly(client: TestClient, db: Session):
    start = (dt.date.today().replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    client.post(
        "/api/recurring",
        json={"title": "Netflix", "amount": "10", "starts_on": start.isoformat()},
    )
    client.get("/api/recurring")
    charge = db.query(Transaction).filter(Transaction.source == TxSource.RECURRING).one()

    response = client.patch(f"/api/transactions/{charge.id}", json={"amount_original": "5"})

    assert response.status_code == 409
    assert "подписк" in response.json()["detail"].lower()


def test_subscription_can_be_paused(client: TestClient):
    item_id = client.post("/api/recurring", json={"title": "Netflix", "amount": "10"}).json()["id"]

    client.patch(f"/api/recurring/{item_id}", json={"active": False})

    assert client.get("/api/recurring").json()["monthly_total_base"] == "0.00"


# --- планы ----------------------------------------------------------------


def test_plan_line_keeps_currency_and_category(client: TestClient, db: Session):
    db.add(FxRate(date=dt.date.today(), currency="EUR", rate_to_base=Decimal("1.1")))
    db.commit()
    month = dt.date.today().strftime("%Y-%m")

    payload = client.put(
        f"/api/plans/{month}",
        json={
            "lines": [
                {
                    "title": "Аренда",
                    "amount": "500",
                    "currency": "EUR",
                    "category_name": "Аренда",
                }
            ]
        },
    ).json()

    assert payload["lines"][0]["currency"] == "EUR"
    assert payload["lines"][0]["amount_base"] == "550.00"
    assert payload["lines"][0]["category_name"] == "Аренда"
    assert payload["total"] == "550.00"


def test_plan_falls_back_to_previous_month(client: TestClient):
    client.put(
        "/api/plans/2026-07",
        json={"lines": [{"title": "Аренда", "amount": "700"}]},
    )

    payload = client.get("/api/plans/2026-08").json()

    assert payload["source"] == "previous"
    assert [line["title"] for line in payload["lines"]] == ["Аренда"]


def test_saved_plan_reports_its_own_source(client: TestClient):
    client.put("/api/plans/2026-08", json={"lines": [{"title": "Кофе", "amount": "50"}]})

    assert client.get("/api/plans/2026-08").json()["source"] == "saved"


def test_vs_fact_matches_lines_to_categories(client: TestClient, db: Session, user: User):
    client.put(
        "/api/plans/2026-07",
        json={
            "lines": [
                {"title": "Продукты", "amount": "400", "category_name": "Продукты"},
                {"title": "Кофе", "amount": "50", "category_name": "Кофе"},
            ]
        },
    )
    add_tx(db, user, "2026-07-05", "Продукты", "450")
    add_tx(db, user, "2026-07-06", "Такси", "80")

    payload = client.get("/api/plans/2026-07/vs-fact").json()
    lines = {line["title"]: line for line in payload["lines"]}

    assert lines["Продукты"]["fact"] == "450.0000"
    assert lines["Продукты"]["diff"] == "50.0000"
    # категория была в плане, но по ней не потратили ничего
    assert lines["Кофе"]["fact"] == "0"
    assert [item["category"] for item in payload["unplanned"]] == ["Такси"]


def test_income_is_carried_to_the_next_month(client: TestClient):
    client.put("/api/income/2026-07", json={"amount": "3000"})

    payload = client.get("/api/income/2026-09").json()

    assert payload["amount"] == "3000.00"
    assert payload["source"] == "carried"
    assert payload["from_month"] == "2026-07"
