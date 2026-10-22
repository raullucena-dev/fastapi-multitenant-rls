"""Application-level tenant scoping (layer 1; RLS is layer 2).

Every query on a tenant-owned table goes through ``scoped`` so that tenant
filtering does not depend on the database policy being present or correct.
"""

from sqlalchemy import Select, select

from app.models.mixins import TenantOwned


def scoped[M: TenantOwned](model: type[M], tenant_id: int) -> Select[M]:
    return select(model).where(model.tenant_id == tenant_id)
