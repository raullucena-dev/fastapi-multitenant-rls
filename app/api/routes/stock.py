from fastapi import APIRouter, status

from app.api.deps import DbSession, ManagerUser, Pagination
from app.schemas.stock import StockMovementCreate, StockMovementOut
from app.services import stock as stock_service

router = APIRouter(prefix="/stock-movements", tags=["stock"])


@router.get("")
def list_movements(
    actor: ManagerUser, db: DbSession, page: Pagination, product_id: int | None = None
) -> list[StockMovementOut]:
    items = stock_service.list_movements(db, actor, *page, product_id=product_id)
    return [StockMovementOut.model_validate(m) for m in items]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_movement(
    payload: StockMovementCreate, actor: ManagerUser, db: DbSession
) -> StockMovementOut:
    return StockMovementOut.model_validate(stock_service.create_movement(db, actor, payload))


@router.delete("/{movement_id}")
def reverse_movement(movement_id: int, actor: ManagerUser, db: DbSession) -> StockMovementOut:
    """Reverse a movement by appending a compensating entry (returned)."""
    return StockMovementOut.model_validate(stock_service.reverse_movement(db, actor, movement_id))
