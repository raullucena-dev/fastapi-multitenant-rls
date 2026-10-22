"""Application settings, loaded from environment variables (see .env.example)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    db_pool_size: int = 5
    db_max_overflow: int = 10

    secret_key: SecretStr = Field(default=SecretStr("dev-only-insecure-secret-change-me"))
    session_cookie_name: str = "session"
    session_ttl_minutes: int = 60 * 8
    cookie_secure: bool = False

    bcrypt_rounds: int = 12
    lockout_threshold: int = 5
    lockout_minutes: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
