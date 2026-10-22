from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAt, TenantOwned


class MovementReason(StrEnum):
    PURCHASE = "purchase"
    ADJUSTMENT = "adjustment"
    SALE = "sale"
    REVERSAL = "reversal"


class StockMovement(TenantOwned, CreatedAt, Base):
    """Append-only stock ledger. Deleting a movement records a compensating reversal."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint("quantity <> 0", name="quantity_non_zero"),
        CheckConstraint(
            "reason IN ('purchase', 'adjustment', 'sale', 'reversal')", name="reason_valid"
        ),
        # Composite FKs make cross-tenant references impossible at the schema level.
        ForeignKeyConstraint(["tenant_id", "product_id"], ["products.tenant_id", "products.id"]),
        ForeignKeyConstraint(["tenant_id", "created_by_id"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "reversal_of_id"], ["stock_movements.tenant_id", "stock_movements.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "sale_id"], ["sales.tenant_id", "sales.id"]),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sale_id: Mapped[int | None] = mapped_column(Integer, index=True)
    reversal_of_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
