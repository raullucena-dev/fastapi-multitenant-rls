from typing import Any

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.demo import demo_info

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/info")
def info() -> dict[str, Any]:
    """Public demo credentials for the frontend login page. 404 unless DEMO_MODE=true."""
    if not get_settings().demo_mode:
        raise NotFoundError("not found")
    return demo_info()
