from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import InsufficientStockError, NotFoundError, PermissionDeniedError
from app.models import MovementReason, Product, Role, Sale, SaleItem, StockMovement, User
from app.schemas.sales import DailySales, SaleCreate, SalesReport, TopProduct
from app.services import audit
from app.services.scoping import scoped


@dataclass(frozen=True, slots=True)
class SaleWithItems:
    sale: Sale
    items: list[SaleItem]

    # Flattened attributes so the response model can read this object directly.
    @property
    def id(self) -> int:
        return self.sale.id

    @property
    def cashier_id(self) -> int:
        return self.sale.cashier_id

    @property
    def total(self) -> Decimal:
        return self.sale.total

    @property
    def created_at(self) -> datetime:
        return self.sale.created_at


def _resolve_cashier(db: Session, actor: User, cashier_id: int | None) -> int:
    if cashier_id is None or cashier_id == actor.id:
        return actor.id
    if actor.role == Role.CASHIER:
        raise PermissionDeniedError("cashiers can only record their own sales")
    # A user id from the payload is only trusted after it is found in the
    # requester's tenant. Foreign ids get the same 404 as non-existent ones.
    cashier = db.scalars(
        scoped(User, actor.tenant_id).where(User.id == cashier_id, User.is_active.is_(True))
    ).one_or_none()
    if cashier is None:
        raise NotFoundError("user not found")
    return cashier.id


def create_sale(db: Session, actor: User, data: SaleCreate) -> SaleWithItems:
    """Create a sale and decrement stock atomically, in a single transaction."""
    cashier_id = _resolve_cashier(db, actor, data.cashier_id)

    product_ids = sorted(item.product_id for item in data.items)
    # Lock in a deterministic order so concurrent sales cannot deadlock.
    products = {
        p.id: p
        for p in db.scalars(
            scoped(Product, actor.tenant_id)
            .where(Product.id.in_(product_ids), Product.deleted_at.is_(None))
            .order_by(Product.id)
            .with_for_update()
        )
    }
    missing = [pid for pid in product_ids if pid not in products]
    if missing:
        raise NotFoundError(f"product not found: {missing[0]}")

    for item in data.items:
        product = products[item.product_id]
        if product.stock_quantity < item.quantity:
            raise InsufficientStockError(
                f"insufficient stock for product {product.id}: "
                f"have {product.stock_quantity}, requested {item.quantity}"
            )

    total = sum((products[i.product_id].price * i.quantity for i in data.items), start=Decimal("0"))
    sale = Sale(tenant_id=actor.tenant_id, cashier_id=cashier_id, total=total)
    db.add(sale)
    db.flush()

    items: list[SaleItem] = []
    for item in data.items:
        product = products[item.product_id]
        product.stock_quantity -= item.quantity
        line = SaleItem(
            tenant_id=actor.tenant_id,
            sale_id=sale.id,
            product_id=product.id,
            quantity=item.quantity,
            unit_price=product.price,
            line_total=product.price * item.quantity,
        )
        items.append(line)
        db.add(line)
        db.add(
            StockMovement(
                tenant_id=actor.tenant_id,
                product_id=product.id,
                quantity=-item.quantity,
                reason=MovementReason.SALE,
                created_by_id=actor.id,
                sale_id=sale.id,
            )
        )
    db.flush()
    audit.record(
        db,
        actor,
        "sale.create",
        "sale",
        sale.id,
        details={"total": str(total), "items": len(items), "cashier_id": cashier_id},
    )
    db.commit()
    return SaleWithItems(sale=sale, items=items)


def get_sale(db: Session, actor: User, sale_id: int) -> SaleWithItems:
    sale = db.scalars(scoped(Sale, actor.tenant_id).where(Sale.id == sale_id)).one_or_none()
    if sale is None:
        raise NotFoundError("sale not found")
    if actor.role == Role.CASHIER and sale.cashier_id != actor.id:
        raise NotFoundError("sale not found")
    items = list(
        db.scalars(
            scoped(SaleItem, actor.tenant_id)
            .where(SaleItem.sale_id == sale.id)
            .order_by(SaleItem.id)
        )
    )
    return SaleWithItems(sale=sale, items=items)


def list_sales(db: Session, actor: User, limit: int, offset: int) -> list[Sale]:
    stmt = scoped(Sale, actor.tenant_id)
    if actor.role == Role.CASHIER:
        stmt = stmt.where(Sale.cashier_id == actor.id)
    return list(db.scalars(stmt.order_by(Sale.id.desc()).limit(limit).offset(offset)))


def _bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    tz = clock.utcnow().tzinfo
    start = datetime.combine(date_from, time.min, tzinfo=tz)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


def sales_report(
    db: Session, actor: User, date_from: date, date_to: date, cashier_id: int | None = None
) -> SalesReport:
    """Aggregate sales in [date_from, date_to] (inclusive, UTC days)."""
    start, end = _bounds(date_from, date_to)
    filters = [Sale.tenant_id == actor.tenant_id, Sale.created_at >= start, Sale.created_at < end]
    if cashier_id is not None:
        if db.scalars(scoped(User, actor.tenant_id).where(User.id == cashier_id)).first() is None:
            raise NotFoundError("user not found")
        filters.append(Sale.cashier_id == cashier_id)

    day = cast(func.timezone("UTC", Sale.created_at), Date).label("day")
    daily_rows = db.execute(
        select(day, func.count(Sale.id), func.coalesce(func.sum(Sale.total), 0))
        .where(*filters)
        .group_by(day)
        .order_by(day)
    ).all()
    daily = [DailySales(day=d, sales_count=c, revenue=r) for d, c, r in daily_rows]

    qty = func.sum(SaleItem.quantity).label("qty")
    top_rows = db.execute(
        select(Product.id, Product.sku, Product.name, qty, func.sum(SaleItem.line_total))
        .select_from(SaleItem)
        .join(Sale, (Sale.id == SaleItem.sale_id) & (Sale.tenant_id == SaleItem.tenant_id))
        .join(
            Product, (Product.id == SaleItem.product_id) & (Product.tenant_id == SaleItem.tenant_id)
        )
        .where(SaleItem.tenant_id == actor.tenant_id, *filters)
        .group_by(Product.id, Product.sku, Product.name)
        .order_by(qty.desc(), Product.id)
        .limit(5)
    ).all()
    top = [
        TopProduct(product_id=pid, sku=sku, name=name, quantity=q, revenue=r)
        for pid, sku, name, q, r in top_rows
    ]
    items_sold = db.scalar(
        select(func.coalesce(func.sum(SaleItem.quantity), 0))
        .join(Sale, (Sale.id == SaleItem.sale_id) & (Sale.tenant_id == SaleItem.tenant_id))
        .where(SaleItem.tenant_id == actor.tenant_id, *filters)
    )
    return SalesReport(
        date_from=date_from,
        date_to=date_to,
        sales_count=sum(d.sales_count for d in daily),
        revenue=sum((d.revenue for d in daily), start=Decimal("0")),
        items_sold=int(items_sold or 0),
        daily=daily,
        top_products=top,
    )
