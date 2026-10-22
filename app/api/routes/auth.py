from fastapi import APIRouter, Response, status

from app.api.deps import CurrentAuth, CurrentUser, DbSession
from app.core.config import get_settings
from app.core.security import encode_session_cookie
from app.schemas.users import LoginRequest, UserOut
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(payload: LoginRequest, db: DbSession, response: Response) -> UserOut:
    settings = get_settings()
    user, cookie = auth_service.login(db, payload.username, payload.password)
    response.set_cookie(
        settings.session_cookie_name,
        encode_session_cookie(cookie),
        max_age=settings.session_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(auth: CurrentAuth, db: DbSession, response: Response) -> None:
    auth_service.logout(db, auth)
    response.delete_cookie(get_settings().session_cookie_name, path="/")


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
