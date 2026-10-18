import sqlite3

from fastapi import APIRouter

from app.core.config import get_settings
from app.db.store import get_conn, utc_now_iso
from app.schemas.common import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    settings = get_settings()

    db_ok = True
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
    except sqlite3.Error:
        db_ok = False

    push_channels: list[str] = []
    if settings.feishu_webhook_url:
        push_channels.append("feishu")
    if settings.smtp_host:
        push_channels.append("smtp")

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        app=settings.app_name,
        version=settings.app_version,
        db="ok" if db_ok else "error",
        llm_configured=bool(settings.llm_api_key),
        reader_configured=bool(settings.reader_base_url),
        push_channels=push_channels,
        time=utc_now_iso(),
    )
