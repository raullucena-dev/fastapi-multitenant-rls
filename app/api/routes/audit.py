from fastapi import APIRouter

from app.api.deps import AdminUser, DbSession, Pagination
from app.models import AuditLog
from app.schemas.audit import AuditLogOut
from app.services.scoping import scoped

router = APIRouter(prefix="/audit-log", tags=["audit"])


@router.get("")
def list_audit_log(
    actor: AdminUser, db: DbSession, page: Pagination, entity_type: str | None = None
) -> list[AuditLogOut]:
    limit, offset = page
    stmt = scoped(AuditLog, actor.tenant_id)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    rows = db.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit).offset(offset))
    return [AuditLogOut.model_validate(r) for r in rows]
