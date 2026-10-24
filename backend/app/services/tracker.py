"""run_once 编排：抓取 → 去重入库 → v2 检测 → LLM 打分 → 清理 → digest/推送 → 0篇告警。

防坑要求（AGENTS.md）：
- 时间窗显式：上次成功运行 last_run_at（含 1h 重叠）→ now；首次运行回看 7 天
- 0 篇告警：全部查询成功但都得 0 篇且 HF 也为空 → 飞书告警，绝不静默成功
- 降级即产品：LLM/推送失败不影响抓取入库与速递台
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import get_settings
from app.db import store
from app.services import llm, push
from app.services.sources import fetch_arxiv_for_keywords, fetch_hf_daily

logger = logging.getLogger("paperradar.tracker")

FIRST_RUN_LOOKBACK_DAYS = 7
WINDOW_OVERLAP = timedelta(hours=1)

_state = {"running": False, "run_id": None, "started_at": None}


def _parse_keyword(raw: str) -> tuple[str, list[str] | None]:
    """解析关键词的 @分类 覆盖后缀：`scaling law @cs.IR` → ("scaling law", ["cs.IR"])。

    后缀支持逗号分隔多个分类（`kw @cs.IR,cs.LG`）；不带后缀返回 (原文, None)。
    """
    text = raw.strip()
    if "@" in text:
        head, _, tail = text.rpartition("@")
        cats = [c.strip() for c in tail.split(",") if c.strip()]
        if cats and head.strip():
            return head.strip(), cats
    return text, None


def _keyword_specs(profile: dict) -> list[tuple[str, list[str] | None]]:
    """画像所有 topic 的关键词展开为 (关键词, 分类覆盖) 列表，保持画像顺序。"""
    specs: list[tuple[str, list[str] | None]] = []
    for topic in profile.get("topics") or []:
        for raw in topic.get("keywords") or []:
            keyword, override = _parse_keyword(raw)
            if keyword:
                specs.append((keyword, override))
    return specs


def is_running() -> bool:
    return _state["running"]


def current_run_id() -> int | None:
    return _state["run_id"]


def _window_start() -> datetime:
    last = store.get_last_successful_run()
    if last and last.get("finished_at"):
        try:
            base = datetime.fromisoformat(last["finished_at"].replace("Z", "+00:00"))
            return base.astimezone(timezone.utc) - WINDOW_OVERLAP
        except ValueError:
            pass
    return datetime.now(timezone.utc) - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)


def _apply_excludes(papers: dict[str, dict], exclude_keywords: list[str]) -> int:
    """按排除词过滤（标题/摘要不区分大小写匹配）。返回过滤掉的篇数。"""
    if not exclude_keywords:
        return 0
    dropped = []
    for pid, paper in papers.items():
        haystack = f"{paper['title']}\n{paper['abstract']}".lower()
        if any(word.lower() in haystack for word in exclude_keywords if word.strip()):
            dropped.append(pid)
    for pid in dropped:
        papers.pop(pid, None)
    return len(dropped)


async def run_once(trigger: str = "manual") -> dict:
    """执行一轮完整抓取。并发调用由 is_running/状态位挡住（单进程假设）。"""
    if _state["running"]:
        return {"status": "running", "run_id": _state["run_id"]}
    _state.update(running=True, run_id=None, started_at=store.utc_now_iso())
    settings = get_settings()
    run_id = store.create_run(trigger)
    _state["run_id"] = run_id

    today = store.local_today()
    profile = store.get_profile()
    keyword_specs = _keyword_specs(profile)
    categories = [c.strip() for c in profile["categories"] if c.strip()]
    excludes = [e.strip() for e in profile["exclude_keywords"] if e.strip()]
    daily_limit = int(profile["daily_limit"])
    window_start = _window_start()
    window_end = datetime.now(timezone.utc)

    inserted = version_updates = scored = alerted = 0
    summary_queries: list[dict] = []
    error: str | None = None

    try:
        fetched: dict[str, dict] = {}
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            if keyword_specs:
                # 显式时间窗 + max_results = 3 x 每日上限（禁止排序+截断的隐式窗口）
                arxiv_papers, summary_queries = await fetch_arxiv_for_keywords(
                    client, keyword_specs, categories, window_start, window_end,
                    max_results=daily_limit * 3,
                )
                for paper in arxiv_papers:
                    fetched[paper["id"]] = paper
            else:
                logger.info("画像关键词为空，跳过 arXiv 关键词查询")

            hf_papers = await fetch_hf_daily(client, today)
            for paper in hf_papers:
                if paper["id"] in fetched:
                    fetched[paper["id"]]["matched_keywords"] = sorted(
                        set(fetched[paper["id"]]["matched_keywords"] + ["hf:daily"])
                    )
                else:
                    paper["matched_keywords"] = ["hf:daily"]
                    fetched[paper["id"]] = paper

        dropped = _apply_excludes(fetched, excludes)
        for paper in fetched.values():
            paper["fetched_date"] = today
            action = store.upsert_paper(paper)
            if action == "inserted":
                inserted += 1
            elif action == "version_update":
                version_updates += 1

        # 打分（无 key 整体跳过；每批 checkpoint 已在 llm 内完成）
        unscored = store.get_unscored_papers()
        if unscored:
            scored, _ = await llm.score_papers(unscored, profile)

        # 保留期清理 + runs 收敛
        pruned = store.prune_papers(settings.paper_retention_days)
        store.prune_runs()
        if pruned:
            logger.info("清理超期论文 %d 篇（保留 %d 天）", pruned, settings.paper_retention_days)

        # digest 工件 + 推送
        digest_papers = store.papers_for_digest(today, daily_limit)
        push_results = await push.push_digest(today, digest_papers, profile)
        alerted = len(digest_papers) if any(
            r.get("status") == "ok" for r in push_results.values()
        ) else 0

        # 0 篇告警：所有关键词查询都成功执行但均得 0 篇，且 HF 也为空
        all_zero = bool(summary_queries) and all(q["count"] == 0 for q in summary_queries)
        if all_zero and not fetched:
            await push.send_alert(
                f"⚠️ PaperRadar 异常告警（{today}）：本轮 {len(summary_queries)} 个关键词查询全部返回 0 篇，"
                "HF Daily Papers 也为空，疑似上游异常，请检查 arXiv API 与画像配置。"
            )
            logger.error("0 篇告警已触发：全部查询为空")

        store.finish_run(
            run_id, "success",
            fetched=inserted + version_updates, scored=scored, alerted=alerted,
        )
        summary = {
            "status": "ok",
            "run_id": run_id,
            "fetched": inserted + version_updates,
            "new": inserted,
            "version_updates": version_updates,
            "excluded": dropped,
            "scored": scored,
            "alerted": alerted,
            "queries": summary_queries,
            "hf_count": len(hf_papers),
            "window": [window_start.isoformat(), window_end.isoformat()],
        }
        logger.info("run #%s 完成: %s", run_id, {k: v for k, v in summary.items() if k != "queries"})
        return summary

    except Exception as exc:  # noqa: BLE001 — 顶层兜底：一轮失败不能打死服务
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("run #%s 失败", run_id)
        store.finish_run(run_id, "error", error=error)
        try:
            await push.send_alert(f"⚠️ PaperRadar 运行失败（{today}）：{error[:300]}")
        except Exception:  # noqa: BLE001
            logger.exception("失败告警发送异常")
        return {"status": "error", "run_id": run_id, "error": error}
    finally:
        _state.update(running=False, run_id=None, started_at=None)
