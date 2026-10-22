"""Tenant provisioning. Runs with the migration/owner connection, never from the API."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.tenant import bind_tenant
from app.models import Role, Tenant, User


def provision_tenant(
    db: Session,
    *,
    name: str,
    slug: str,
    admin_username: str,
    admin_password: str,
    admin_full_name: str = "Administrator",
) -> tuple[Tenant, User]:
    # The id is reserved first so the tenant context can be set before the
    # INSERT: FORCE RLS applies to the owner role as well.
    tenant_id = db.scalar(text("SELECT nextval(pg_get_serial_sequence('tenants', 'id'))"))
    if not isinstance(tenant_id, int):  # pragma: no cover
        raise RuntimeError("could not reserve a tenant id")
    bind_tenant(db, tenant_id)
    tenant = Tenant(id=tenant_id, name=name, slug=slug)
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant_id,
        username=admin_username.lower(),
        full_name=admin_full_name,
        password_hash=hash_password(admin_password),
        role=Role.ADMIN.value,
    )
    db.add(admin)
    db.commit()
    return tenant, admin
