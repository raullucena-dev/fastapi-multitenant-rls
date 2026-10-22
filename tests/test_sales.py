import threading
from collections.abc import Callable
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.main import app
from app.models import Product, Role, Sale, SaleItem, StockMovement
from tests.conftest import TenantCtx, create_product, login


def test_sale_decrements_stock_and_records_ledger(
    tenant_a: TenantCtx, owner_session: Callable[[int], Session]
) -> None:
    p1 = create_product(tenant_a.admin, sku="P1", price="2.50", stock=10)
    p2 = create_product(tenant_a.admin, sku="P2", price="4.00", stock=3)

    response = tenant_a.cashier.post(
        "/sales",
        json={
            "items": [
                {"product_id": p1["id"], "quantity": 4},
                {"product_id": p2["id"], "quantity": 3},
            ]
        },
    )
    assert response.status_code == 201, response.text
    sale = response.json()
    assert Decimal(sale["total"]) == Decimal("22.00")
    assert sale["cashier_id"] == tenant_a.users["cashier"].id
    assert {(i["product_id"], i["quantity"], i["line_total"]) for i in sale["items"]} == {
        (p1["id"], 4, "10.00"),
        (p2["id"], 3, "12.00"),
    }

    db = owner_session(tenant_a.id)
    assert db.get(Product, p1["id"]).stock_quantity == 6  # type: ignore[union-attr]
    assert db.get(Product, p2["id"]).stock_quantity == 0  # type: ignore[union-attr]
    sale_movements = db.scalars(
        select(StockMovement).where(StockMovement.sale_id == sale["id"])
    ).all()
    assert sorted(m.quantity for m in sale_movements) == [-4, -3]

    fetched = tenant_a.cashier.get(f"/sales/{sale['id']}").json()
    assert fetched == sale


def test_insufficient_stock_rolls_back_whole_sale(
    tenant_a: TenantCtx, owner_session: Callable[[int], Session]
) -> None:
    p1 = create_product(tenant_a.admin, sku="P1", stock=10)
    p2 = create_product(tenant_a.admin, sku="P2", stock=1)

    response = tenant_a.cashier.post(
        "/sales",
        json={
            "items": [
                {"product_id": p1["id"], "quantity": 5},
                {"product_id": p2["id"], "quantity": 2},
            ]
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_stock"

    db = owner_session(tenant_a.id)
    assert db.get(Product, p1["id"]).stock_quantity == 10  # type: ignore[union-attr]
    assert db.scalar(select(func.count()).select_from(Sale)) == 0
    assert db.scalar(select(func.count()).select_from(SaleItem)) == 0


def test_sale_validation(tenant_a: TenantCtx) -> None:
    p1 = create_product(tenant_a.admin, sku="P1", stock=10)
    bad = [
        {"items": []},
        {"items": [{"product_id": p1["id"], "quantity": 0}]},
        {
            "items": [
                {"product_id": p1["id"], "quantity": 1},
                {"product_id": p1["id"], "quantity": 1},
            ]
        },
    ]
    for body in bad:
        assert tenant_a.cashier.post("/sales", json=body).status_code == 422, body


def test_cannot_sell_deleted_or_unknown_product(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.admin, sku="P1", stock=10)
    tenant_a.admin.delete(f"/products/{product['id']}")  # soft (has stock history)
    for pid in (product["id"], 424242):
        response = tenant_a.cashier.post(
            "/sales", json={"items": [{"product_id": pid, "quantity": 1}]}
        )
        assert response.status_code == 404


def test_manager_can_record_sale_for_cashier(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.admin, sku="P1", stock=10)
    cashier_id = tenant_a.users["cashier"].id
    response = tenant_a.manager.post(
        "/sales",
        json={"items": [{"product_id": product["id"], "quantity": 1}], "cashier_id": cashier_id},
    )
    assert response.status_code == 201
    assert response.json()["cashier_id"] == cashier_id


def test_cashier_cannot_attribute_sale_to_someone_else(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.admin, sku="P1", stock=10)
    response = tenant_a.cashier.post(
        "/sales",
        json={
            "items": [{"product_id": product["id"], "quantity": 1}],
            "cashier_id": tenant_a.users["manager"].id,
        },
    )
    assert response.status_code == 403
    own = tenant_a.cashier.post(
        "/sales",
        json={
            "items": [{"product_id": product["id"], "quantity": 1}],
            "cashier_id": tenant_a.users["cashier"].id,
        },
    )
    assert own.status_code == 201


def test_unknown_cashier_id_returns_404(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.admin, sku="P1", stock=10)
    response = tenant_a.manager.post(
        "/sales",
        json={"items": [{"product_id": product["id"], "quantity": 1}], "cashier_id": 987654},
    )
    assert response.status_code == 404


def test_cashier_sees_only_own_sales(
    tenant_a: TenantCtx, create_user: Callable[..., object]
) -> None:
    product = create_product(tenant_a.admin, sku="P1", stock=10)
    create_user(tenant_a.id, "alpha-cashier2", Role.CASHIER)
    other = TestClient(app)
    login(other, "alpha-cashier2")
    mine = tenant_a.cashier.post(
        "/sales", json={"items": [{"product_id": product["id"], "quantity": 1}]}
    ).json()
    theirs = other.post(
        "/sales", json={"items": [{"product_id": product["id"], "quantity": 1}]}
    ).json()

    assert [s["id"] for s in tenant_a.cashier.get("/sales").json()] == [mine["id"]]
    assert tenant_a.cashier.get(f"/sales/{theirs['id']}").status_code == 404
    assert {s["id"] for s in tenant_a.manager.get("/sales").json()} == {mine["id"], theirs["id"]}


def test_concurrent_sales_never_oversell(
    tenant_a: TenantCtx, owner_session: Callable[[int], Session]
) -> None:
    """Row locks make the stock check and decrement atomic across transactions."""
    product = create_product(tenant_a.admin, sku="LAST", stock=3)
    clients = [tenant_a.cashier, tenant_a.manager, tenant_a.admin] * 2
    barrier = threading.Barrier(len(clients))
    statuses: list[int] = []
    lock = threading.Lock()

    def sell(client: TestClient) -> None:
        barrier.wait()
        response = client.post(
            "/sales", json={"items": [{"product_id": product["id"], "quantity": 1}]}
        )
        with lock:
            statuses.append(response.status_code)

    threads = [threading.Thread(target=sell, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(statuses) == [201, 201, 201, 409, 409, 409]
    db = owner_session(tenant_a.id)
    assert db.get(Product, product["id"]).stock_quantity == 0  # type: ignore[union-attr]
