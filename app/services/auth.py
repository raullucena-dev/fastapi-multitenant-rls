from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.errors import AccountLockedError, AuthenticationError
from app.core.security import (
    SessionCookie,
    burn_password_check,
    decode_session_cookie,
    hash_session_token,
    new_session_token,
    verify_password,
)
from app.db.tenant import bind_tenant
from app.models import User, UserSession
from app.services import audit

INVALID_CREDENTIALS = "invalid username or password"

_LOOKUP_SQL = text("SELECT user_id, tenant_id FROM auth.lookup_login_user(:username)")


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    user: User
    session: UserSession


def login(db: Session, username: str, password: str) -> tuple[User, SessionCookie]:
    settings = get_settings()

    # Tenant is unknown here: the SECURITY DEFINER function is the only path that
    # can see across tenants, and it returns nothing but (user_id, tenant_id).
    row = db.execute(_LOOKUP_SQL, {"username": username}).one_or_none()
    if row is None:
        burn_password_check(password)
        raise AuthenticationError(INVALID_CREDENTIALS)

    bind_tenant(db, row.tenant_id)
    user = db.scalars(
        select(User)
        .where(User.id == row.user_id, User.tenant_id == row.tenant_id)
        .with_for_update()
    ).one()

    now = clock.utcnow()
    if user.locked_until is not None and user.locked_until > now:
        db.rollback()
        raise AccountLockedError("account temporarily locked after repeated failed logins")

    if not verify_password(password, user.password_hash):
        user.failed_login_attempts += 1
        locked = user.failed_login_attempts >= settings.lockout_threshold
        if locked:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_attempts = 0
        audit.record(
            db, user, "auth.account_locked" if locked else "auth.login_failed", "user", user.id
        )
        db.commit()
        if locked:
            raise AccountLockedError("account temporarily locked after repeated failed logins")
        raise AuthenticationError(INVALID_CREDENTIALS)

    if not user.is_active:
        db.rollback()
        raise AuthenticationError(INVALID_CREDENTIALS)

    user.failed_login_attempts = 0
    user.locked_until = None
    token = new_session_token()
    db.add(
        UserSession(
            tenant_id=user.tenant_id,
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=now + timedelta(minutes=settings.session_ttl_minutes),
        )
    )
    audit.record(db, user, "auth.login", "user", user.id)
    db.commit()
    return user, SessionCookie(token=token, tenant_id=user.tenant_id)


def resolve_session(db: Session, cookie_value: str | None) -> AuthenticatedSession:
    """Resolve the tenant and user from the signed session cookie only."""
    settings = get_settings()
    if not cookie_value:
        raise AuthenticationError()
    cookie = decode_session_cookie(cookie_value, settings.session_ttl_minutes * 60)
    if cookie is None:
        raise AuthenticationError()

    bind_tenant(db, cookie.tenant_id)
    result = db.execute(
        select(UserSession, User)
        .join(User, (User.id == UserSession.user_id) & (User.tenant_id == UserSession.tenant_id))
        .where(
            UserSession.tenant_id == cookie.tenant_id,
            UserSession.token_hash == hash_session_token(cookie.token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > clock.utcnow(),
        )
    ).one_or_none()
    if result is None:
        raise AuthenticationError()
    session, user = result
    if not user.is_active:
        raise AuthenticationError()
    return AuthenticatedSession(user=user, session=session)


def logout(db: Session, auth: AuthenticatedSession) -> None:
    auth.session.revoked_at = clock.utcnow()
    audit.record(db, auth.user, "auth.logout", "user", auth.user.id)
    db.commit()
