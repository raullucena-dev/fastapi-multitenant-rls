from tests.conftest import TenantCtx, create_product


def test_write_operations_are_audited(tenant_a: TenantCtx) -> None:
    product = create_product(tenant_a.manager, sku="AUD-1", stock=4)
    tenant_a.cashier.post("/sales", json={"items": [{"product_id": product["id"], "quantity": 1}]})
    tenant_a.manager.patch(f"/products/{product['id']}", json={"price": "3.00"})

    entries = tenant_a.admin.get("/audit-log").json()
    actions = [e["action"] for e in entries]
    for expected in (
        "product.create",
        "stock.create",
        "sale.create",
        "product.update",
        "auth.login",
    ):
        assert expected in actions
    update = next(e for e in entries if e["action"] == "product.update")
    assert update["actor_user_id"] == tenant_a.users["manager"].id
    assert update["entity_id"] == product["id"]
    assert update["details"] == {"price": "3.00"}
    assert update["created_at"]


def test_audit_log_filter_by_entity_type(tenant_a: TenantCtx) -> None:
    create_product(tenant_a.manager, sku="AUD-2")
    entries = tenant_a.admin.get("/audit-log", params={"entity_type": "product"}).json()
    assert entries
    assert {e["entity_type"] for e in entries} == {"product"}
