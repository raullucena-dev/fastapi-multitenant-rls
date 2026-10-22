from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class SaleItemIn(BaseModel):
    product_id: int
    quantity: Annotated[int, Field(gt=0, le=10_000)]


class SaleCreate(BaseModel):
    items: Annotated[list[SaleItemIn], Field(min_length=1, max_length=100)]
    # Lets a manager/admin record a sale on behalf of a cashier. Validated against
    # the requester's tenant; a foreign id is indistinguishable from a missing one.
    cashier_id: int | None = None

    @field_validator("items")
    @classmethod
    def _unique_products(cls, items: list[SaleItemIn]) -> list[SaleItemIn]:
        if len({item.product_id for item in items}) != len(items):
            raise ValueError("each product may appear only once per sale")
        return items


class SaleItemOut(ORMModel):
    product_id: int
    quantity: int
    unit_price: Decimal
    line_total: Decimal


class SaleOut(ORMModel):
    id: int
    cashier_id: int
    total: Decimal
    created_at: datetime
    items: list[SaleItemOut]


class SaleSummary(ORMModel):
    id: int
    cashier_id: int
    total: Decimal
    created_at: datetime


class DailySales(BaseModel):
    day: date
    sales_count: int
    revenue: Decimal


class TopProduct(BaseModel):
    product_id: int
    sku: str
    name: str
    quantity: int
    revenue: Decimal


class SalesReport(BaseModel):
    date_from: date
    date_to: date
    sales_count: int
    revenue: Decimal
    items_sold: int
    daily: list[DailySales]
    top_products: list[TopProduct]
