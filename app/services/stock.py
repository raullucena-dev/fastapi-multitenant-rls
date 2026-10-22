from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import ConflictError, InsufficientStockError, NotFoundError
from app.models import MovementReason, Product, StockMovement, User
from app.schemas.stock import StockMovementCreate
from app.services import audit
from app.services.products import get_product
from app.services.scoping import scoped


def list_movements(
    db: Session, actor: User, limit: int, offset: int, product_id: int | None = None
) -> list[StockMovement]:
    stmt = scoped(StockMovement, actor.tenant_id)
    if product_id is not None:
        stmt = stmt.where(StockMovement.product_id == product_id)
    stmt = stmt.order_by(StockMovement.id.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def _apply_delta(product: Product, delta: int) -> None:
    if product.stock_quantity + delta < 0:
        raise InsufficientStockError(
            f"insufficient stock for product {product.id}: "
            f"have {product.stock_quantity}, change {delta}"
        )
    product.stock_quantity += delta


def create_movement(db: Session, actor: User, data: StockMovementCreate) -> StockMovement:
    # Row lock serialises concurrent movements and sales on the same product.
    product = get_product(db, actor, data.product_id, for_update=True)
    _apply_delta(product, data.quantity)
    movement = StockMovement(
        tenant_id=actor.tenant_id,
        product_id=product.id,
        quantity=data.quantity,
        reason=data.reason,
        note=data.note,
        created_by_id=actor.id,
    )
    db.add(movement)
    db.flush()
    audit.record(
        db,
        actor,
        "stock.create",
        "stock_movement",
        movement.id,
        details={"product_id": product.id, "quantity": data.quantity, "reason": data.reason},
    )
    db.commit()
    return movement


def reverse_movement(db: Session, actor: User, movement_id: int) -> StockMovement:
    """'Deleting' a movement appends a compensating entry; the ledger is never rewritten."""
    original = db.scalars(
        scoped(StockMovement, actor.tenant_id)
        .where(StockMovement.id == movement_id)
        .with_for_update()
    ).one_or_none()
    if original is None:
        raise NotFoundError("stock movement not found")
    if original.reason in (MovementReason.SALE, MovementReason.REVERSAL):
        raise ConflictError(f"{original.reason} movements cannot be reversed")
    if original.reversed_at is not None:
        raise ConflictError("stock movement already reversed")

    product = db.scalars(
        scoped(Product, actor.tenant_id).where(Product.id == original.product_id).with_for_update()
    ).one()
    _apply_delta(product, -original.quantity)

    now = clock.utcnow()
    original.reversed_at = now
    reversal = StockMovement(
        tenant_id=actor.tenant_id,
        product_id=product.id,
        quantity=-original.quantity,
        reason=MovementReason.REVERSAL,
        note=f"reversal of movement {original.id}",
        created_by_id=actor.id,
        reversal_of_id=original.id,
    )
    db.add(reversal)
    db.flush()
    audit.record(
        db,
        actor,
        "stock.reverse",
        "stock_movement",
        original.id,
        details={"reversal_id": reversal.id, "quantity": reversal.quantity},
    )
    db.commit()
    return reversal
