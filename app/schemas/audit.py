from datetime import datetime
from typing import Any

from app.schemas.common import ORMModel


class AuditLogOut(ORMModel):
    id: int
    actor_user_id: int | None
    action: str
    entity_type: str
    entity_id: int | None
    details: dict[str, Any]
    created_at: datetime
