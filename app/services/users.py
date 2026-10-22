from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import ConflictError, NotFoundError
from app.core.security import hash_password
from app.models import Role, User, UserSession
from app.schemas.users import UserCreate, UserUpdate
from app.services import audit
from app.services.scoping import scoped


def list_users(db: Session, actor: User, limit: int, offset: int) -> list[User]:
    stmt = scoped(User, actor.tenant_id).order_by(User.id).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def get_user(db: Session, actor: User, user_id: int) -> User:
    user = db.scalars(scoped(User, actor.tenant_id).where(User.id == user_id)).one_or_none()
    if user is None:
        raise NotFoundError("user not found")
    return user


def create_user(db: Session, actor: User, data: UserCreate) -> User:
    user = User(
        tenant_id=actor.tenant_id,
        username=data.username,
        full_name=data.full_name,
        password_hash=hash_password(data.password),
        role=data.role.value,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("username already taken") from exc
    audit.record(
        db,
        actor,
        "user.create",
        "user",
        user.id,
        details={"username": user.username, "role": user.role},
    )
    db.commit()
    return user


def update_user(db: Session, actor: User, user_id: int, data: UserUpdate) -> User:
    user = get_user(db, actor, user_id)
    changes = data.model_dump(exclude_unset=True, exclude={"password"})
    if user.id == actor.id and (
        data.is_active is False or (data.role is not None and data.role != Role.ADMIN)
    ):
        raise ConflictError("admins cannot deactivate or demote themselves")

    if data.full_name is not None:
        user.full_name = data.full_name
    if data.role is not None:
        user.role = data.role.value
        changes["role"] = data.role.value
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password is not None:
        user.password_hash = hash_password(data.password)
        changes["password_changed"] = True
    if data.is_active is False or data.password is not None:
        db.execute(
            update(UserSession)
            .where(
                UserSession.tenant_id == actor.tenant_id,
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=clock.utcnow())
        )
    audit.record(db, actor, "user.update", "user", user.id, details=changes)
    db.commit()
    return user
