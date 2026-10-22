"""Password hashing and signed session cookies."""

import hashlib
import secrets
from dataclasses import dataclass

import bcrypt
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.core.config import get_settings

_SESSION_SALT = "session-cookie-v1"


def hash_password(password: str, rounds: int | None = None) -> str:
    cost = rounds if rounds is not None else get_settings().bcrypt_rounds
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=cost)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# Used to keep response time similar when the username does not exist.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds=4)).decode()


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Only the SHA-256 of the session token is stored, so a DB leak yields no live sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SessionCookie:
    token: str
    tenant_id: int


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key.get_secret_value(), salt=_SESSION_SALT)


def encode_session_cookie(cookie: SessionCookie) -> str:
    return _serializer().dumps({"sid": cookie.token, "tid": cookie.tenant_id})


def decode_session_cookie(value: str, max_age_seconds: int) -> SessionCookie | None:
    try:
        data = _serializer().loads(value, max_age=max_age_seconds)
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    token, tenant_id = data.get("sid"), data.get("tid")
    if not isinstance(token, str) or not isinstance(tenant_id, int) or isinstance(tenant_id, bool):
        return None
    return SessionCookie(token=token, tenant_id=tenant_id)
