import asyncio

from fastapi import APIRouter, HTTPException, Query

from app.db import store
from app.schemas.paper import (
    FetchStartedResponse,
    PaperStatusPatch,
    PapersResponse,
    PapersStatusResponse,
    PaperOut,
    RunSummary,
)
from app.services import scheduler, tracker
from app.services.normalize import InvalidPaperInput, normalize_paper_input
from app.services.reader import ReaderError, reader_base, reader_configured, send_to_reader

router = APIRouter(tags=["papers"])


@router.get("/papers", response_model=PapersResponse)
async def list_papers(
    date: str | None = Query(default=None, description="YYYY-MM-DD，缺省为今天"),
    status: str | None = Query(default=None, pattern="^(new|starred|dismissed)$"),
    min_score: float | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=60, ge=1, le=200),
    start: int = Query(default=0, ge=0),
) -> PapersResponse:
    target_date = date or store.local_today()
    papers, total = store.list_papers(
        date=target_date, status=status, min_score=min_score, limit=limit, start=start
    )
    has_more = start + limit < total
    return PapersResponse(
        date=target_date,
        total=total,
        papers=[PaperOut(**p) for p in papers],
        has_more=has_more,
        next_start=start + limit if has_more else None,
    )


@router.get("/papers/status", response_model=PapersStatusResponse)
async def papers_status() -> PapersStatusResponse:
    latest = store.get_latest_run()
    last_summary = None
    if latest and latest.get("status") == "success":
        last_summary = RunSummary(
            fetched=latest["fetched"], scored=latest["scored"], alerted=latest["alerted"]
        )
    return PapersStatusResponse(
        running=tracker.is_running(),
        run_id=tracker.current_run_id(),
        last_run_at=(latest or {}).get("finished_at"),
        last_run_summary=last_summary,
        last_run_status=(latest or {}).get("status"),
        next_run_at=scheduler.get_next_run_at(),
        papers_today=store.count_papers_on_date(store.local_today()),
    )


@router.post("/papers/fetch", response_model=FetchStartedResponse)
async def trigger_fetch() -> FetchStartedResponse:
    if tracker.is_running():
        raise HTTPException(
            status_code=409,
            detail={"status": "running", "retry_after_seconds": 30},
        )
    asyncio.create_task(tracker.run_once(trigger="manual"))
    return FetchStartedResponse(status="started", message="抓取已启动，可轮询 /api/papers/status")


def _resolve_paper_id(raw_id: str) -> str:
    """路径参数统一过 normalize_paper_input：裸 id / abs 链接 / pdf 链接 / HF 链接均可。"""
    try:
        return normalize_paper_input(raw_id)["bare_id"]
    except InvalidPaperInput as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/papers/{paper_id:path}")
async def patch_paper(paper_id: str, payload: PaperStatusPatch) -> dict:
    bare_id = _resolve_paper_id(paper_id)
    updated = store.update_paper_status(bare_id, payload.status)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"论文不存在: {bare_id}")
    return {"status": "ok", "paper": updated}


@router.post("/papers/{paper_id:path}/read")
async def read_paper(paper_id: str) -> dict:
    bare_id = _resolve_paper_id(paper_id)
    if not reader_configured():
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "message": "精读服务未配置",
                "hint": "在 backend/.env 设置 RADAR_READER_BASE_URL（指向 local_ai_toolkit 等 reader 实例）后重启，速递卡片即可出现「去精读」。",
            },
        )
    paper = store.get_paper(bare_id)
    if paper is None:
        raise HTTPException(status_code=404, detail=f"论文不存在: {bare_id}")
    if not paper.get("pdf_url"):
        raise HTTPException(status_code=400, detail={"status": "error", "message": "该论文没有 PDF 链接"})
    try:
        result = await send_to_reader(paper["pdf_url"])
    except ReaderError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"status": "error", "message": str(exc)})
    store.mark_paper_read(bare_id, result.get("doc_name") or "")
    return {
        "status": "ok",
        "doc_name": result.get("doc_name"),
        "reader_url": reader_base(),
        "task": result.get("task"),
    }
