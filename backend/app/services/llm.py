"""LLM 相关度打分：手写 OpenAI 兼容 HTTP 调用（不用任何 SDK）。

防坑要求（AGENTS.md）：
- 批量 10-15 篇/次；response_format=json_object + pydantic schema 校验
- 摘要按句子边界截断 ~600 字符（禁止字符比例截断）
- 批间 sleep 3s；每批立即写库 checkpoint（崩溃重跑不重复计费）
- 单批解析失败 stricter 重试 1 次，仍失败该批 score=null 照常入库
- 未配 key → 整体跳过打分；调用系统性失败 → 跳过本轮，服务照常可用
- token 预算按字符估算，绝不加载 tokenizer
"""

import asyncio
import json
import logging

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.db.store import set_paper_score

logger = logging.getLogger("paperradar.llm")

BATCH_SIZE = 12
BATCH_SLEEP_SECONDS = 3.0
ABSTRACT_CHAR_LIMIT = 600
PROMPT_CHAR_BUDGET = 24000  # 字符估算，避免加载 tokenizer
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ScoreItem(BaseModel):
    id: str
    score: float
    reason: str
    summary: str = ""


class ScoreResponse(BaseModel):
    scores: list[ScoreItem]


class LLMError(Exception):
    """系统性失败（鉴权错误、网络不可达等）→ 放弃本轮打分。"""


def truncate_at_sentence(text: str, limit: int = ABSTRACT_CHAR_LIMIT) -> str:
    """按句子边界截断；找不到边界则退化为硬截 + 省略号。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    best = -1
    for sep in (". ", "! ", "? ", "。", "！", "？"):
        idx = cut.rfind(sep)
        if idx > best:
            best = idx
    if best >= int(limit * 0.4):
        return cut[: best + (2 if cut[best] in ".!?" and cut[best : best + 1] else 1)].strip()
    return cut.rstrip() + "…"


def _build_user_content(profile: dict, papers: list[dict]) -> str:
    lines = [
        "研究画像：",
        f"- arXiv 分类：{'、'.join(profile.get('categories') or []) or '（不限）'}",
        f"- 关注关键词：{'、'.join(profile.get('keywords') or []) or '（不限）'}",
        f"- 排除方向：{'、'.join(profile.get('exclude_keywords') or []) or '（无）'}",
        "",
        "论文列表：",
    ]
    budget = PROMPT_CHAR_BUDGET
    for paper in papers:
        abstract = truncate_at_sentence(paper["abstract"])
        block = (
            f"[{paper['id']}] 标题：{paper['title']}\n"
            f"摘要：{abstract}\n"
        )
        if len("\n".join(lines)) + len(block) > budget:
            break
        lines.append(block)
    lines.append(
        "\n请对上面列出的每一篇论文输出："
        "① 相关度分数（0-100 整数，越高越相关）；"
        "② 一句话理由（不超过50字中文）；"
        "③ 中文摘要（2-4 句，150 字以内：解决什么问题、核心方法、关键结果或贡献；"
        "基于标题与摘要归纳，术语与模型名保留英文）。"
        '返回 JSON：{"scores":[{"id":"论文id","score":整数,"reason":"...","summary":"..."}]}，'
        "必须覆盖列出的所有 id，不要输出其他内容。"
    )
    return "\n".join(lines)


async def _chat(client: httpx.AsyncClient, user_content: str, stricter: bool) -> dict:
    settings = get_settings()
    system = (
        "你是论文相关度评估助手。根据用户的研究画像为论文打 0-100 的相关度分数，"
        "并给出简短中文理由。只输出 JSON 对象。"
    )
    if stricter:
        system += " 注意：输出必须是合法 JSON，顶层键为 scores，值为数组，不要加任何解释或代码块标记。"
    body = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 3400,
        "response_format": {"type": "json_object"},
    }
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json=body,
            )
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(4.0 * (2**attempt))
                continue
            raise LLMError(f"LLM 网络错误: {exc}") from exc
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in RETRYABLE_STATUS and attempt < 2:
            await asyncio.sleep(4.0 * (2**attempt))
            continue
        # 401/400 等确定性失败：重试无意义，直接放弃本轮
        raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
    raise LLMError(f"LLM 重试耗尽: {last_error}")


def _parse_scores(raw: dict, batch_ids: set[str]) -> list[ScoreItem]:
    content = (raw.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    data = json.loads(content)
    validated = ScoreResponse.model_validate(data)
    seen = {item.id: item for item in validated.scores if item.id in batch_ids}
    return list(seen.values())


async def score_papers(papers: list[dict], profile: dict) -> tuple[int, int]:
    """给未打分论文批量打分，逐批写库。返回 (成功打分数, 系统性放弃标志为 0)。"""
    settings = get_settings()
    if not settings.llm_api_key:
        logger.info("未配置 RADAR_LLM_API_KEY，跳过打分（服务照常可用）")
        return 0, 0
    if not papers:
        return 0, 0

    scored = 0
    async with httpx.AsyncClient(timeout=120.0) as client:
        for i in range(0, len(papers), BATCH_SIZE):
            batch = papers[i : i + BATCH_SIZE]
            batch_ids = {p["id"] for p in batch}
            user_content = _build_user_content(profile, batch)
            items: list[ScoreItem] | None = None
            for stricter in (False, True):
                try:
                    raw = await _chat(client, user_content, stricter)
                    items = _parse_scores(raw, batch_ids)
                    break
                except (json.JSONDecodeError, ValidationError, KeyError, IndexError) as exc:
                    logger.warning("批 %d 解析失败（%s），stricter 重试", i // BATCH_SIZE, exc)
                    continue
                except LLMError as exc:
                    logger.warning("LLM 系统性失败，跳过本轮剩余打分: %s", exc)
                    return scored, 0
            if items is None:
                # 该批两次都失败：score=null 照常入库（保持 NULL 即可），继续下一批
                logger.warning("批 %d 两次解析均失败，保留 score=null", i // BATCH_SIZE)
            else:
                for item in items:
                    score = max(0, min(100, round(float(item.score))))
                    set_paper_score(item.id, score, item.reason.strip(), item.summary.strip())
                    scored += 1
            if i + BATCH_SIZE < len(papers):
                await asyncio.sleep(BATCH_SLEEP_SECONDS)
    return scored, 0
