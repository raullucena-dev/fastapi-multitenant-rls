from fastapi import APIRouter, status

from app.api.deps import AdminUser, DbSession, ManagerUser, Pagination
from app.schemas.users import UserCreate, UserOut, UserUpdate
from app.services import users as users_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
def list_users(actor: ManagerUser, db: DbSession, page: Pagination) -> list[UserOut]:
    return [UserOut.model_validate(u) for u in users_service.list_users(db, actor, *page)]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, actor: AdminUser, db: DbSession) -> UserOut:
    return UserOut.model_validate(users_service.create_user(db, actor, payload))


@router.get("/{user_id}")
def get_user(user_id: int, actor: ManagerUser, db: DbSession) -> UserOut:
    return UserOut.model_validate(users_service.get_user(db, actor, user_id))


@router.patch("/{user_id}")
def update_user(user_id: int, payload: UserUpdate, actor: AdminUser, db: DbSession) -> UserOut:
    return UserOut.model_validate(users_service.update_user(db, actor, user_id, payload))
