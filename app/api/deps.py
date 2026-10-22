from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import PermissionDeniedError
from app.db.session import get_db
from app.models import Role, User
from app.services import auth as auth_service
from app.services.auth import AuthenticatedSession

DbSession = Annotated[Session, Depends(get_db)]


def client_ip(request: Request) -> str | None:
    """Client address as seen after uvicorn's proxy-header handling (see docs/deploy.md)."""
    return request.client.host if request.client else None


def get_auth_session(request: Request, db: DbSession) -> AuthenticatedSession:
    """Authenticate the request and bind the DB session to the caller's tenant.

    The tenant comes exclusively from the signed session cookie; nothing in the
    path, query or body can influence it.
    """
    cookie = request.cookies.get(get_settings().session_cookie_name)
    auth = auth_service.resolve_session(db, cookie)
    request.state.tenant_id = auth.user.tenant_id
    request.state.user_id = auth.user.id
    return auth


def get_current_user(auth: Annotated[AuthenticatedSession, Depends(get_auth_session)]) -> User:
    return auth.user


CurrentAuth = Annotated[AuthenticatedSession, Depends(get_auth_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: Role) -> Callable[[User], User]:
    allowed = frozenset(roles)

    def dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise PermissionDeniedError("insufficient role for this operation")
        return user

    return dependency


AdminUser = Annotated[User, Depends(require_role(Role.ADMIN))]
ManagerUser = Annotated[User, Depends(require_role(Role.ADMIN, Role.MANAGER))]
AnyUser = Annotated[User, Depends(require_role(Role.ADMIN, Role.MANAGER, Role.CASHIER))]


def pagination(limit: int = 50, offset: int = 0) -> tuple[int, int]:
    return max(1, min(limit, 200)), max(0, offset)


Pagination = Annotated[tuple[int, int], Depends(pagination)]
