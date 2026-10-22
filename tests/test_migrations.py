"""Migration behaviour: drift check and the nullable -> backfill -> NOT NULL pattern."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text

from app.db.base import Base
from tests.conftest import alembic_config


def test_models_match_migrations(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_sale_items_tenant_id_backfill(owner_engine: Engine) -> None:
    cfg = alembic_config()
    command.downgrade(cfg, "0003")
    try:
        with owner_engine.begin() as conn:
            # Owner is subject to FORCE RLS: set the tenant to seed rows.
            for tenant_id in (1, 2):
                conn.execute(
                    text("SELECT set_config('app.current_tenant_id', :t, true)"),
                    {"t": str(tenant_id)},
                )
                conn.execute(
                    text("INSERT INTO tenants (id, name, slug) VALUES (:t, 'T', 't' || :t)"),
                    {"t": tenant_id},
                )
                conn.execute(
                    text(
                        "INSERT INTO users (id, tenant_id, username, full_name, password_hash, "
                        "role) VALUES (:t, :t, 'u' || :t, 'U', 'x', 'cashier')"
                    ),
                    {"t": tenant_id},
                )
                conn.execute(
                    text(
                        "INSERT INTO products (id, tenant_id, sku, name, price) "
                        "VALUES (:t, :t, 'S', 'P', 1)"
                    ),
                    {"t": tenant_id},
                )
                conn.execute(
                    text(
                        "INSERT INTO sales (id, tenant_id, cashier_id, total) "
                        "VALUES (:t, :t, :t, 1)"
                    ),
                    {"t": tenant_id},
                )
                # Pre-0004 shape: no tenant_id column on sale_items.
                conn.execute(
                    text(
                        "INSERT INTO sale_items (sale_id, product_id, quantity, unit_price, "
                        "line_total) VALUES (:t, :t, 1, 1, 1)"
                    ),
                    {"t": tenant_id},
                )

        command.upgrade(cfg, "head")

        with owner_engine.begin() as conn:
            conn.execute(text("ALTER TABLE sale_items NO FORCE ROW LEVEL SECURITY"))
            rows = conn.execute(
                text("SELECT sale_id, tenant_id FROM sale_items ORDER BY sale_id")
            ).all()
            conn.execute(text("ALTER TABLE sale_items FORCE ROW LEVEL SECURITY"))
        assert [tuple(r) for r in rows] == [(1, 1), (2, 2)]
    finally:
        command.upgrade(cfg, "head")
