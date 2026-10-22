from fastapi import APIRouter

from app.api.routes import audit, auth, demo, health, products, sales, stock, users

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(products.router)
api_router.include_router(stock.router)
api_router.include_router(sales.router)
api_router.include_router(audit.router)
api_router.include_router(demo.router)
