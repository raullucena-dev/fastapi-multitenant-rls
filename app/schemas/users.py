from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.models.user import Role
from app.schemas.common import ORMModel

Username = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[a-z0-9._-]{3,64}$")
]
Password = Annotated[str, Field(min_length=8, max_length=128)]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class LoginRequest(BaseModel):
    username: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=64)]
    password: Annotated[str, Field(max_length=128)]


class UserOut(ORMModel):
    id: int
    tenant_id: int
    username: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    username: Username
    full_name: FullName
    password: Password
    role: Role


class UserUpdate(BaseModel):
    full_name: FullName | None = None
    role: Role | None = None
    is_active: bool | None = None
    password: Password | None = None
