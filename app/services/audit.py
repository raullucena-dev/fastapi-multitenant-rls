from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, User


def record(
    db: Session,
    actor: User,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    """Append an audit entry inside the caller's transaction (committed with the change)."""
    db.add(
        AuditLog(
            tenant_id=actor.tenant_id,
            actor_user_id=actor.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )
