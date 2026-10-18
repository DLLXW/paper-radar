"""可选精读集成：代理转发到 reader 服务（local_ai_toolkit 的 POST /api/upload/url）。

未配置 reader 时接口返回 400 + 引导信息，核心体验完全不受影响（降级即产品）。
"""

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger("paperradar.reader")


class ReaderError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def reader_configured() -> bool:
    return bool(get_settings().reader_base_url)


def reader_base() -> str:
    return (get_settings().reader_base_url or "").rstrip("/")


async def send_to_reader(pdf_url: str) -> dict:
    """转发 PDF 链接给 reader 服务，返回其任务信息（含 doc_name）。"""
    base = reader_base()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{base}/api/upload/url", json={"url": pdf_url})
    except httpx.HTTPError as exc:
        raise ReaderError(f"精读服务连接失败（{base}）: {exc}") from exc
    if resp.status_code >= 400:
        raise ReaderError(
            f"精读服务返回 HTTP {resp.status_code}: {resp.text[:200]}",
            status_code=502,
        )
    try:
        task = resp.json()["task"]
        return {"doc_name": task.get("doc_name"), "task_id": task.get("id"), "task": task}
    except (ValueError, KeyError) as exc:
        raise ReaderError(f"精读服务响应格式异常: {resp.text[:200]}") from exc
