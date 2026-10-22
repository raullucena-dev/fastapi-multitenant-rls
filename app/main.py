import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.middleware import rate_limit, request_context
from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.logging import configure_logging
from app.core.ratelimit import SlidingWindow

logger = logging.getLogger("app")


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="fastapi-multitenant-rls",
        version="0.1.0",
        description="Multi-tenant retail backend with application- and database-level isolation.",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    app.state.rate_limiter = SlidingWindow(60)
    # Registered first so that request_context (added last, outermost) also logs 429s.
    app.middleware("http")(rate_limit)
    app.middleware("http")(request_context)
    app.include_router(api_router)

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(_: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("integrity_error", extra={"detail": str(exc.orig)})
        return _error(409, "conflict", "the request conflicts with existing data")

    return app


app = create_app()
