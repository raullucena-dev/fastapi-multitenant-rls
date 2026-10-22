from datetime import date

from fastapi import APIRouter, Query, status

from app.api.deps import AnyUser, DbSession, ManagerUser, Pagination
from app.core.errors import DomainError
from app.schemas.sales import SaleCreate, SaleOut, SalesReport, SaleSummary
from app.services import sales as sales_service

router = APIRouter(tags=["sales"])

MAX_REPORT_DAYS = 366


@router.post("/sales", status_code=status.HTTP_201_CREATED)
def create_sale(payload: SaleCreate, actor: AnyUser, db: DbSession) -> SaleOut:
    return SaleOut.model_validate(sales_service.create_sale(db, actor, payload))


@router.get("/sales")
def list_sales(actor: AnyUser, db: DbSession, page: Pagination) -> list[SaleSummary]:
    return [SaleSummary.model_validate(s) for s in sales_service.list_sales(db, actor, *page)]


@router.get("/sales/{sale_id}")
def get_sale(sale_id: int, actor: AnyUser, db: DbSession) -> SaleOut:
    return SaleOut.model_validate(sales_service.get_sale(db, actor, sale_id))


@router.get("/reports/sales")
def sales_report(
    actor: ManagerUser,
    db: DbSession,
    date_from: date = Query(...),
    date_to: date = Query(...),
    cashier_id: int | None = None,
) -> SalesReport:
    if date_to < date_from:
        raise DomainError("date_to must not be before date_from")
    if (date_to - date_from).days >= MAX_REPORT_DAYS:
        raise DomainError(f"date range must not exceed {MAX_REPORT_DAYS} days")
    return sales_service.sales_report(db, actor, date_from, date_to, cashier_id)
