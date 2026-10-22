"""Test fixtures. Tests run against a real PostgreSQL with the production roles.

Expected environment (defaults match ``make test`` / docker compose):

* DATABASE_URL             app_user  @ app_test  (subject to RLS)
* MIGRATIONS_DATABASE_URL  app_owner @ app_test  (schema owner)
* TEST_SUPERUSER_DATABASE_URL (optional) enables tests that bypass RLS on purpose.
"""

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://app_user:app_user_dev_pw@localhost:5432/app_test"
)
os.environ.setdefault(
    "MIGRATIONS_DATABASE_URL",
    "postgresql+psycopg://app_owner:app_owner_dev_pw@localhost:5432/app_test",
)
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-used-anywhere-else")

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.tenant import bind_tenant
from app.main import app
from app.models import Role, User
from app.services.tenants import provision_tenant

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "correct-horse-battery"


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def owner_engine() -> Iterator[Engine]:
    engine = create_engine(get_settings().migrations_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine() -> Iterator[Engine]:
    engine = create_engine(get_settings().database_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _migrated_database(owner_engine: Engine) -> None:
    """Exercise the full migration chain down and up once per session."""
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def _clean_tables(owner_engine: Engine) -> Iterator[None]:
    yield
    # TRUNCATE is not subject to RLS, so the owner can reset all tenants at once.
    with owner_engine.begin() as conn:
        conn.execute(text("TRUNCATE tenants RESTART IDENTITY CASCADE"))


@dataclass
class TenantCtx:
    id: int
    slug: str
    users: dict[str, User] = field(default_factory=dict)
    clients: dict[str, TestClient] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def admin(self) -> TestClient:
        return self.clients["admin"]

    @property
    def manager(self) -> TestClient:
        return self.clients["manager"]

    @property
    def cashier(self) -> TestClient:
        return self.clients["cashier"]


def login(client: TestClient, username: str, password: str = PASSWORD) -> Any:
    return client.post("/auth/login", json={"username": username, "password": password})


@pytest.fixture
def owner_session(owner_engine: Engine) -> Callable[[int], Session]:
    """Owner-role session bound to a tenant (FORCE RLS applies to the owner as well)."""
    opened: list[Session] = []

    def factory(tenant_id: int) -> Session:
        session = Session(owner_engine, expire_on_commit=False)
        bind_tenant(session, tenant_id)
        opened.append(session)
        return session

    yield factory  # type: ignore[misc]
    for session in opened:
        session.close()


@pytest.fixture
def create_user(owner_session: Callable[[int], Session]) -> Callable[..., User]:
    def factory(tenant_id: int, username: str, role: Role, **kwargs: Any) -> User:
        db = owner_session(tenant_id)
        user = User(
            tenant_id=tenant_id,
            username=username,
            full_name=kwargs.pop("full_name", username.title()),
            password_hash=hash_password(kwargs.pop("password", PASSWORD), rounds=4),
            role=role.value,
            **kwargs,
        )
        db.add(user)
        db.commit()
        return user

    return factory


@pytest.fixture
def make_tenant(
    owner_engine: Engine, create_user: Callable[..., User]
) -> Callable[[str], TenantCtx]:
    def factory(slug: str) -> TenantCtx:
        with Session(owner_engine, expire_on_commit=False) as db:
            tenant, admin = provision_tenant(
                db,
                name=f"Tenant {slug.upper()}",
                slug=slug,
                admin_username=f"{slug}-admin",
                admin_password=PASSWORD,
            )
        ctx = TenantCtx(id=tenant.id, slug=slug)
        ctx.users["admin"] = admin
        ctx.users["manager"] = create_user(tenant.id, f"{slug}-manager", Role.MANAGER)
        ctx.users["cashier"] = create_user(tenant.id, f"{slug}-cashier", Role.CASHIER)
        for role, user in ctx.users.items():
            client = TestClient(app)
            response = login(client, user.username)
            assert response.status_code == 200, response.text
            ctx.clients[role] = client
        return ctx

    return factory


@pytest.fixture
def tenant_a(make_tenant: Callable[[str], TenantCtx]) -> TenantCtx:
    return make_tenant("alpha")


@pytest.fixture
def tenant_b(make_tenant: Callable[[str], TenantCtx]) -> TenantCtx:
    return make_tenant("beta")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def create_product(
    client: TestClient, sku: str = "SKU-1", price: str = "10.00", stock: int = 0
) -> dict[str, Any]:
    response = client.post("/products", json={"sku": sku, "name": f"Product {sku}", "price": price})
    assert response.status_code == 201, response.text
    product: dict[str, Any] = response.json()
    if stock:
        movement = client.post(
            "/stock-movements",
            json={"product_id": product["id"], "quantity": stock, "reason": "purchase"},
        )
        assert movement.status_code == 201, movement.text
        product["stock_quantity"] = stock
    return product


def superuser_url() -> str | None:
    return os.environ.get("TEST_SUPERUSER_DATABASE_URL")
