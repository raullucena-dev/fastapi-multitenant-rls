from fastapi import APIRouter, status

from app.api.deps import AdminUser, AnyUser, DbSession, ManagerUser, Pagination
from app.schemas.products import ProductCreate, ProductDeleted, ProductOut, ProductUpdate
from app.services import products as products_service

router = APIRouter(prefix="/products", tags=["products"])


@router.get("")
def list_products(
    actor: AnyUser, db: DbSession, page: Pagination, q: str | None = None
) -> list[ProductOut]:
    items = products_service.list_products(db, actor, *page, search=q)
    return [ProductOut.model_validate(p) for p in items]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, actor: ManagerUser, db: DbSession) -> ProductOut:
    return ProductOut.model_validate(products_service.create_product(db, actor, payload))


@router.get("/{product_id}")
def get_product(product_id: int, actor: AnyUser, db: DbSession) -> ProductOut:
    return ProductOut.model_validate(products_service.get_product(db, actor, product_id))


@router.patch("/{product_id}")
def update_product(
    product_id: int, payload: ProductUpdate, actor: ManagerUser, db: DbSession
) -> ProductOut:
    product = products_service.update_product(db, actor, product_id, payload)
    return ProductOut.model_validate(product)


@router.delete("/{product_id}")
def delete_product(product_id: int, actor: AdminUser, db: DbSession) -> ProductDeleted:
    mode = products_service.delete_product(db, actor, product_id)
    return ProductDeleted(id=product_id, mode=mode)
