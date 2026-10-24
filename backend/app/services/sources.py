"""情报源抓取：arXiv Atom API + Hugging Face Daily Papers。

防坑要求（AGENTS.md）：
- 请求间隔 >= 3s；429/5xx 指数退避；识别 HTTP 200 但 body 为 "Rate exceeded." 的软限速
- 查询串程序化拼接，用户只提供关键词/分类，不手写原始查询
- 显式 submittedDate 时间窗 + max_results = 3 x 每日上限（禁止排序+截断的隐式窗口）
- 单个查询失败只记日志，不拖垮整轮
- 循环内零外部富化调用
"""

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger("paperradar.sources")

ARXIV_API = "https://export.arxiv.org/api/query"
HF_DAILY_API = "https://huggingface.co/api/daily_papers"
USER_AGENT = "PaperRadar/0.1 (arXiv TOU-compliant daily digest; +local instance)"

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
RETRY_STATUS = {429, 500, 502, 503, 504}
MIN_REQUEST_INTERVAL = 3.0  # arXiv TOU：请求间隔 >= 3 秒
MAX_RETRIES = 3

_last_request_at = 0.0  # 全局间距游标（跨查询生效）


class FetchError(Exception):
    pass


async def _respect_spacing() -> None:
    global _last_request_at
    now = asyncio.get_event_loop().time()
    wait = _last_request_at + MIN_REQUEST_INTERVAL - now
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request_at = asyncio.get_event_loop().time()


async def _get_with_retry(client: httpx.AsyncClient, url: str, params: dict) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        await _respect_spacing()
        try:
            resp = await client.get(url, params=params, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                await asyncio.sleep(3.0 * (2**attempt))
                continue
            raise FetchError(f"{url} 网络错误: {exc}") from exc
        if resp.status_code == 200:
            # 软限速：HTTP 200 但 body 是 "Rate exceeded."（paper-search-mcp 事故）
            if resp.text.strip().startswith("Rate exceeded"):
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(5.0 * (2**attempt))
                    continue
                raise FetchError(f"{url} 软限速（Rate exceeded.）持续出现")
            return resp
        if resp.status_code in RETRY_STATUS and attempt < MAX_RETRIES:
            await asyncio.sleep(3.0 * (2**attempt))
            continue
        raise FetchError(f"{url} HTTP {resp.status_code}")
    raise FetchError(f"{url} 重试耗尽: {last_error}")


def build_arxiv_query(keyword: str, categories: list[str], window_start: datetime, window_end: datetime) -> str:
    """程序化拼接 (kw) AND (cat:A OR cat:B) AND submittedDate:[a TO b]；禁止手写原始查询串。"""
    kw = keyword.strip().replace('"', "")
    kw_part = f'"{kw}"' if " " in kw else kw
    parts = [f"({kw_part})"]
    if categories:
        cat_part = " OR ".join(f"cat:{c.strip()}" for c in categories if c.strip())
        if cat_part:
            parts.append(f"({cat_part})")
    parts.append(
        f"submittedDate:[{_format_arxiv_time(window_start)} TO {_format_arxiv_time(window_end)}]"
    )
    return " AND ".join(parts)


def _format_arxiv_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _parse_arxiv_atom(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    papers = []
    for entry in root.findall("atom:entry", ATOM_NS):
        id_url = (entry.findtext("atom:id", default="", namespaces=ATOM_NS) or "").strip()
        raw_id = id_url.split("/abs/")[-1] if "/abs/" in id_url else ""
        if not raw_id:
            continue
        m = re.match(r"^(.+?)(?:v(\d+))?$", raw_id)
        bare_id, ver = m.group(1), m.group(2) or "1"

        pdf_url = None
        for link in entry.findall("atom:link", ATOM_NS):
            if link.get("title") == "pdf":
                pdf_url = link.get("href")
                break
        primary = entry.find("arxiv:primary_category", ATOM_NS)
        comment = entry.findtext("arxiv:comment", default=None, namespaces=ATOM_NS)

        papers.append(
            {
                "id": bare_id,
                "version": f"v{ver}",
                "source": "arxiv",
                "title": _squash(entry.findtext("atom:title", default="", namespaces=ATOM_NS)),
                "authors": [
                    _squash(name.text or "")
                    for name in entry.findall("atom:author/atom:name", ATOM_NS)
                ],
                "abstract": _squash(entry.findtext("atom:summary", default="", namespaces=ATOM_NS)),
                "categories": [c.get("term") for c in entry.findall("atom:category", ATOM_NS) if c.get("term")],
                "primary_category": primary.get("term") if primary is not None else None,
                "arxiv_url": f"https://arxiv.org/abs/{bare_id}",
                "pdf_url": pdf_url or f"https://arxiv.org/pdf/{bare_id}",
                "published_at": _to_utc_iso(entry.findtext("atom:published", default="", namespaces=ATOM_NS)),
                "updated_at": _to_utc_iso(entry.findtext("atom:updated", default="", namespaces=ATOM_NS)),
                "comment": _squash(comment) if comment else None,
            }
        )
    return papers


def _to_utc_iso(raw: str) -> str:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_arxiv_for_keywords(
    client: httpx.AsyncClient,
    keyword_specs: list[tuple[str, list[str] | None]],
    categories: list[str],
    window_start: datetime,
    window_end: datetime,
    max_results: int,
) -> tuple[list[dict], list[dict]]:
    """逐关键词查询。返回 (合并去重后的论文列表, 每个查询的结果数)。

    keyword_specs 为 (清洗后关键词, 分类覆盖或 None) 列表；覆盖时该关键词只在指定
    分类内查询（如 "scaling law @cs.IR"），否则用全局 categories。
    单个查询失败仅记日志（防坑 5）；合并时累加 matched_keywords。
    """
    merged: dict[str, dict] = {}
    per_query: list[dict] = []
    for keyword, override in keyword_specs:
        query = build_arxiv_query(keyword, override or categories, window_start, window_end)
        params = {
            "search_query": query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "start": 0,
            "max_results": max_results,
        }
        try:
            resp = await _get_with_retry(client, ARXIV_API, params)
            entries = _parse_arxiv_atom(resp.text)
        except (FetchError, ET.ParseError) as exc:
            logger.warning("arXiv 查询失败（不影响其余查询）kw=%r: %s", keyword, exc)
            per_query.append({"keyword": keyword, "count": -1, "error": str(exc)})
            continue
        per_query.append({"keyword": keyword, "count": len(entries)})
        for paper in entries:
            if paper["id"] in merged:
                merged[paper["id"]]["matched_keywords"] = sorted(
                    set(merged[paper["id"]]["matched_keywords"] + [keyword])
                )
            else:
                paper["matched_keywords"] = [keyword]
                merged[paper["id"]] = paper
    return list(merged.values()), per_query


async def fetch_hf_daily(client: httpx.AsyncClient, date: str) -> list[dict]:
    """HF Daily Papers：小而精的策展源，整体纳入后再走统一打分。当天为空时回退昨天。"""
    papers: list[dict] = []
    for day in (date, _yesterday()):
        try:
            resp = await _get_with_retry(client, HF_DAILY_API, {"date": day})
            items = resp.json()
        except (FetchError, ValueError) as exc:
            logger.warning("HF daily_papers 获取失败 date=%s: %s", day, exc)
            continue
        if items:
            papers = [_map_hf_item(item) for item in items]
            break
    return papers


def _yesterday() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _map_hf_item(item: dict) -> dict:
    paper = item.get("paper") or {}
    raw_id = str(paper.get("id") or "").strip()
    m = re.match(r"^(.+?)(?:v(\d+))?$", raw_id)
    bare_id, ver = m.group(1), m.group(2) or "1"
    return {
        "id": bare_id,
        "version": f"v{ver}",
        "source": "hf_daily",
        "title": _squash(paper.get("title") or ""),
        "authors": [a.get("name", "") for a in paper.get("authors", []) if a.get("name")],
        "abstract": _squash(paper.get("summary") or ""),
        "categories": [],
        "primary_category": None,
        "arxiv_url": f"https://arxiv.org/abs/{bare_id}",
        "pdf_url": f"https://arxiv.org/pdf/{bare_id}",
        "published_at": _to_utc_iso(paper.get("publishedAt") or ""),
        "updated_at": _to_utc_iso(item.get("publishedAt") or paper.get("publishedAt") or ""),
        "matched_keywords": [],
        "comment": None,
    }


async def fetch_arxiv_comments(client: httpx.AsyncClient, paper_ids: list[str]) -> dict[str, str | None]:
    """按裸 id 批量拉取 comment（id_list 一次请求几十个），用于存量回填。

    这是循环外的一次性维护调用；正常抓取流程在关键词查询里已带回 comment。
    """
    result: dict[str, str | None] = {}
    for i in range(0, len(paper_ids), 40):
        chunk = paper_ids[i : i + 40]
        params = {"id_list": ",".join(chunk), "max_results": len(chunk)}
        try:
            resp = await _get_with_retry(client, ARXIV_API, params)
            for paper in _parse_arxiv_atom(resp.text):
                result[paper["id"]] = paper.get("comment")
        except (FetchError, ET.ParseError) as exc:
            logger.warning("id_list 回填失败（chunk %d）: %s", i // 40, exc)
    return result
