"""Core schema: tenants, users, sessions, products, stock, sales, audit log.

Revision ID: 0001
Revises:
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tz(name: str, *, nullable: bool = True, now: bool = False) -> sa.Column[sa.DateTime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if now else None,
    )


def _tenant_id() -> sa.Column[sa.Integer]:
    return sa.Column(
        "tenant_id",
        sa.Integer(),
        sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False, unique=True),
        _tz("created_at", nullable=False, now=True),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant_id(),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(100), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "failed_login_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        _tz("locked_until"),
        _tz("created_at", nullable=False, now=True),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.CheckConstraint("role IN ('admin', 'manager', 'cashier')", name="role_valid"),
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant_id(),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        _tz("expires_at", nullable=False),
        _tz("revoked_at"),
        _tz("created_at", nullable=False, now=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
        ),
    )

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant_id(),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("stock_quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _tz("updated_at", nullable=False, now=True),
        _tz("deleted_at"),
        _tz("created_at", nullable=False, now=True),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.CheckConstraint("stock_quantity >= 0", name="stock_non_negative"),
        sa.CheckConstraint("price >= 0", name="price_non_negative"),
    )
    op.create_index(
        "uq_products_tenant_sku_active",
        "products",
        ["tenant_id", "sku"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "sales",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant_id(),
        sa.Column("cashier_id", sa.Integer(), nullable=False, index=True),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        _tz("created_at", nullable=False, now=True),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.ForeignKeyConstraint(["tenant_id", "cashier_id"], ["users.tenant_id", "users.id"]),
        sa.CheckConstraint("total >= 0", name="total_non_negative"),
    )
    op.create_index("ix_sales_tenant_created_at", "sales", ["tenant_id", "created_at"])

    # sale_items initially relied on its parent sale for isolation; tenant_id is
    # added in 0004 using the nullable -> backfill -> NOT NULL pattern.
    op.create_table(
        "sale_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "sale_id",
            sa.Integer(),
            sa.ForeignKey("sales.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False, index=True
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
    )

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant_id(),
        sa.Column("product_id", sa.Integer(), nullable=False, index=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("note", sa.String(500)),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("sale_id", sa.Integer(), index=True),
        sa.Column("reversal_of_id", sa.Integer(), unique=True),
        _tz("reversed_at"),
        _tz("created_at", nullable=False, now=True),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.CheckConstraint("quantity <> 0", name="quantity_non_zero"),
        sa.CheckConstraint(
            "reason IN ('purchase', 'adjustment', 'sale', 'reversal')", name="reason_valid"
        ),
        sa.ForeignKeyConstraint(["tenant_id", "product_id"], ["products.tenant_id", "products.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "created_by_id"], ["users.tenant_id", "users.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reversal_of_id"], ["stock_movements.tenant_id", "stock_movements.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "sale_id"], ["sales.tenant_id", "sales.id"]),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant_id(),
        sa.Column("actor_user_id", sa.Integer(), index=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.Integer()),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        _tz("created_at", nullable=False, now=True),
        sa.ForeignKeyConstraint(["tenant_id", "actor_user_id"], ["users.tenant_id", "users.id"]),
    )
    op.create_index("ix_audit_log_tenant_created_at", "audit_log", ["tenant_id", "created_at"])


def downgrade() -> None:
    for table in (
        "audit_log",
        "stock_movements",
        "sale_items",
        "sales",
        "products",
        "user_sessions",
        "users",
        "tenants",
    ):
        op.drop_table(table)
