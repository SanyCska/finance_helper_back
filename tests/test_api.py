"""Тесты HTTP API."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.main import app
from app.models import Direction, FxStatus, Transaction, TxSource, User
from tests.test_auth import make_init_data

HEADER = (
    "date;categoryName;payee;comment;outcomeAccountName;outcome;outcomeCurrencyShortTitle;"
    "incomeAccountName;income;incomeCurrencyShortTitle;createdDate;changedDate;qrCode"
)


@pytest.fixture
def client(db: Session, user: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client(db: Session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def add_tx(
    db: Session,
    user: User,
    day: str,
    category: str,
    amount: str,
    direction: Direction = Direction.OUTCOME,
    source: TxSource = TxSource.CSV,
) -> Transaction:
    transaction = Transaction(
        user_id=user.id,
        date=dt.date.fromisoformat(day),
        category_name=category,
        account_name="Сербия",
        direction=direction,
        amount_original=Decimal(amount),
        currency="USD",
        amount_base=Decimal(amount),
        fx_rate=Decimal(1),
        fx_status=FxStatus.OK,
        source=source,
        zen_created_at=(
            dt.datetime.fromisoformat(f"{day}T10:00:00") if source is TxSource.CSV else None
        ),
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction


# --- авторизация ----------------------------------------------------------


def test_request_without_authorization_is_rejected(anon_client: TestClient):
    response = anon_client.get("/api/stats/month", params={"month": "2026-07"})

    assert response.status_code == 401


def test_request_with_valid_init_data_passes(anon_client: TestClient):
    response = anon_client.get(
        "/api/meta/months",
        headers={"Authorization": f"tma {make_init_data(telegram_id=42)}"},
    )

    assert response.status_code == 200


def test_request_from_foreign_telegram_id_is_forbidden(anon_client: TestClient):
    response = anon_client.get(
        "/api/meta/months",
        headers={"Authorization": f"tma {make_init_data(telegram_id=999)}"},
    )

    assert response.status_code == 403


def test_health_needs_no_auth(anon_client: TestClient):
    assert anon_client.get("/health").json() == {"status": "ok"}


# --- сводка месяца --------------------------------------------------------


def test_month_summary_returns_saldo_and_categories(client: TestClient, db: Session, user: User):
    add_tx(db, user, "2026-07-01", "Кофе", "100")
    add_tx(db, user, "2026-07-02", "Рестики", "300")
    client.put("/api/income/2026-07", json={"amount": "1000"})

    body = client.get("/api/stats/month", params={"month": "2026-07"}).json()

    assert Decimal(body["outcome_total"]) == Decimal("400")
    assert Decimal(body["saldo"]) == Decimal("600")
    assert [item["category"] for item in body["categories"]] == ["Рестики", "Кофе"]
    assert len(body["recent"]) == 2
    assert body["base_currency"] == "USD"


def test_month_summary_rejects_broken_month(client: TestClient):
    assert client.get("/api/stats/month", params={"month": "июль"}).status_code == 422


def test_empty_month_returns_zeros(client: TestClient):
    body = client.get("/api/stats/month", params={"month": "2020-01"}).json()

    assert Decimal(body["outcome_total"]) == Decimal(0)
    assert body["categories"] == []


def test_months_list_includes_current_month(client: TestClient, db: Session, user: User):
    add_tx(db, user, "2026-07-01", "Кофе", "100")

    body = client.get("/api/meta/months").json()

    assert "2026-07" in body["months"]
    assert body["current"] == dt.date.today().strftime("%Y-%m")


# --- транзакции -----------------------------------------------------------


def test_transactions_are_filtered_by_month_and_category(
    client: TestClient, db: Session, user: User
):
    add_tx(db, user, "2026-07-01", "Кофе", "100")
    add_tx(db, user, "2026-07-02", "Рестики", "300")
    add_tx(db, user, "2026-06-01", "Кофе", "50")

    body = client.get(
        "/api/transactions", params={"month": "2026-07", "categories": ["Кофе"]}
    ).json()

    assert body["total"] == 1
    assert Decimal(body["items"][0]["amount_original"]) == Decimal("100")


def test_transactions_search_matches_comment(client: TestClient, db: Session, user: User):
    transaction = add_tx(db, user, "2026-07-01", "Кофе", "100")
    transaction.comment = "Кофейня у дома"
    db.commit()

    # регистронезависимый поиск по кириллице обеспечивает ilike в Postgres;
    # на SQLite в тестах регистр совпадает специально
    body = client.get("/api/transactions", params={"q": "Кофейня"}).json()

    assert body["total"] == 1


def test_manual_transaction_is_created(client: TestClient):
    response = client.post(
        "/api/transactions",
        json={
            "date": "2026-07-15",
            "category_name": "Наличные",
            "account_name": "Cash",
            "amount_original": "42.50",
            "currency": "USD",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "manual"
    assert Decimal(body["amount_base"]) == Decimal("42.5")


def test_transaction_with_future_date_is_rejected(client: TestClient):
    future = (dt.date.today() + dt.timedelta(days=30)).isoformat()

    response = client.post(
        "/api/transactions",
        json={"date": future, "amount_original": "10", "currency": "USD"},
    )

    assert response.status_code == 422


def test_negative_amount_is_rejected(client: TestClient):
    response = client.post(
        "/api/transactions",
        json={"date": "2026-07-15", "amount_original": "-5", "currency": "USD"},
    )

    assert response.status_code == 422


def test_csv_transaction_cannot_be_edited(client: TestClient, db: Session, user: User):
    transaction = add_tx(db, user, "2026-07-01", "Кофе", "100")

    response = client.patch(f"/api/transactions/{transaction.id}", json={"category_name": "Чай"})

    assert response.status_code == 409


def test_manual_transaction_can_be_edited_and_deleted(client: TestClient):
    created = client.post(
        "/api/transactions",
        json={"date": "2026-07-15", "amount_original": "10", "currency": "USD"},
    ).json()

    patched = client.patch(
        f"/api/transactions/{created['id']}", json={"amount_original": "25"}
    )
    assert patched.status_code == 200
    assert Decimal(patched.json()["amount_base"]) == Decimal("25")

    assert client.delete(f"/api/transactions/{created['id']}").status_code == 204
    assert client.get("/api/transactions").json()["total"] == 0


def test_missing_transaction_returns_404(client: TestClient):
    assert client.patch("/api/transactions/99999", json={}).status_code == 404


# --- доход и настройки ----------------------------------------------------


def test_income_falls_back_to_default(client: TestClient, db: Session, user: User):
    client.put("/api/settings", json={"default_monthly_income": "3500"})

    body = client.get("/api/income/2026-09").json()

    assert Decimal(body["amount"]) == Decimal("3500")
    assert body["is_default"] is True


def test_income_can_be_saved_as_default(client: TestClient, db: Session, user: User):
    client.put("/api/income/2026-07", json={"amount": "4000", "save_as_default": True})

    assert client.get("/api/income/2026-07").json()["is_default"] is False
    settings_body = client.get("/api/settings").json()
    assert Decimal(settings_body["default_monthly_income"]) == Decimal("4000")


# --- планы ----------------------------------------------------------------


def test_plan_is_saved_and_returned_with_expected_saldo(client: TestClient):
    client.put("/api/income/2026-08", json={"amount": "4000"})

    response = client.put(
        "/api/plans/2026-08",
        json={
            "lines": [
                {"title": "Продукты", "amount": "1200"},
                {"title": "Кафе", "amount": "650"},
            ]
        },
    )

    body = response.json()
    assert Decimal(body["total"]) == Decimal("1850")
    assert Decimal(body["expected_saldo"]) == Decimal("2150")
    assert [line["title"] for line in body["lines"]] == ["Продукты", "Кафе"]


def test_saving_plan_replaces_previous_lines(client: TestClient):
    client.put("/api/plans/2026-08", json={"lines": [{"title": "Старое", "amount": "100"}]})
    client.put("/api/plans/2026-08", json={"lines": [{"title": "Новое", "amount": "200"}]})

    body = client.get("/api/plans/2026-08").json()

    assert [line["title"] for line in body["lines"]] == ["Новое"]


def test_empty_plan_lines_are_dropped(client: TestClient):
    body = client.put(
        "/api/plans/2026-08",
        json={"lines": [{"title": "", "amount": "0"}, {"title": "Еда", "amount": "100"}]},
    ).json()

    assert len(body["lines"]) == 1


def test_plan_vs_fact_compares_totals(client: TestClient, db: Session, user: User):
    add_tx(db, user, "2026-07-01", "Кофе", "1000")
    client.put("/api/income/2026-07", json={"amount": "4000"})
    client.put("/api/plans/2026-07", json={"lines": [{"title": "Всё", "amount": "800"}]})

    body = client.get("/api/plans/2026-07/vs-fact").json()

    assert Decimal(body["plan_total"]) == Decimal("800")
    assert Decimal(body["fact_total"]) == Decimal("1000")
    assert Decimal(body["diff"]) == Decimal("200")
    assert body["has_plan"] is True
    assert Decimal(body["plan_saldo"]) == Decimal("3200")


def test_plan_vs_fact_without_plan_reports_absence(client: TestClient):
    body = client.get("/api/plans/2026-07/vs-fact").json()

    assert body["has_plan"] is False
    assert Decimal(body["plan_total"]) == Decimal(0)
    assert body["accuracy"] is None


def test_plan_suggestions_use_average_of_previous_months(
    client: TestClient, db: Session, user: User
):
    add_tx(db, user, "2026-05-10", "Продукты", "300")
    add_tx(db, user, "2026-06-10", "Продукты", "300")
    add_tx(db, user, "2026-07-10", "Продукты", "300")

    body = client.get("/api/plans/2026-08/suggestions", params={"window": 3}).json()

    assert body[0]["title"] == "Продукты"
    assert Decimal(body[0]["amount"]) == Decimal("300")


# --- динамика и сравнение -------------------------------------------------


def test_category_dynamics_returns_requested_window(client: TestClient, db: Session, user: User):
    add_tx(db, user, "2026-06-01", "Кофе", "20")
    add_tx(db, user, "2026-07-01", "Кофе", "30")

    body = client.get(
        "/api/stats/category-dynamics",
        params={"category": "Кофе", "months": 3, "until": "2026-07"},
    ).json()

    assert [point["month"] for point in body["points"]] == ["2026-05", "2026-06", "2026-07"]
    assert Decimal(body["total"]) == Decimal("50")
    assert Decimal(body["delta_pct"]) == Decimal("0.5")


def test_compare_two_months(client: TestClient, db: Session, user: User):
    add_tx(db, user, "2026-06-01", "Кофе", "100")
    add_tx(db, user, "2026-07-01", "Кофе", "150")

    body = client.get("/api/stats/compare", params={"a": "2026-06", "b": "2026-07"}).json()

    assert Decimal(body["total_a"]) == Decimal("100")
    assert Decimal(body["total_b"]) == Decimal("150")
    assert Decimal(body["categories"][0]["diff"]) == Decimal("50")


# --- импорт ---------------------------------------------------------------


def test_csv_upload_imports_rows(client: TestClient):
    content = (
        "﻿"
        + HEADER
        + "\n"
        + '2026-07-01;"Кофе";;;"Сербия ";"10";USD;"Сербия ";"0";USD;'
        '"2026-07-01 09:00:00";"2026-07-01 09:00:00";\n'
    ).encode()

    response = client.post(
        "/api/import/csv", files={"file": ("zen.csv", content, "text/csv")}
    )

    assert response.status_code == 200
    assert response.json()["rows_new"] == 1


def test_upload_of_foreign_file_returns_400(client: TestClient):
    response = client.post(
        "/api/import/csv", files={"file": ("notes.txt", b"just text", "text/plain")}
    )

    assert response.status_code == 400


def test_empty_upload_returns_400(client: TestClient):
    response = client.post("/api/import/csv", files={"file": ("zen.csv", b"", "text/csv")})

    assert response.status_code == 400


def test_backfill_reports_counts(client: TestClient, db: Session, user: User):
    body = client.post("/api/fx/backfill").json()

    assert body == {"filled": 0, "pending_left": 0}
