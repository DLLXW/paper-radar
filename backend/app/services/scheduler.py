"""asyncio lifespan 调度循环（不用 APScheduler）：每天 fetch_hour 本地时间跑一轮。

启动补跑：若上次成功运行距今超过 20 小时（服务停过/首次部署），启动时补跑一次，
保证 docker run 后无需等到明天就能看到数据。
"""

import asyncio
import logging
from datetime import datetime, timedelta

from app.core.config import get_settings
from app.db import store
from app.services.tracker import run_once

logger = logging.getLogger("paperradar.scheduler")

CATCHUP_THRESHOLD = timedelta(hours=20)

_next_run_at: datetime | None = None
_task: asyncio.Task | None = None


def compute_next_run(now: datetime | None = None) -> datetime:
    settings = get_settings()
    now = now or datetime.now()
    target = now.replace(hour=settings.fetch_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def get_next_run_at() -> str | None:
    global _next_run_at
    if _next_run_at is None:
        _next_run_at = compute_next_run()
    return _next_run_at.strftime("%Y-%m-%dT%H:%M:%S")


async def _loop() -> None:
    global _next_run_at
    while True:
        _next_run_at = compute_next_run()
        wait = (_next_run_at - datetime.now()).total_seconds()
        logger.info("下次定时抓取: %s（%.0f 秒后）", _next_run_at, max(wait, 0))
        try:
            await asyncio.sleep(max(wait, 1))
            await run_once(trigger="scheduled")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 调度循环永死是最大事故
            logger.exception("调度循环异常，10 分钟后重试")
            await asyncio.sleep(600)


async def start_scheduler() -> None:
    global _task
    if _task is not None:
        return
    last = store.get_last_successful_run()
    needs_catchup = True
    if last and last.get("finished_at"):
        try:
            finished = datetime.fromisoformat(last["finished_at"].replace("Z", "+00:00"))
            needs_catchup = datetime.now().astimezone() - finished.astimezone() > CATCHUP_THRESHOLD
        except ValueError:
            needs_catchup = True
    _task = asyncio.create_task(_loop())
    if needs_catchup:
        logger.info("距上次成功运行超过 %.0f 小时，启动补跑", CATCHUP_THRESHOLD.total_seconds() / 3600)
        asyncio.create_task(run_once(trigger="scheduled"))


async def stop_scheduler() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
