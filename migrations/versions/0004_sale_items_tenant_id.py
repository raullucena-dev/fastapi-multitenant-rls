"""Add tenant_id to sale_items: nullable -> backfill -> NOT NULL, then RLS.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22

Reference implementation of the pattern documented in docs/migrations.md.
For a small table the three steps fit in one transactional migration. For a
large, hot table split them across deploys (add nullable column; batched
backfill; NOT VALID check constraint + VALIDATE; SET NOT NULL), and make sure
the code that writes tenant_id is deployed *before* RLS is enabled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_TENANT = "NULLIF(current_setting('app.current_tenant_id', true), '')::int"


def upgrade() -> None:
    # 1. Nullable column: instant, no table rewrite.
    op.add_column("sale_items", sa.Column("tenant_id", sa.Integer(), nullable=True))

    # 2. Backfill from the parent. The migration role owns `sales`, but FORCE RLS
    #    applies to owners too, so with no tenant set the join would see zero rows
    #    and silently backfill nothing. Lift FORCE for the duration of this
    #    transaction only; the ACCESS EXCLUSIVE lock means no other session can
    #    observe the intermediate state.
    op.execute("ALTER TABLE sales NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        UPDATE sale_items AS si
        SET tenant_id = s.tenant_id
        FROM sales AS s
        WHERE s.id = si.sale_id AND si.tenant_id IS NULL
        """
    )
    op.execute("ALTER TABLE sales FORCE ROW LEVEL SECURITY")

    # 3. Enforce. A NOT VALID check + VALIDATE lets SET NOT NULL skip its own scan.
    op.execute(
        "ALTER TABLE sale_items ADD CONSTRAINT ck_sale_items_tenant_id_not_null "
        "CHECK (tenant_id IS NOT NULL) NOT VALID"
    )
    op.execute("ALTER TABLE sale_items VALIDATE CONSTRAINT ck_sale_items_tenant_id_not_null")
    op.alter_column("sale_items", "tenant_id", nullable=False)
    op.execute("ALTER TABLE sale_items DROP CONSTRAINT ck_sale_items_tenant_id_not_null")

    op.create_foreign_key(
        None, "sale_items", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index(op.f("ix_sale_items_tenant_id"), "sale_items", ["tenant_id"])

    # Swap single-column FKs for composite ones so a sale item can never point
    # at another tenant's sale or product, regardless of application code.
    op.drop_constraint("fk_sale_items_sale_id_sales", "sale_items", type_="foreignkey")
    op.drop_constraint("fk_sale_items_product_id_products", "sale_items", type_="foreignkey")
    op.create_foreign_key(
        None,
        "sale_items",
        "sales",
        ["tenant_id", "sale_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        None, "sale_items", "products", ["tenant_id", "product_id"], ["tenant_id", "id"]
    )

    op.execute("ALTER TABLE sale_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sale_items FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON sale_items "
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY tenant_isolation ON sale_items")
    op.execute("ALTER TABLE sale_items NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sale_items DISABLE ROW LEVEL SECURITY")
    op.drop_constraint("fk_sale_items_tenant_id_product_id_products", "sale_items", "foreignkey")
    op.drop_constraint("fk_sale_items_tenant_id_sale_id_sales", "sale_items", "foreignkey")
    op.create_foreign_key(
        None, "sale_items", "sales", ["sale_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(None, "sale_items", "products", ["product_id"], ["id"])
    op.drop_index(op.f("ix_sale_items_tenant_id"), "sale_items")
    op.drop_constraint("fk_sale_items_tenant_id_tenants", "sale_items", "foreignkey")
    op.drop_column("sale_items", "tenant_id")
