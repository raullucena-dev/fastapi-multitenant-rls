from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.common import ORMModel

Sku = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9._-]{1,64}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Price = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


class ProductCreate(BaseModel):
    sku: Sku
    name: Name
    price: Price


class ProductUpdate(BaseModel):
    sku: Sku | None = None
    name: Name | None = None
    price: Price | None = None


class ProductOut(ORMModel):
    id: int
    sku: str
    name: str
    price: Decimal
    stock_quantity: int
    created_at: datetime
    updated_at: datetime


class ProductDeleted(BaseModel):
    id: int
    mode: Literal["soft", "hard"]
