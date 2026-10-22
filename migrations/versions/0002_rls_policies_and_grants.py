"""Row-Level Security policies and least-privilege grants for the app role.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22

Every tenant-owned table gets ENABLE + FORCE ROW LEVEL SECURITY (FORCE so that
even the table owner is filtered) and a single policy comparing the row's
tenant to the transaction-local setting ``app.current_tenant_id``.

The setting is read as ``NULLIF(current_setting(..., true), '')::int``:
``missing_ok=true`` returns NULL when the setting was never defined, and
NULLIF covers the case where it was defined earlier on the same pooled
connection and has reverted to ''. Either way the comparison is NULL and the
query returns zero rows instead of raising (see ADR 0002).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_TENANT = "NULLIF(current_setting('app.current_tenant_id', true), '')::int"

# table -> privileges for app_user. No table grants DELETE unless the domain needs it;
# audit_log and sales are append-only for the application.
APP_GRANTS = {
    "tenants": "SELECT",
    "users": "SELECT, INSERT, UPDATE",
    "user_sessions": "SELECT, INSERT, UPDATE, DELETE",
    "products": "SELECT, INSERT, UPDATE, DELETE",
    "stock_movements": "SELECT, INSERT, UPDATE",
    "sales": "SELECT, INSERT",
    "sale_items": "SELECT, INSERT",
    "audit_log": "SELECT, INSERT",
}

RLS_TABLES = {
    "tenants": "id",
    "users": "tenant_id",
    "user_sessions": "tenant_id",
    "products": "tenant_id",
    "stock_movements": "tenant_id",
    "sales": "tenant_id",
    "audit_log": "tenant_id",
}


def enable_rls(table: str, column: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING ({column} = {CURRENT_TENANT}) "
        f"WITH CHECK ({column} = {CURRENT_TENANT})"
    )


def disable_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO app_user")
    for table, privileges in APP_GRANTS.items():
        op.execute(f"GRANT {privileges} ON {table} TO app_user")
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_user")

    for table, column in RLS_TABLES.items():
        enable_rls(table, column)


def downgrade() -> None:
    for table in RLS_TABLES:
        disable_rls(table)
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_user")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM app_user")
