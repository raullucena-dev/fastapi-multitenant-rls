"""DEMO_MODE: per-IP login throttling, rate limiting, /demo/info and reset-demo."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import cli
from app.core.config import INSECURE_DEV_SECRET, Settings, get_settings
from app.core.ratelimit import SlidingWindow
from app.demo import DEMO_PASSWORD, DEMO_STORES, reset_demo
from app.main import app
from app.models import Product, Sale, User
from app.services import auth as auth_service
from tests.conftest import PASSWORD, TenantCtx, login


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _fresh_limiters() -> Iterator[None]:
    app.state.rate_limiter.clear()
    auth_service._demo_failures = None
    yield
    app.state.rate_limiter.clear()
    auth_service._demo_failures = None


@pytest.fixture
def demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "demo_mode", True)


def client_from(ip: str) -> TestClient:
    return TestClient(app, client=(ip, 50000))


# --- Login throttling ------------------------------------------------------------------------


@pytest.mark.usefixtures("demo_mode")
def test_demo_lockout_throttles_only_the_offending_client(
    tenant_a: TenantCtx, owner_session: Callable[[int], Session]
) -> None:
    attacker = client_from("203.0.113.10")
    statuses = [login(attacker, "alpha-cashier", "wrong").status_code for _ in range(5)]
    assert statuses == [401, 401, 401, 401, 423]
    # Even the right password is refused from that address for now...
    assert login(attacker, "alpha-cashier").status_code == 423
    # ...but the shared demo account keeps working for everybody else.
    assert login(client_from("198.51.100.20"), "alpha-cashier").status_code == 200

    user = owner_session(tenant_a.id).get(User, tenant_a.users["cashier"].id)
    assert user is not None
    assert (user.failed_login_attempts, user.locked_until) == (0, None)


@pytest.mark.usefixtures("demo_mode")
def test_demo_lockout_expires_after_the_short_window(tenant_a: TenantCtx) -> None:
    fake = FakeClock()
    auth_service._demo_failures = SlidingWindow(get_settings().demo_lockout_seconds, clock=fake)
    client = client_from("203.0.113.11")
    for _ in range(5):
        login(client, "alpha-manager", "wrong")
    assert login(client, "alpha-manager").status_code == 423
    fake.now += get_settings().demo_lockout_seconds + 1
    assert login(client, "alpha-manager").status_code == 200


@pytest.mark.usefixtures("demo_mode")
def test_demo_lockout_also_counts_unknown_usernames(tenant_a: TenantCtx) -> None:
    client = client_from("203.0.113.12")
    for _ in range(5):
        login(client, "nobody", "x")
    assert login(client, "nobody", "x").status_code == 423


@pytest.mark.usefixtures("demo_mode")
def test_successful_login_clears_demo_failures(tenant_a: TenantCtx) -> None:
    client = client_from("203.0.113.13")
    for _ in range(4):
        login(client, "alpha-admin", "wrong")
    assert login(client, "alpha-admin").status_code == 200
    for _ in range(4):
        assert login(client, "alpha-admin", "wrong").status_code == 401


def test_normal_mode_still_locks_the_account_globally(tenant_a: TenantCtx) -> None:
    attacker = client_from("203.0.113.14")
    for _ in range(5):
        login(attacker, "alpha-cashier", "wrong")
    assert login(client_from("198.51.100.21"), "alpha-cashier", PASSWORD).status_code == 423


# --- Rate limiting ---------------------------------------------------------------------------


@pytest.fixture
def tight_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 3)
    monkeypatch.setattr(settings, "rate_limit_writes_per_minute", 2)


@pytest.mark.usefixtures("tight_limits")
def test_login_is_rate_limited_per_ip(tenant_a: TenantCtx) -> None:
    client = client_from("192.0.2.1")
    assert [login(client, "alpha-admin").status_code for _ in range(3)] == [200, 200, 200]
    limited = login(client, "alpha-admin")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1
    assert login(client_from("192.0.2.2"), "alpha-admin").status_code == 200


@pytest.mark.usefixtures("tight_limits")
def test_writes_are_rate_limited_but_reads_are_not(tenant_a: TenantCtx) -> None:
    client = client_from("192.0.2.3")
    login(client, "alpha-admin")
    body = {"name": "x", "price": "1.00"}
    codes = [
        client.post("/products", json={**body, "sku": f"RL-{i}"}).status_code for i in range(3)
    ]
    assert codes == [201, 201, 429]
    assert all(client.get("/products").status_code == 200 for _ in range(10))


def test_rate_limiting_is_off_by_default(tenant_a: TenantCtx) -> None:
    assert get_settings().rate_limiting is False
    client = client_from("192.0.2.4")
    assert all(login(client, "alpha-admin").status_code == 200 for _ in range(15))


def test_rate_limiting_follows_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(demo_mode=True).rate_limiting is True
    assert Settings(demo_mode=True, rate_limit_enabled=False).rate_limiting is False


def test_sliding_window_evicts_stale_keys() -> None:
    fake = FakeClock()
    window = SlidingWindow(10, clock=fake)
    assert window.hit("a", 1) is None
    assert window.hit("a", 1) == pytest.approx(10.0)
    assert window.retry_after("a") == pytest.approx(10.0)
    fake.now += 11
    assert window.retry_after("a") == 0.0
    assert window.count("a") == 0
    for i in range(10_002):
        window.add(f"k{i}")
    fake.now += 11
    window.add("fresh")
    assert window.count("k0") == 0
    window.clear("fresh")
    assert window.count("fresh") == 0


# --- /demo/info ------------------------------------------------------------------------------


def test_demo_info_is_hidden_outside_demo_mode(client: TestClient) -> None:
    assert client.get("/demo/info").status_code == 404


@pytest.mark.usefixtures("demo_mode")
def test_demo_info_lists_stores_and_credentials(client: TestClient) -> None:
    body = client.get("/demo/info").json()
    assert body["password"] == DEMO_PASSWORD
    assert [s["name"] for s in body["stores"]] == ["Demo Store North", "Demo Store South"]
    assert [u["username"] for u in body["stores"][0]["users"]] == [
        "north.admin",
        "north.manager",
        "north.cashier",
    ]
    assert body["notes"]


# --- reset-demo ------------------------------------------------------------------------------


def test_reset_demo_refuses_without_demo_mode(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["reset-demo"]) == 2
    assert "DEMO_MODE=true" in capsys.readouterr().err


@pytest.mark.usefixtures("demo_mode")
def test_reset_demo_seeds_two_isolated_stores(
    tenant_a: TenantCtx,
    owner_session: Callable[[int], Session],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["reset-demo", "--days", "3", "--seed", "1"]) == 0
    out = capsys.readouterr().out
    assert "north: tenant 1, 25 products" in out
    assert "south: tenant 2, 25 products" in out

    # The previous tenant (alpha) was wiped.
    assert login(TestClient(app), "alpha-admin").status_code == 401

    for tenant_id, store in enumerate(DEMO_STORES, start=1):
        db = owner_session(tenant_id)
        assert db.scalar(select(func.count()).select_from(Product)) == 25
        assert (
            db.scalar(select(func.count()).select_from(Product).where(Product.stock_quantity < 0))
            == 0
        )
        days_with_sales = db.scalar(select(func.count(func.distinct(func.date(Sale.created_at)))))
        assert days_with_sales is not None
        assert days_with_sales >= 2
        assert {u.username for u in db.scalars(select(User))} == {
            f"{store.slug}.admin",
            f"{store.slug}.manager",
            f"{store.slug}.cashier",
        }

    north = TestClient(app)
    assert login(north, "north.cashier", DEMO_PASSWORD).status_code == 200
    skus = {p["sku"] for p in north.get("/products?limit=200").json()}
    assert len(skus) == 25
    assert all(sku.startswith("ALM-") for sku in skus)

    manager = TestClient(app)
    login(manager, "south.manager", DEMO_PASSWORD)
    today = datetime.now(UTC).date()
    report = manager.get(
        "/reports/sales",
        params={"date_from": (today - timedelta(days=5)).isoformat(), "date_to": today.isoformat()},
    ).json()
    assert report["sales_count"] > 0
    assert report["top_products"][0]["sku"].startswith("FER-")


@pytest.mark.usefixtures("demo_mode")
def test_reset_demo_is_repeatable() -> None:
    first = reset_demo(days=1, seed=3)
    second = reset_demo(days=1, seed=3)
    assert first["north"].tenant_id == second["north"].tenant_id == 1
    assert first["north"].sales == second["north"].sales


# --- Production settings ---------------------------------------------------------------------


def test_production_requires_secure_cookie_and_secret() -> None:
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(environment="production", cookie_secure=False, secret_key="x" * 40)
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(environment="production", cookie_secure=True, secret_key=INSECURE_DEV_SECRET)
    ok = Settings(environment="production", cookie_secure=True, secret_key="x" * 40)
    assert ok.cookie_samesite == "lax"
