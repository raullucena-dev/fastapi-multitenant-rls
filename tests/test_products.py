from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Product
from tests.conftest import TenantCtx, create_product


def test_product_crud_roundtrip(tenant_a: TenantCtx) -> None:
    created = create_product(tenant_a.manager, sku="TOOL-1", price="19.99")
    assert created["stock_quantity"] == 0
    assert created["price"] == "19.99"

    fetched = tenant_a.cashier.get(f"/products/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["sku"] == "TOOL-1"

    updated = tenant_a.manager.patch(
        f"/products/{created['id']}", json={"name": "Hammer", "price": "21.50"}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Hammer"
    assert updated.json()["price"] == "21.50"

    deleted = tenant_a.admin.delete(f"/products/{created['id']}")
    assert deleted.json() == {"id": created["id"], "mode": "hard"}
    assert tenant_a.admin.get(f"/products/{created['id']}").status_code == 404


def test_list_and_search_products(tenant_a: TenantCtx) -> None:
    create_product(tenant_a.admin, sku="NAIL-1")
    create_product(tenant_a.admin, sku="SCREW-1")
    assert len(tenant_a.cashier.get("/products").json()) == 2
    found = tenant_a.cashier.get("/products", params={"q": "screw"}).json()
    assert [p["sku"] for p in found] == ["SCREW-1"]
    page = tenant_a.cashier.get("/products", params={"limit": 1, "offset": 1}).json()
    assert [p["sku"] for p in page] == ["SCREW-1"]


def test_duplicate_sku_conflicts_within_tenant_only(
    tenant_a: TenantCtx, tenant_b: TenantCtx
) -> None:
    create_product(tenant_a.admin, sku="DUP")
    again = tenant_a.admin.post("/products", json={"sku": "DUP", "name": "x", "price": "1"})
    assert again.status_code == 409
    # Same SKU in another tenant is fine.
    create_product(tenant_b.admin, sku="DUP")


def test_update_to_existing_sku_conflicts(tenant_a: TenantCtx) -> None:
    create_product(tenant_a.admin, sku="ONE")
    two = create_product(tenant_a.admin, sku="TWO")
    response = tenant_a.admin.patch(f"/products/{two['id']}", json={"sku": "ONE"})
    assert response.status_code == 409


def test_product_with_sales_is_soft_deleted(
    tenant_a: TenantCtx, owner_session: Callable[[int], Session]
) -> None:
    product = create_product(tenant_a.admin, sku="SOLD", stock=3)
    tenant_a.cashier.post("/sales", json={"items": [{"product_id": product["id"], "quantity": 1}]})

    response = tenant_a.admin.delete(f"/products/{product['id']}")
    assert response.json() == {"id": product["id"], "mode": "soft"}
    assert tenant_a.admin.get(f"/products/{product['id']}").status_code == 404
    assert tenant_a.admin.get("/products").json() == []

    db = owner_session(tenant_a.id)
    row = db.get(Product, product["id"])
    assert row is not None
    assert row.deleted_at is not None
    # Sale history still resolves.
    sales = tenant_a.admin.get("/sales").json()
    assert tenant_a.admin.get(f"/sales/{sales[0]['id']}").status_code == 200

    # The SKU becomes reusable once the old product is soft-deleted.
    create_product(tenant_a.admin, sku="SOLD")
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_type == "product")).all()
    assert actions.count("product.delete") == 1


def test_product_with_stock_history_is_soft_deleted(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.admin, sku="STOCKED", stock=2)
    response = tenant_a.admin.delete(f"/products/{product['id']}")
    assert response.json()["mode"] == "soft"


def test_deleted_product_cannot_be_deleted_again(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.admin, sku="GONE")
    assert tenant_a.admin.delete(f"/products/{product['id']}").status_code == 200
    assert tenant_a.admin.delete(f"/products/{product['id']}").status_code == 404


def test_product_validation(tenant_a: TenantCtx) -> None:
    bad = [
        {"sku": "", "name": "x", "price": "1"},
        {"sku": "has space", "name": "x", "price": "1"},
        {"sku": "OK", "name": "x", "price": "-1"},
        {"sku": "OK", "name": "x", "price": "1.234"},
        {"sku": "OK", "name": "", "price": "1"},
    ]
    for body in bad:
        assert tenant_a.admin.post("/products", json=body).status_code == 422, body
