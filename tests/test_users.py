from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models import AuditLog
from tests.conftest import TenantCtx, login


def _new_user(username: str = "new.cashier", role: str = "cashier") -> dict[str, str]:
    return {
        "username": username,
        "full_name": "New Person",
        "password": "long-enough-pw",
        "role": role,
    }


def test_admin_creates_user_in_own_tenant(tenant_a: TenantCtx, client: object) -> None:
    response = tenant_a.admin.post("/users", json=_new_user())
    assert response.status_code == 201
    body = response.json()
    assert body["tenant_id"] == tenant_a.id
    assert body["role"] == "cashier"
    assert "password" not in body
    assert "password_hash" not in body

    fresh = TestClient(app)
    assert login(fresh, "new.cashier", "long-enough-pw").status_code == 200
    assert fresh.get("/auth/me").json()["tenant_id"] == tenant_a.id


def test_usernames_are_globally_unique(tenant_a: TenantCtx, tenant_b: TenantCtx) -> None:
    """Single login domain: a username taken in another tenant is a conflict (ADR 0004)."""
    response = tenant_a.admin.post("/users", json=_new_user(username="beta-cashier"))
    assert response.status_code == 409


def test_user_validation(tenant_a: TenantCtx) -> None:
    for body in (
        _new_user(username="x"),
        _new_user(role="superadmin"),
        {**_new_user(), "password": "short"},
    ):
        assert tenant_a.admin.post("/users", json=body).status_code == 422


def test_list_and_get_users(tenant_a: TenantCtx) -> None:
    users = tenant_a.manager.get("/users").json()
    assert {u["username"] for u in users} == {"alpha-admin", "alpha-manager", "alpha-cashier"}
    one = tenant_a.manager.get(f"/users/{tenant_a.users['cashier'].id}")
    assert one.json()["username"] == "alpha-cashier"
    assert tenant_a.manager.get("/users/999999").status_code == 404


def test_admin_updates_role_and_name(
    tenant_a: TenantCtx, owner_session: Callable[[int], Session]
) -> None:
    cashier_id = tenant_a.users["cashier"].id
    response = tenant_a.admin.patch(
        f"/users/{cashier_id}", json={"role": "manager", "full_name": "Promoted"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "manager"
    # Role is re-read on every request, so the promotion applies immediately.
    assert tenant_a.cashier.get("/stock-movements").status_code == 200

    entry = (
        owner_session(tenant_a.id)
        .scalars(select(AuditLog).where(AuditLog.action == "user.update"))
        .one()
    )
    assert entry.details["role"] == "manager"
    assert entry.actor_user_id == tenant_a.users["admin"].id


def test_password_change_revokes_sessions(tenant_a: TenantCtx, client: object) -> None:
    manager_id = tenant_a.users["manager"].id
    response = tenant_a.admin.patch(f"/users/{manager_id}", json={"password": "brand-new-pass"})
    assert response.status_code == 200
    assert tenant_a.manager.get("/auth/me").status_code == 401
    assert login(tenant_a.manager, "alpha-manager", "brand-new-pass").status_code == 200


def test_admin_cannot_lock_themselves_out(tenant_a: TenantCtx) -> None:
    admin_id = tenant_a.users["admin"].id
    assert tenant_a.admin.patch(f"/users/{admin_id}", json={"is_active": False}).status_code == 409
    assert tenant_a.admin.patch(f"/users/{admin_id}", json={"role": "cashier"}).status_code == 409
    assert tenant_a.admin.patch(f"/users/{admin_id}", json={"full_name": "Me"}).status_code == 200
