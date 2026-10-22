from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.security import (
    SessionCookie,
    decode_session_cookie,
    encode_session_cookie,
    verify_password,
)
from app.main import app
from app.models import AuditLog, Role, User
from tests.conftest import PASSWORD, TenantCtx, login

COOKIE = get_settings().session_cookie_name


def test_login_sets_hardened_session_cookie(tenant_a: TenantCtx, client: TestClient) -> None:
    response = login(client, "alpha-admin")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alpha-admin"
    assert body["tenant_id"] == tenant_a.id
    assert "password_hash" not in body

    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


def test_username_is_case_insensitive(tenant_a: TenantCtx, client: TestClient) -> None:
    assert login(client, "  ALPHA-Admin ").status_code == 200


@pytest.mark.parametrize(
    ("username", "password"),
    [("alpha-admin", "wrong-password"), ("nobody-here", PASSWORD)],
)
def test_bad_credentials_get_identical_401(
    tenant_a: TenantCtx, client: TestClient, username: str, password: str
) -> None:
    response = login(client, username, password)
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "not_authenticated", "message": "invalid username or password"}
    }


def test_requests_without_session_are_rejected(client: TestClient) -> None:
    assert client.get("/auth/me").status_code == 401
    assert client.get("/products").status_code == 401


def test_lockout_after_five_failures_for_fifteen_minutes(
    tenant_a: TenantCtx, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    statuses = [login(client, "alpha-cashier", "wrong").status_code for _ in range(5)]
    assert statuses == [401, 401, 401, 401, 423]

    # Locked: even the correct password is refused.
    assert login(client, "alpha-cashier").status_code == 423

    # Still locked just before the window ends.
    real_now = clock.utcnow()
    monkeypatch.setattr(clock, "utcnow", lambda: real_now + timedelta(minutes=14, seconds=50))
    assert login(client, "alpha-cashier").status_code == 423

    monkeypatch.setattr(clock, "utcnow", lambda: real_now + timedelta(minutes=15, seconds=5))
    assert login(client, "alpha-cashier").status_code == 200


def test_successful_login_resets_failure_counter(tenant_a: TenantCtx, client: TestClient) -> None:
    for _ in range(4):
        assert login(client, "alpha-manager", "wrong").status_code == 401
    assert login(client, "alpha-manager").status_code == 200
    # Counter reset: four more failures do not lock the account.
    for _ in range(4):
        assert login(client, "alpha-manager", "wrong").status_code == 401
    assert login(client, "alpha-manager").status_code == 200


def test_lockout_is_audited(
    tenant_a: TenantCtx, client: TestClient, owner_session: Callable[[int], Session]
) -> None:
    for _ in range(5):
        login(client, "alpha-cashier", "wrong")
    actions = owner_session(tenant_a.id).scalars(select(AuditLog.action)).all()
    assert actions.count("auth.login_failed") == 4
    assert "auth.account_locked" in actions


def test_logout_revokes_server_side_session(tenant_a: TenantCtx) -> None:
    client = tenant_a.manager
    stolen_cookie = client.cookies[COOKIE]
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401

    # Replaying the old cookie value is useless once the session is revoked.
    replay = TestClient(app, cookies={COOKIE: stolen_cookie})
    assert replay.get("/auth/me").status_code == 401


def test_tampered_cookie_is_rejected(tenant_a: TenantCtx) -> None:
    cookie = tenant_a.admin.cookies[COOKIE]
    forged = TestClient(app, cookies={COOKIE: cookie[:-2] + "xx"})
    assert forged.get("/auth/me").status_code == 401


def test_cookie_signed_with_another_key_cannot_choose_tenant(
    tenant_a: TenantCtx, tenant_b: TenantCtx
) -> None:
    payload = {"sid": "whatever", "tid": tenant_b.id}
    forged_value = URLSafeTimedSerializer("attacker-key", salt="session-cookie-v1").dumps(payload)
    forged = TestClient(app, cookies={COOKIE: forged_value})
    assert forged.get("/auth/me").status_code == 401


def test_valid_token_with_swapped_tenant_finds_no_session(
    tenant_a: TenantCtx, tenant_b: TenantCtx
) -> None:
    """Even a correctly signed cookie cannot cross tenants: RLS hides the session row."""
    original = decode_session_cookie(tenant_a.admin.cookies[COOKIE], 3600)
    assert original is not None
    swapped = encode_session_cookie(SessionCookie(token=original.token, tenant_id=tenant_b.id))
    attacker = TestClient(app, cookies={COOKIE: swapped})
    assert attacker.get("/auth/me").status_code == 401


def test_expired_session_is_rejected(tenant_a: TenantCtx, monkeypatch: pytest.MonkeyPatch) -> None:
    later = datetime.now(UTC) + timedelta(minutes=get_settings().session_ttl_minutes + 1)
    monkeypatch.setattr(clock, "utcnow", lambda: later)
    assert tenant_a.admin.get("/auth/me").status_code == 401


def test_inactive_user_cannot_log_in_and_loses_sessions(
    tenant_a: TenantCtx, client: TestClient
) -> None:
    cashier_id = tenant_a.users["cashier"].id
    response = tenant_a.admin.patch(f"/users/{cashier_id}", json={"is_active": False})
    assert response.status_code == 200
    assert tenant_a.cashier.get("/auth/me").status_code == 401
    assert login(client, "alpha-cashier").status_code == 401


def test_inactive_user_with_live_session_is_rejected(
    tenant_a: TenantCtx, owner_session: Callable[[int], Session]
) -> None:
    db = owner_session(tenant_a.id)
    user = db.get(User, tenant_a.users["manager"].id)
    assert user is not None
    user.is_active = False  # bypasses the API, so sessions are not revoked
    db.commit()
    assert tenant_a.manager.get("/auth/me").status_code == 401


def test_password_hashes_are_bcrypt(create_user: Callable[..., User], tenant_a: TenantCtx) -> None:
    user = create_user(tenant_a.id, "hash-check", Role.CASHIER, password="s3cret-pass")
    assert user.password_hash.startswith("$2b$")
    assert verify_password("s3cret-pass", user.password_hash)
    assert not verify_password("other", user.password_hash)
    assert not verify_password("x", "not-a-bcrypt-hash")


@pytest.mark.parametrize(
    "value", ["", "garbage", URLSafeTimedSerializer("k").dumps({"sid": 1, "tid": "x"})]
)
def test_decode_rejects_malformed_cookies(value: str) -> None:
    assert decode_session_cookie(value, 60) is None


def test_decode_rejects_non_dict_and_bool_tenant() -> None:
    serializer = URLSafeTimedSerializer(
        get_settings().secret_key.get_secret_value(), salt="session-cookie-v1"
    )
    assert decode_session_cookie(serializer.dumps(["a", 1]), 60) is None
    assert decode_session_cookie(serializer.dumps({"sid": "a", "tid": True}), 60) is None
    assert decode_session_cookie(serializer.dumps({"sid": "a", "tid": 3}), 60) == SessionCookie(
        "a", 3
    )
