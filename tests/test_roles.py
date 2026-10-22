"""Server-side role checks: every privileged endpoint rejects lower roles with 403."""

import pytest

from tests.conftest import TenantCtx, create_product

ADMIN_ONLY = [
    (
        "post",
        "/users",
        {"username": "new-user", "full_name": "N", "password": "x" * 10, "role": "cashier"},
    ),
    ("patch", "/users/{user}", {"full_name": "Renamed"}),
    ("delete", "/products/{product}", None),
    ("get", "/audit-log", None),
]
MANAGER_AND_UP = [
    ("get", "/users", None),
    ("get", "/users/{user}", None),
    ("post", "/products", {"sku": "R-1", "name": "Role test", "price": "1.00"}),
    ("patch", "/products/{product}", {"name": "Renamed"}),
    ("get", "/stock-movements", None),
    ("post", "/stock-movements", {"product_id": "{product}", "quantity": 1, "reason": "purchase"}),
    ("delete", "/stock-movements/1", None),
    ("get", "/reports/sales?date_from=2026-01-01&date_to=2026-01-31", None),
]


def _call(tenant: TenantCtx, role: str, method: str, path: str, body: object) -> int:
    ids = {"user": tenant.users["cashier"].id, "product": tenant.extra["product"]}
    url = path.format(**ids)
    if isinstance(body, dict):
        body = {k: (ids["product"] if v == "{product}" else v) for k, v in body.items()}
    client = tenant.clients[role]
    kwargs = {"json": body} if body is not None else {}
    return client.request(method.upper(), url, **kwargs).status_code


@pytest.fixture
def tenant(tenant_a: TenantCtx) -> TenantCtx:
    tenant_a.extra["product"] = create_product(tenant_a.admin)["id"]
    return tenant_a


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ONLY)
@pytest.mark.parametrize("role", ["manager", "cashier"])
def test_admin_only_endpoints_forbid_other_roles(
    tenant: TenantCtx, role: str, method: str, path: str, body: object
) -> None:
    assert _call(tenant, role, method, path, body) == 403


@pytest.mark.parametrize(("method", "path", "body"), MANAGER_AND_UP)
def test_manager_endpoints_forbid_cashier(
    tenant: TenantCtx, method: str, path: str, body: object
) -> None:
    assert _call(tenant, "cashier", method, path, body) == 403


@pytest.mark.parametrize(("method", "path", "body"), MANAGER_AND_UP)
def test_manager_endpoints_allow_manager(
    tenant: TenantCtx, method: str, path: str, body: object
) -> None:
    assert _call(tenant, "manager", method, path, body) not in (401, 403)


def test_forbidden_response_shape(tenant: TenantCtx) -> None:
    response = tenant.cashier.get("/audit-log")
    assert response.json() == {
        "error": {"code": "forbidden", "message": "insufficient role for this operation"}
    }
