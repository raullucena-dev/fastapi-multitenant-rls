"""Per-transaction tenant context for PostgreSQL Row-Level Security.

The tenant id lives in ``session.info`` and is pushed to PostgreSQL with
``set_config('app.current_tenant_id', :id, true)`` at the start of every
transaction. ``is_local=true`` scopes the setting to the transaction, so it
can never leak to the next user of a pooled connection. ``SET LOCAL`` is not
used because it does not accept bind parameters.
"""

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, SessionTransaction

TENANT_GUC = "app.current_tenant_id"
_TENANT_KEY = "tenant_id"

_SET_TENANT_SQL = text("SELECT set_config(:name, :value, true)")


def _apply(connection: Connection, tenant_id: int) -> None:
    connection.execute(_SET_TENANT_SQL, {"name": TENANT_GUC, "value": str(tenant_id)})


def bind_tenant(session: Session, tenant_id: int) -> None:
    """Scope ``session`` to ``tenant_id`` for the current and all later transactions."""
    session.info[_TENANT_KEY] = tenant_id
    if session.in_transaction():
        _apply(session.connection(), tenant_id)


def current_tenant(session: Session) -> int | None:
    value = session.info.get(_TENANT_KEY)
    return value if isinstance(value, int) else None


@event.listens_for(Session, "after_begin")
def _set_tenant_on_begin(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    tenant_id = current_tenant(session)
    if tenant_id is not None:
        _apply(connection, tenant_id)
