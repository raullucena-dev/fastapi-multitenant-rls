from typing import Literal

from sqlalchemy import Select, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import ConflictError, NotFoundError
from app.models import Product, SaleItem, StockMovement, User
from app.schemas.products import ProductCreate, ProductUpdate
from app.services import audit
from app.services.scoping import scoped


def _active(tenant_id: int) -> Select[Product]:
    return scoped(Product, tenant_id).where(Product.deleted_at.is_(None))


def list_products(
    db: Session, actor: User, limit: int, offset: int, search: str | None = None
) -> list[Product]:
    stmt = _active(actor.tenant_id)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(Product.name.ilike(pattern) | Product.sku.ilike(pattern))
    return list(db.scalars(stmt.order_by(Product.id).limit(limit).offset(offset)))


def get_product(db: Session, actor: User, product_id: int, *, for_update: bool = False) -> Product:
    stmt = _active(actor.tenant_id).where(Product.id == product_id)
    if for_update:
        stmt = stmt.with_for_update()
    product = db.scalars(stmt).one_or_none()
    if product is None:
        raise NotFoundError("product not found")
    return product


def _flush_or_conflict(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("a product with this SKU already exists") from exc


def create_product(db: Session, actor: User, data: ProductCreate) -> Product:
    product = Product(tenant_id=actor.tenant_id, sku=data.sku, name=data.name, price=data.price)
    db.add(product)
    _flush_or_conflict(db)
    audit.record(
        db, actor, "product.create", "product", product.id, details=data.model_dump(mode="json")
    )
    db.commit()
    db.refresh(product)
    return product


def update_product(db: Session, actor: User, product_id: int, data: ProductUpdate) -> Product:
    product = get_product(db, actor, product_id, for_update=True)
    changes = data.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(product, field, value)
    _flush_or_conflict(db)
    audit.record(db, actor, "product.update", "product", product.id, details=changes)
    db.commit()
    db.refresh(product)
    return product


def delete_product(db: Session, actor: User, product_id: int) -> Literal["soft", "hard"]:
    """Hard delete unless history (sales or stock movements) references the product."""
    product = get_product(db, actor, product_id, for_update=True)
    referenced = db.scalar(
        select(
            exists().where(SaleItem.tenant_id == actor.tenant_id, SaleItem.product_id == product.id)
            | exists().where(
                StockMovement.tenant_id == actor.tenant_id, StockMovement.product_id == product.id
            )
        )
    )
    mode: Literal["soft", "hard"]
    if referenced:
        product.deleted_at = clock.utcnow()
        mode = "soft"
    else:
        db.delete(product)
        mode = "hard"
    audit.record(
        db,
        actor,
        "product.delete",
        "product",
        product_id,
        details={"mode": mode, "sku": product.sku},
    )
    db.commit()
    return mode
