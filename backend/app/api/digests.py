from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import re

from app.services.push import digest_path

router = APIRouter(tags=["digests"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("/digests/{date}.md")
async def get_digest(date: str) -> FileResponse:
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail={"status": "error", "message": "日期格式应为 YYYY-MM-DD"})
    path = digest_path(date)
    if not path.exists():
        raise HTTPException(status_code=404, detail={"status": "error", "message": f"{date} 的 digest 尚未生成"})
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=f"{date}.md")
