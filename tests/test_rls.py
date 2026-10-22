"""Database-layer isolation (PostgreSQL RLS), tested without the application.

These tests connect as the same role the API uses (app_user) and issue raw SQL.
"""

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DataError, ProgrammingError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services import products, sales, stock, users
from tests.conftest import TenantCtx, create_product, superuser_url

TENANT_TABLES = [
    "tenants",
    "users",
    "user_sessions",
    "products",
    "stock_movements",
    "sales",
    "sale_items",
    "audit_log",
]


@pytest.fixture
def populated(tenant_a: TenantCtx, tenant_b: TenantCtx) -> tuple[TenantCtx, TenantCtx]:
    for tenant in (tenant_a, tenant_b):
        product = create_product(tenant.admin, sku=f"{tenant.slug}-1", stock=5)
        sale = tenant.cashier.post(
            "/sales", json={"items": [{"product_id": product["id"], "quantity": 1}]}
        )
        assert sale.status_code == 201
    return tenant_a, tenant_b


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_no_tenant_set_returns_zero_rows(
    populated: tuple[TenantCtx, TenantCtx], app_engine: Engine, owner_engine: Engine, table: str
) -> None:
    """RLS fails closed: no tenant context means no rows, not an error and not all rows."""
    with app_engine.connect() as conn:
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    # FORCE ROW LEVEL SECURITY: the table owner is filtered too.
    with owner_engine.connect() as conn:
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_tenant_context_exposes_only_that_tenant(
    populated: tuple[TenantCtx, TenantCtx], app_engine: Engine, table: str
) -> None:
    tenant_a, _ = populated
    column = "id" if table == "tenants" else "tenant_id"
    with app_engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant_a.id)}
        )
        tenants_seen = conn.execute(text(f"SELECT DISTINCT {column} FROM {table}")).scalars().all()
    assert tenants_seen == [tenant_a.id]


def test_guc_reverts_to_empty_string_and_nullif_keeps_it_safe(
    populated: tuple[TenantCtx, TenantCtx], app_engine: Engine
) -> None:
    """The postmortem scenario: after a transaction-local set_config, the setting is ''.

    A bare ``''::int`` cast raises; the NULLIF form used by the policies yields NULL.
    """
    tenant_a, _ = populated
    with app_engine.connect() as conn:
        with conn.begin():
            conn.execute(
                text("SELECT set_config('app.current_tenant_id', :t, true)"),
                {"t": str(tenant_a.id)},
            )
            assert conn.execute(text("SELECT count(*) FROM products")).scalar_one() == 1
        # Same pooled connection, new transaction: the setting now exists but is empty.
        value = conn.execute(text("SELECT current_setting('app.current_tenant_id', true)"))
        assert value.scalar_one() == ""
        assert conn.execute(text("SELECT count(*) FROM products")).scalar_one() == 0
        with pytest.raises(DataError):
            conn.execute(text("SELECT current_setting('app.current_tenant_id', true)::int"))


def test_insert_for_another_tenant_violates_with_check(
    populated: tuple[TenantCtx, TenantCtx], app_engine: Engine
) -> None:
    tenant_a, tenant_b = populated
    with app_engine.connect() as conn, conn.begin():
        conn.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant_a.id)}
        )
        with pytest.raises(ProgrammingError, match="row-level security"):
            conn.execute(
                text("INSERT INTO products (tenant_id, sku, name, price) VALUES (:t, 'X', 'x', 1)"),
                {"t": tenant_b.id},
            )


def test_update_cannot_move_rows_to_another_tenant(
    populated: tuple[TenantCtx, TenantCtx], app_engine: Engine
) -> None:
    tenant_a, tenant_b = populated
    with app_engine.connect() as conn, conn.begin():
        conn.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant_a.id)}
        )
        with pytest.raises(ProgrammingError, match="row-level security"):
            conn.execute(text("UPDATE products SET tenant_id = :t"), {"t": tenant_b.id})


def test_update_and_delete_of_foreign_rows_affect_nothing(
    populated: tuple[TenantCtx, TenantCtx], app_engine: Engine
) -> None:
    tenant_a, _ = populated
    with app_engine.connect() as conn, conn.begin():
        conn.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant_a.id)}
        )
        updated = conn.execute(
            text("UPDATE products SET name = 'pwned' WHERE tenant_id <> :t"), {"t": tenant_a.id}
        )
        deleted = conn.execute(
            text("DELETE FROM user_sessions WHERE tenant_id <> :t"), {"t": tenant_a.id}
        )
        assert updated.rowcount == 0
        assert deleted.rowcount == 0


def test_tenant_setting_does_not_leak_across_pooled_connections(
    populated: tuple[TenantCtx, TenantCtx],
) -> None:
    engine = create_engine(get_settings().database_url, pool_size=1, max_overflow=0)
    try:
        tenant_a, _ = populated
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant_id', :t, true)"),
                {"t": str(tenant_a.id)},
            )
        with engine.connect() as conn:  # same physical connection
            assert conn.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
    finally:
        engine.dispose()


def test_app_role_is_not_privileged(app_engine: Engine) -> None:
    with app_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
        assert tuple(row) == (False, False, False, False)
        owned = conn.execute(
            text(
                "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                "AND tableowner = current_user"
            )
        ).scalar_one()
        assert owned == 0


def test_every_tenant_table_has_forced_rls_and_not_null_tenant(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' "
                "AND relname <> 'alembic_version'"
            )
        ).all()
        assert {r.relname for r in rows} == set(TENANT_TABLES)
        assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows)

        nullable = (
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
                    "AND is_nullable = 'YES'"
                )
            )
            .scalars()
            .all()
        )
        assert nullable == []

        policies = conn.execute(
            text(
                "SELECT tablename, qual, with_check FROM pg_policies "
                "WHERE schemaname = 'public' AND policyname = 'tenant_isolation'"
            )
        ).all()
        assert {p.tablename for p in policies} == set(TENANT_TABLES)
        for policy in policies:
            assert "NULLIF(current_setting('app.current_tenant_id'::text, true), ''::text)" in (
                policy.qual
            )
            assert policy.with_check == policy.qual


def test_audit_log_is_append_only_for_app_role(
    populated: tuple[TenantCtx, TenantCtx], app_engine: Engine
) -> None:
    tenant_a, _ = populated
    for statement in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"):
        with app_engine.connect() as conn, conn.begin():
            conn.execute(
                text("SELECT set_config('app.current_tenant_id', :t, true)"),
                {"t": str(tenant_a.id)},
            )
            with pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(text(statement))


def test_app_role_cannot_create_tenants(app_engine: Engine) -> None:
    with app_engine.connect() as conn, pytest.raises(ProgrammingError, match="permission denied"):
        conn.execute(text("INSERT INTO tenants (name, slug) VALUES ('x', 'x')"))


# --- Login bootstrap: SECURITY DEFINER function --------------------------------------------


def test_login_lookup_resolves_tenant_without_context(
    populated: tuple[TenantCtx, TenantCtx], app_engine: Engine
) -> None:
    _, tenant_b = populated
    with app_engine.connect() as conn:
        row = (
            conn.execute(text("SELECT * FROM auth.lookup_login_user('beta-cashier')"))
            .mappings()
            .one()
        )
        assert dict(row) == {"user_id": tenant_b.users["cashier"].id, "tenant_id": tenant_b.id}
        assert (
            conn.execute(
                text("SELECT count(*) FROM auth.lookup_login_user('missing')")
            ).scalar_one()
            == 0
        )


def test_login_lookup_exposes_no_secrets(app_engine: Engine) -> None:
    with app_engine.connect() as conn:
        definition = conn.execute(
            text(
                "SELECT p.prosecdef, r.rolname, r.rolcanlogin, "
                "pg_get_function_result(p.oid) AS result "
                "FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner "
                "WHERE p.proname = 'lookup_login_user'"
            )
        ).one()
    assert definition.prosecdef is True
    assert definition.rolname == "app_auth"
    assert definition.rolcanlogin is False
    assert definition.result == "TABLE(user_id integer, tenant_id integer)"


def test_definer_role_cannot_read_password_hashes(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn, conn.begin():
        conn.execute(text("SET LOCAL ROLE app_auth"))
        assert conn.execute(text("SELECT count(username) FROM users")).scalar_one() >= 0
        with pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(text("SELECT password_hash FROM users"))


# --- Layer 1 on its own: application scoping without RLS ------------------------------------


@pytest.mark.skipif(superuser_url() is None, reason="TEST_SUPERUSER_DATABASE_URL not set")
def test_service_layer_scopes_queries_even_when_rls_is_bypassed(
    populated: tuple[TenantCtx, TenantCtx],
) -> None:
    """A superuser bypasses RLS entirely; the services must still filter by tenant."""
    tenant_a, tenant_b = populated
    url = superuser_url()
    assert url is not None
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            raw = db.execute(text("SELECT count(*) FROM products")).scalar_one()
            assert raw == 2, "superuser sees both tenants: RLS is out of the picture"
            actor = tenant_a.users["admin"]
            assert {p.sku for p in products.list_products(db, actor, 100, 0)} == {"alpha-1"}
            assert {u.tenant_id for u in users.list_users(db, actor, 100, 0)} == {tenant_a.id}
            assert all(s.tenant_id == tenant_a.id for s in sales.list_sales(db, actor, 100, 0))
            assert all(m.tenant_id == tenant_a.id for m in stock.list_movements(db, actor, 100, 0))
            foreign_user = tenant_b.users["cashier"].id
            with pytest.raises(Exception, match="user not found"):
                users.get_user(db, actor, foreign_user)
    finally:
        engine.dispose()
