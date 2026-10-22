"""Tenant A must not be able to read, change or delete tenant B data via any endpoint.

Every cross-tenant access returns 404 (never 403), so the API does not even
confirm that the foreign id exists.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditLog, Product, Sale, StockMovement, User
from tests.conftest import TenantCtx, create_product


@pytest.fixture
def victim(tenant_b: TenantCtx) -> dict[str, Any]:
    """Tenant B data: a product with stock, a stock movement and a sale."""
    product = create_product(tenant_b.admin, sku="B-1", price="5.00", stock=10)
    movement = tenant_b.manager.post(
        "/stock-movements",
        json={"product_id": product["id"], "quantity": 3, "reason": "purchase"},
    ).json()
    sale = tenant_b.cashier.post(
        "/sales", json={"items": [{"product_id": product["id"], "quantity": 2}]}
    ).json()
    return {
        "product": product["id"],
        "movement": movement["id"],
        "sale": sale["id"],
        "user": tenant_b.users["cashier"].id,
    }


CROSS_TENANT_REQUESTS = [
    ("get", "/products/{product}", None),
    ("patch", "/products/{product}", {"name": "pwned", "price": "0.01"}),
    ("delete", "/products/{product}", None),
    ("delete", "/stock-movements/{movement}", None),
    ("get", "/sales/{sale}", None),
    ("get", "/users/{user}", None),
    ("patch", "/users/{user}", {"role": "admin", "is_active": False}),
]


@pytest.mark.parametrize(("method", "path", "body"), CROSS_TENANT_REQUESTS)
def test_cross_tenant_access_by_id_returns_404(
    tenant_a: TenantCtx, victim: dict[str, Any], method: str, path: str, body: object
) -> None:
    kwargs = {"json": body} if body is not None else {}
    response = tenant_a.admin.request(method.upper(), path.format(**victim), **kwargs)
    assert response.status_code == 404, response.text


def test_cross_tenant_writes_leave_victim_data_untouched(
    tenant_a: TenantCtx,
    tenant_b: TenantCtx,
    victim: dict[str, Any],
    owner_session: Callable[[int], Session],
) -> None:
    for method, path, body in CROSS_TENANT_REQUESTS:
        kwargs = {"json": body} if body is not None else {}
        tenant_a.admin.request(method.upper(), path.format(**victim), **kwargs)

    db = owner_session(tenant_b.id)
    product = db.get(Product, victim["product"])
    assert product is not None
    assert product.name == "Product B-1"
    assert product.deleted_at is None
    assert product.stock_quantity == 11
    movement = db.get(StockMovement, victim["movement"])
    assert movement is not None
    assert movement.reversed_at is None
    cashier = db.get(User, victim["user"])
    assert cashier is not None
    assert (cashier.role, cashier.is_active) == ("cashier", True)


@pytest.mark.parametrize(
    "path", ["/products", "/stock-movements", "/sales", "/users", "/audit-log"]
)
def test_listings_only_contain_own_tenant_rows(
    tenant_a: TenantCtx, tenant_b: TenantCtx, victim: dict[str, Any], path: str
) -> None:
    own = create_product(tenant_a.admin, sku="A-1", stock=1)
    tenant_a.cashier.post("/sales", json={"items": [{"product_id": own["id"], "quantity": 1}]})
    rows = tenant_a.admin.get(path).json()
    assert rows, "tenant A should see its own rows"
    own_ids = {u.id for u in tenant_a.users.values()}
    for row in rows:
        if path == "/users":
            assert row["tenant_id"] == tenant_a.id
        if path == "/products":
            assert row["sku"] != "B-1"
        if path == "/stock-movements":
            assert row["product_id"] != victim["product"]
        if path in ("/sales",):
            assert row["id"] != victim["sale"]
        if path == "/audit-log":
            assert row["actor_user_id"] in own_ids


def test_stock_movement_for_foreign_product_returns_404(
    tenant_a: TenantCtx, victim: dict[str, Any]
) -> None:
    response = tenant_a.manager.post(
        "/stock-movements",
        json={"product_id": victim["product"], "quantity": 100, "reason": "purchase"},
    )
    assert response.status_code == 404
    assert tenant_a.manager.get(f"/stock-movements?product_id={victim['product']}").json() == []


def test_sale_with_foreign_product_returns_404_and_changes_nothing(
    tenant_a: TenantCtx,
    tenant_b: TenantCtx,
    victim: dict[str, Any],
    owner_session: Callable[[int], Session],
) -> None:
    own = create_product(tenant_a.admin, sku="A-1", stock=5)
    response = tenant_a.cashier.post(
        "/sales",
        json={
            "items": [
                {"product_id": own["id"], "quantity": 1},
                {"product_id": victim["product"], "quantity": 1},
            ]
        },
    )
    assert response.status_code == 404

    assert tenant_a.admin.get(f"/products/{own['id']}").json()["stock_quantity"] == 5
    db_b = owner_session(tenant_b.id)
    product_b = db_b.get(Product, victim["product"])
    assert product_b is not None
    assert product_b.stock_quantity == 11


def test_sale_with_foreign_cashier_id_returns_404(
    tenant_a: TenantCtx, victim: dict[str, Any], owner_session: Callable[[int], Session]
) -> None:
    product = create_product(tenant_a.admin, sku="A-1", stock=5)
    response = tenant_a.manager.post(
        "/sales",
        json={
            "items": [{"product_id": product["id"], "quantity": 1}],
            "cashier_id": victim["user"],
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "user not found"
    db = owner_session(tenant_a.id)
    assert db.scalar(select(func.count()).select_from(Sale)) == 0


def test_report_filtered_by_foreign_cashier_returns_404(
    tenant_a: TenantCtx, victim: dict[str, Any]
) -> None:
    response = tenant_a.manager.get(
        "/reports/sales",
        params={"date_from": "2020-01-01", "date_to": "2020-12-31", "cashier_id": victim["user"]},
    )
    assert response.status_code == 404


def test_reports_only_aggregate_own_sales(tenant_a: TenantCtx, victim: dict[str, Any]) -> None:
    today = datetime.now(UTC).date().isoformat()
    report = tenant_a.manager.get(
        "/reports/sales", params={"date_from": today, "date_to": today}
    ).json()
    assert report["sales_count"] == 0
    assert report["top_products"] == []


def test_tenant_cannot_be_chosen_via_payload(
    tenant_a: TenantCtx, tenant_b: TenantCtx, owner_session: Callable[[int], Session]
) -> None:
    """Unknown fields such as tenant_id are ignored; the session decides the tenant."""
    response = tenant_a.admin.post(
        "/products",
        json={"sku": "SNEAKY", "name": "x", "price": "1.00", "tenant_id": tenant_b.id},
    )
    assert response.status_code == 201
    db_b = owner_session(tenant_b.id)
    assert db_b.scalars(select(Product).where(Product.sku == "SNEAKY")).first() is None


def test_tenant_header_or_query_has_no_effect(tenant_a: TenantCtx, tenant_b: TenantCtx) -> None:
    create_product(tenant_b.admin, sku="B-ONLY")
    response = tenant_a.admin.get(
        f"/products?tenant_id={tenant_b.id}", headers={"X-Tenant-ID": str(tenant_b.id)}
    )
    assert all(p["sku"] != "B-ONLY" for p in response.json())


def test_audit_entries_are_attributed_to_the_right_tenant(
    tenant_a: TenantCtx, tenant_b: TenantCtx, owner_session: Callable[[int], Session]
) -> None:
    create_product(tenant_a.admin, sku="A-AUD")
    db_b = owner_session(tenant_b.id)
    actions = db_b.scalars(select(AuditLog.action)).all()
    assert "product.create" not in actions
