"""Application settings, loaded from environment variables (see .env.example)."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEV_SECRET = "dev-only-insecure-secret-change-me"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["local", "test", "production"] = "local"
    log_level: str = "INFO"

    # Runtime connection: a non-superuser, non-owner role that is subject to RLS.
    database_url: str = "postgresql+psycopg://app_user:app_user_dev_pw@localhost:5432/app"
    # Migration connection: the schema owner. Never used by the running API.
    migrations_database_url: str = (
        "postgresql+psycopg://app_owner:app_owner_dev_pw@localhost:5432/app"
    )
    # Defaults suit a single small instance; on a free-tier managed Postgres (Neon) use
    # e.g. DB_POOL_SIZE=3 DB_MAX_OVERFLOW=2 so that a few instances stay under its limits.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 10
    # Recycle before serverless Postgres / proxies drop idle connections.
    db_pool_recycle_seconds: int = 300

    secret_key: SecretStr = Field(default=SecretStr(INSECURE_DEV_SECRET))
    session_cookie_name: str = "session"
    session_ttl_minutes: int = 60 * 8
    cookie_secure: bool = False
    # Lax: the frontend is same-origin (served through a reverse proxy), and Lax still
    # withholds the cookie from cross-site POSTs.
    cookie_samesite: Literal["lax", "strict"] = "lax"

    bcrypt_rounds: int = 12
    lockout_threshold: int = 5
    lockout_minutes: int = 15

    # Public demo: account lockout becomes per client IP + username with a short window,
    # rate limiting is on, and GET /demo/info is exposed. Never enable on real data.
    demo_mode: bool = False
    demo_lockout_seconds: int = 60
    # Defaults to demo_mode when unset.
    rate_limit_enabled: bool | None = None
    rate_limit_login_per_minute: int = 10
    rate_limit_writes_per_minute: int = 60

    @model_validator(mode="after")
    def _production_requirements(self) -> Self:
        if self.environment == "production":
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            if self.secret_key.get_secret_value() == INSECURE_DEV_SECRET:
                raise ValueError("SECRET_KEY must be set in production")
        return self

    @property
    def rate_limiting(self) -> bool:
        return self.demo_mode if self.rate_limit_enabled is None else self.rate_limit_enabled


@lru_cache
def get_settings() -> Settings:
    return Settings()
