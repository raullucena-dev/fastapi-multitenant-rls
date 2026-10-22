from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel


class StockMovementCreate(BaseModel):
    product_id: int
    quantity: Annotated[int, Field(ge=-100_000, le=100_000)]
    reason: Literal["purchase", "adjustment"]
    note: Annotated[str | None, Field(max_length=500)] = None

    @model_validator(mode="after")
    def _check_quantity(self) -> "StockMovementCreate":
        if self.quantity == 0:
            raise ValueError("quantity must not be zero")
        if self.reason == "purchase" and self.quantity < 0:
            raise ValueError("purchase quantity must be positive")
        return self


class StockMovementOut(ORMModel):
    id: int
    product_id: int
    quantity: int
    reason: str
    note: str | None
    created_by_id: int
    sale_id: int | None
    reversal_of_id: int | None
    reversed_at: datetime | None
    created_at: datetime
