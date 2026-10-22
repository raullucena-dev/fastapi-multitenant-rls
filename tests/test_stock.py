from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StockMovement
from tests.conftest import TenantCtx, create_product


def _move(tenant: TenantCtx, product_id: int, quantity: int, reason: str = "purchase") -> int:
    response = tenant.manager.post(
        "/stock-movements",
        json={"product_id": product_id, "quantity": quantity, "reason": reason, "note": "test"},
    )
    return response.status_code


def _stock(tenant: TenantCtx, product_id: int) -> int:
    quantity: int = tenant.manager.get(f"/products/{product_id}").json()["stock_quantity"]
    return quantity


def test_purchase_and_adjustment_update_stock(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.admin)
    assert _move(tenant_a, product["id"], 10) == 201
    assert _move(tenant_a, product["id"], -3, "adjustment") == 201
    assert _stock(tenant_a, product["id"]) == 7

    movements = tenant_a.manager.get(
        "/stock-movements", params={"product_id": product["id"]}
    ).json()
    assert [m["quantity"] for m in movements] == [-3, 10]
    assert all(m["created_by_id"] == tenant_a.users["manager"].id for m in movements)


def test_adjustment_cannot_make_stock_negative(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.admin, stock=2)
    response = tenant_a.manager.post(
        "/stock-movements",
        json={"product_id": product["id"], "quantity": -5, "reason": "adjustment"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_stock"
    assert _stock(tenant_a, product["id"]) == 2


def test_invalid_movements_are_rejected(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.admin)
    assert _move(tenant_a, product["id"], 0, "adjustment") == 422
    assert _move(tenant_a, product["id"], -1, "purchase") == 422
    assert _move(tenant_a, product["id"], 1, "sale") == 422
    assert _move(tenant_a, 999_999, 1) == 404


def test_delete_reverses_movement_with_compensating_entry(
    tenant_a: TenantCtx, owner_session: Callable[[int], Session]
) -> None:
    product = create_product(tenant_a.admin)
    movement = tenant_a.manager.post(
        "/stock-movements", json={"product_id": product["id"], "quantity": 8, "reason": "purchase"}
    ).json()

    reversal = tenant_a.manager.delete(f"/stock-movements/{movement['id']}")
    assert reversal.status_code == 200
    body = reversal.json()
    assert body["reason"] == "reversal"
    assert body["quantity"] == -8
    assert body["reversal_of_id"] == movement["id"]
    assert _stock(tenant_a, product["id"]) == 0

    db = owner_session(tenant_a.id)
    original = db.get(StockMovement, movement["id"])
    assert original is not None
    assert original.reversed_at is not None  # ledger kept, not deleted
    assert len(db.scalars(select(StockMovement)).all()) == 2


def test_movement_can_only_be_reversed_once(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.admin)
    movement = tenant_a.manager.post(
        "/stock-movements", json={"product_id": product["id"], "quantity": 1, "reason": "purchase"}
    ).json()
    assert tenant_a.manager.delete(f"/stock-movements/{movement['id']}").status_code == 200
    again = tenant_a.manager.delete(f"/stock-movements/{movement['id']}")
    assert again.status_code == 409


def test_reversal_and_sale_movements_cannot_be_reversed(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.admin, stock=5)
    tenant_a.cashier.post("/sales", json={"items": [{"product_id": product["id"], "quantity": 1}]})
    movements = tenant_a.manager.get("/stock-movements").json()
    sale_movement = next(m for m in movements if m["reason"] == "sale")
    assert tenant_a.manager.delete(f"/stock-movements/{sale_movement['id']}").status_code == 409

    purchase = next(m for m in movements if m["reason"] == "purchase")
    reversal = tenant_a.manager.delete(f"/stock-movements/{purchase['id']}")
    assert reversal.status_code == 409  # 5 - 1 sold = 4 left; reversing +5 would go negative

    extra = tenant_a.manager.post(
        "/stock-movements", json={"product_id": product["id"], "quantity": 2, "reason": "purchase"}
    ).json()
    rev = tenant_a.manager.delete(f"/stock-movements/{extra['id']}").json()
    assert tenant_a.manager.delete(f"/stock-movements/{rev['id']}").status_code == 409


def test_reversing_unknown_movement_returns_404(tenant_a: TenantCtx) -> None:
    assert tenant_a.manager.delete("/stock-movements/123456").status_code == 404
