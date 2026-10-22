from typing import Any

from sqlalchemy import ForeignKeyConstraint, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAt, TenantOwned


class AuditLog(TenantOwned, CreatedAt, Base):
    """Append-only record of write operations. The app role may INSERT/SELECT only."""

    __tablename__ = "audit_log"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "actor_user_id"], ["users.tenant_id", "users.id"]),
        Index("ix_audit_log_tenant_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
