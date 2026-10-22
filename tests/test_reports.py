from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Sale
from tests.conftest import TenantCtx, create_product


def _sell(tenant: TenantCtx, product_id: int, quantity: int) -> int:
    response = tenant.cashier.post(
        "/sales", json={"items": [{"product_id": product_id, "quantity": quantity}]}
    )
    assert response.status_code == 201
    sale_id: int = response.json()["id"]
    return sale_id


def test_sales_report_by_date_range(
    tenant_a: TenantCtx, owner_session: Callable[[int], Session]
) -> None:
    hammer = create_product(tenant_a.admin, sku="HAMMER", price="10.00", stock=100)
    nails = create_product(tenant_a.admin, sku="NAILS", price="0.50", stock=100)
    old = _sell(tenant_a, hammer["id"], 1)
    _sell(tenant_a, hammer["id"], 2)
    _sell(tenant_a, nails["id"], 10)

    # Move one sale to three days ago so it falls outside the default window.
    db = owner_session(tenant_a.id)
    three_days_ago = datetime.now(UTC) - timedelta(days=3)
    db.execute(update(Sale).where(Sale.id == old).values(created_at=three_days_ago))
    db.commit()

    today = datetime.now(UTC).date()
    report = tenant_a.manager.get(
        "/reports/sales", params={"date_from": today.isoformat(), "date_to": today.isoformat()}
    ).json()
    assert report["sales_count"] == 2
    assert report["revenue"] == "25.00"
    assert report["items_sold"] == 12
    assert report["daily"] == [{"day": today.isoformat(), "sales_count": 2, "revenue": "25.00"}]
    assert [(p["sku"], p["quantity"]) for p in report["top_products"]] == [
        ("NAILS", 10),
        ("HAMMER", 2),
    ]

    wide = tenant_a.manager.get(
        "/reports/sales",
        params={"date_from": (today - timedelta(days=7)).isoformat(), "date_to": today.isoformat()},
    ).json()
    assert wide["sales_count"] == 3
    assert len(wide["daily"]) == 2
    assert wide["revenue"] == "35.00"


def test_report_filtered_by_cashier(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.admin, sku="P", price="1.00", stock=10)
    _sell(tenant_a, product["id"], 1)
    today = datetime.now(UTC).date().isoformat()
    params = {"date_from": today, "date_to": today}
    mine = tenant_a.manager.get(
        "/reports/sales", params={**params, "cashier_id": tenant_a.users["cashier"].id}
    ).json()
    assert mine["sales_count"] == 1
    nobody = tenant_a.manager.get(
        "/reports/sales", params={**params, "cashier_id": tenant_a.users["manager"].id}
    ).json()
    assert nobody["sales_count"] == 0
    assert nobody["revenue"] == "0"


def test_report_range_validation(tenant_a: TenantCtx) -> None:
    inverted = tenant_a.manager.get(
        "/reports/sales", params={"date_from": "2026-02-01", "date_to": "2026-01-01"}
    )
    assert inverted.status_code == 400
    too_wide = tenant_a.manager.get(
        "/reports/sales", params={"date_from": "2024-01-01", "date_to": "2026-01-01"}
    )
    assert too_wide.status_code == 400
    missing = tenant_a.manager.get("/reports/sales")
    assert missing.status_code == 422
