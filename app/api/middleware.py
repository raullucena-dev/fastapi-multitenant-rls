import logging
import math
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.logging import request_id_var
from app.core.ratelimit import SlidingWindow

logger = logging.getLogger("app.request")

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        logger.info(
            "request.completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                "tenant_id": getattr(request.state, "tenant_id", None),
                "user_id": getattr(request.state, "user_id", None),
            },
        )
        request_id_var.reset(token)


_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def rate_limit(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Per-IP limits on POST /auth/login and on write endpoints (when enabled)."""
    settings = get_settings()
    if not settings.rate_limiting:
        return await call_next(request)
    if request.method == "POST" and request.url.path == "/auth/login":
        bucket, limit = "login", settings.rate_limit_login_per_minute
    elif request.method in _WRITE_METHODS:
        bucket, limit = "write", settings.rate_limit_writes_per_minute
    else:
        return await call_next(request)

    limiter: SlidingWindow = request.app.state.rate_limiter
    ip = request.client.host if request.client else "unknown"
    retry_after = limiter.hit(f"{bucket}|{ip}", limit)
    if retry_after is not None:
        return JSONResponse(
            status_code=429,
            content={"error": {"code": "rate_limited", "message": "too many requests"}},
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )
    return await call_next(request)
