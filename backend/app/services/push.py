"""推送层：飞书 text + SMTP HTML 卡片 + digest markdown 工件 + 异常告警。

降级即产品：任何推送失败都只记日志，不影响抓取与速递台。
"""

import asyncio
import html
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.db.store import db_path

logger = logging.getLogger("paperradar.push")

FEISHU_TEXT_LIMIT = 28000


def score_tier(score) -> str:
    if score is None:
        return "unscored"
    if score >= 75:
        return "high"
    if score >= 50:
        return "mid"
    return "low"


TIER_LABEL = {"high": "🔥 高相关", "mid": "⭐ 中相关", "low": "低相关", "unscored": "未打分"}


def digest_dir() -> Path:
    path = get_settings().data_dir / "digests"
    path.mkdir(parents=True, exist_ok=True)
    return path


def digest_path(date: str) -> Path:
    return digest_dir() / f"{date}.md"


def _paper_meta(p: dict) -> str:
    """时间 · 会议 · 首作者，供 digest markdown 与邮件共用。"""
    parts = []
    if p.get("published_at"):
        parts.append(f"📅 {p['published_at'][:10]}")
    if p.get("venue"):
        parts.append(f"🎯 {p['venue']}")
    authors = p.get("authors") or []
    if authors:
        parts.append(f"✍️ {authors[0]}" + (" 等" if len(authors) > 1 else ""))
    return " · ".join(parts)


def _group_papers(papers: list[dict], profile: dict) -> list[tuple[str, list[tuple[str, list[dict]]]]]:
    """论文按 方向(group) → topic 两级分组，保持画像里的方向与 topic 顺序。

    论文只存 topic 名；group 由画像 topics 的顺序映射。未打分出 topic 的、
    以及画像已改名的遗留 topic，统一收进「其他」。无 topic 画像时退化为单组平铺。
    返回 [(group, [(topic, papers)])]，空分组不出现。
    """
    topic_to_group: dict[str, str] = {}
    groups: dict[str, dict[str, list[dict]]] = {}
    for t in profile.get("topics") or []:
        group = (t.get("group") or "").strip() or "未分组方向"
        topic_to_group[t["name"]] = group
        groups.setdefault(group, {}).setdefault(t["name"], [])
    if not groups:
        groups["全部论文"] = {}
    others = groups.setdefault("其他", {})
    for p in papers:
        topic = (p.get("topic") or "").strip()
        group = topic_to_group.get(topic)
        if group is not None:
            groups[group][topic].append(p)
        else:
            others.setdefault(topic or "未分类", []).append(p)
    result = []
    for group, topic_map in groups.items():
        nonempty = [(name, ps) for name, ps in topic_map.items() if ps]
        if nonempty:
            result.append((group, nonempty))
    return result


def build_digest_markdown(date: str, papers: list[dict], profile: dict) -> str:
    topics = profile.get("topics") or []
    lines = [
        f"# 📚 PaperRadar 日报 - {date}",
        "",
        f"> 画像：{len(topics)} 个 topic（{'、'.join(t['name'] for t in topics) or '无'}）"
        f" · 分类：{' / '.join(profile.get('categories') or []) or '不限'} · 共 {len(papers)} 篇",
        "",
    ]
    if not papers:
        lines.append("今日没有符合条件的论文。")
        return "\n".join(lines)
    idx = 0
    for group, topic_list in _group_papers(papers, profile):
        group_count = sum(len(ps) for _, ps in topic_list)
        lines += ["", f"## 🧭 {group}（{group_count} 篇）"]
        for topic, tps in topic_list:
            lines += ["", f"### {topic}（{len(tps)} 篇）"]
            for p in tps:
                idx += 1
                score = int(p["score"]) if p["score"] is not None else "-"
                lines.append(
                    f"#### {idx}. {TIER_LABEL[score_tier(p['score'])]} [{p['title']}]({p['arxiv_url']}) · {score} 分"
                )
                meta = _paper_meta(p)
                if meta:
                    lines.append(f"- {meta}")
                if p.get("reason"):
                    lines.append(f"- 理由：{p['reason']}")
                if p.get("summary"):
                    lines.append(f"- 中文摘要：{p['summary']}")
                lines.append(
                    f"- [abs]({p['arxiv_url']}) · [pdf]({p.get('pdf_url') or p['arxiv_url'].replace('/abs/', '/pdf/')})"
                )
                lines.append("")
    return "\n".join(lines)


def build_feishu_text(date: str, papers: list[dict], profile: dict | None = None) -> str:
    lines = [f"📚 PaperRadar 日报 - {date}", ""]
    truncated = False
    idx = 0
    for group, topic_list in _group_papers(papers, profile or {}):
        header = f"—— {group} ——"
        if sum(len(l) for l in lines) + len(header) > FEISHU_TEXT_LIMIT:
            truncated = True
            break
        lines.append(header)
        for topic, tps in topic_list:
            sub = f"▶ {topic}（{len(tps)} 篇）"
            if sum(len(l) for l in lines) + len(sub) > FEISHU_TEXT_LIMIT:
                truncated = True
                break
            lines.append(sub)
            for p in tps:
                idx += 1
                score = int(p["score"]) if p["score"] is not None else "-"
                meta_bits = [p.get("published_at", "")[:10]]
                if p.get("venue"):
                    meta_bits.append(p["venue"])
                entry = (
                    f"{idx}. {TIER_LABEL[score_tier(p['score'])]} {score}分 [ {' · '.join(b for b in meta_bits if b)} ]\n"
                    f"{p['title']}\n"
                )
                if p.get("reason"):
                    entry += f"理由：{p['reason']}\n"
                entry += f"abs: {p['arxiv_url']}\npdf: {p.get('pdf_url') or ''}\n"
                if sum(len(l) for l in lines) + len(entry) > FEISHU_TEXT_LIMIT:
                    truncated = True
                    break
                lines.append(entry)
            if truncated:
                break
        if truncated:
            break
    if truncated:
        lines.append("……（篇幅所限，其余论文见速递台）")
    return "\n".join(lines)


def _badge_html(score) -> str:
    tier = score_tier(score)
    colors = {"high": "#0a6b55", "mid": "#b07a1e", "low": "#8a94a0", "unscored": "#8a94a0"}
    label = f"{int(score)} 分" if score is not None else "未打分"
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:12px;'
        f'background:{colors[tier]};color:#fff;font-size:12px;font-weight:600">{label}</span>'
    )


def _card_html(p: dict, idx: int) -> str:
    reason = (
        f'<p style="margin:6px 0;color:#0a6b55;font-size:13px">💡 {html.escape(p["reason"])}</p>'
        if p.get("reason")
        else ""
    )
    pdf_url = p.get("pdf_url") or p["arxiv_url"].replace("/abs/", "/pdf/")

    # 元信息行：时间 · 会议（如有）· 首作者
    meta_bits = [f"#{idx} · {p.get('primary_category') or 'arXiv'}"]
    if p.get("published_at"):
        meta_bits.append(f"📅 {p['published_at'][:10]}")
    if p.get("venue"):
        meta_bits.append(
            f'<b style="color:#b07a1e">🎯 {html.escape(p["venue"])}</b>'
        )
    authors = p.get("authors") or []
    if authors:
        first = html.escape(authors[0]) + (" 等" if len(authors) > 1 else "")
        meta_bits.append(f"✍️ {first}")

    # 摘要双折叠：LLM 中文摘要为主，arXiv 原文摘要为辅。
    # 支持 <details> 的客户端（QQ邮箱App/Gmail等）折叠，
    # 不支持的（Apple Mail/Outlook桌面）展开显示——优雅降级
    abstract_html = ""
    if p.get("summary"):
        abstract_html += f'''<details style="margin:6px 0">
<summary style="font-size:12px;color:#0f8a6d;cursor:pointer">🧠 展开中文摘要（LLM 归纳）</summary>
<p style="margin:8px 0 2px;font-size:13px;line-height:1.7;color:#3a463f;white-space:pre-wrap">{html.escape(p["summary"])}</p>
</details>'''
    if p.get("abstract"):
        abstract_html += f'''<details style="margin:4px 0">
<summary style="font-size:12px;color:#8a948e;cursor:pointer">📄 展开原文摘要（arXiv Abstract）</summary>
<p style="margin:8px 0 2px;font-size:12.5px;line-height:1.7;color:#6f7f78;white-space:pre-wrap">{html.escape(p["abstract"][:2000])}</p>
</details>'''

    return f'''<div style="border:1px solid #e3e8e6;border-radius:12px;padding:14px 16px;margin:10px 0;background:#fff">
  <div style="font-size:12px;color:#98a29c;margin-bottom:4px">{' · '.join(meta_bits)}</div>
  <div style="margin-bottom:6px">{_badge_html(p["score"])}
    <a href="{p["arxiv_url"]}" style="color:#17251f;font-size:15px;font-weight:600;text-decoration:none;margin-left:8px">{html.escape(p["title"])}</a>
  </div>
  {reason}
  {abstract_html}
  <div style="font-size:12px">
    <a href="{p["arxiv_url"]}" style="color:#0f8a6d;margin-right:12px">abs</a>
    <a href="{pdf_url}" style="color:#0f8a6d">pdf</a>
  </div>
</div>'''


def build_email_html(date: str, papers: list[dict], profile: dict | None = None) -> str:
    sections = []
    idx = 0
    for group, topic_list in _group_papers(papers, profile or {}):
        parts = [
            f'<h2 style="margin:20px 0 6px;color:#17251f;font-size:17px;border-bottom:2px solid '
            f'#0f8a6d;padding-bottom:6px">🧭 {html.escape(group)}'
            f"<span style='font-size:12px;color:#6f7f78;font-weight:400'>（{sum(len(ps) for _, ps in topic_list)} 篇）</span></h2>"
        ]
        for topic, tps in topic_list:
            parts.append(
                f'<h3 style="margin:12px 0 2px;color:#0f8a6d;font-size:14px">📌 {html.escape(topic)}'
                f"<span style='font-size:12px;color:#98a29c;font-weight:400'>（{len(tps)} 篇）</span></h3>"
            )
            for p in tps:
                idx += 1
                parts.append(_card_html(p, idx))
        sections.append("".join(parts))
    body = "".join(sections) or "<p>今日没有符合条件的论文。</p>"
    grouped = bool(profile and profile.get("topics"))
    subtitle = "按方向与 topic 分组" if grouped else "按相关度排序"
    return f"""<!doctype html><html><body style="margin:0;background:#f2f6f4;padding:24px;font-family:'PingFang SC',sans-serif">
<h2 style="margin:0 0 4px;color:#17251f">📚 PaperRadar 日报 - {date}</h2>
<p style="margin:0 0 12px;color:#6f7f78;font-size:13px">共 {len(papers)} 篇 · {subtitle}</p>
{body}
</body></html>"""


async def send_feishu(text: str) -> dict:
    settings = get_settings()
    if not settings.feishu_webhook_url:
        return {"status": "skipped", "reason": "未配置 RADAR_FEISHU_WEBHOOK_URL"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                settings.feishu_webhook_url,
                json={"msg_type": "text", "content": {"text": text}},
            )
        data = resp.json()
        code = data.get("code", data.get("StatusCode", resp.status_code))
        if code == 0:
            return {"status": "ok"}
        return {"status": "error", "error": f"飞书返回 code={code}: {str(data)[:200]}"}
    except Exception as exc:  # noqa: BLE001 — 推送失败不能影响主流程
        logger.warning("飞书推送失败: %s", exc)
        return {"status": "error", "error": str(exc)}


def _send_smtp_sync(subject: str, html: str) -> dict:
    settings = get_settings()
    if not settings.smtp_host:
        return {"status": "skipped", "reason": "未配置 RADAR_SMTP_HOST"}
    try:
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = settings.smtp_from or settings.smtp_user
        message["To"] = settings.smtp_to or settings.smtp_user
        message.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            server.ehlo()
            server.starttls()  # 587 STARTTLS
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(
                message["From"], [message["To"]], message.as_string()
            )
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("SMTP 推送失败: %s", exc)
        return {"status": "error", "error": str(exc)}


async def send_email(subject: str, html: str) -> dict:
    return await asyncio.to_thread(_send_smtp_sync, subject, html)


def write_digest_file(date: str, content: str) -> Path:
    """digest 工件：data/digests/YYYY-MM-DD.md（未来 MCP/Agent skill 直读）。"""
    path = digest_path(date)
    path.write_text(content, encoding="utf-8")
    return path


async def push_digest(date: str, papers: list[dict], profile: dict) -> dict:
    """写 digest 工件（始终）+ 推送已配置的渠道（失败仅记日志）。"""
    results: dict = {}
    write_digest_file(date, build_digest_markdown(date, papers, profile))
    if papers:
        feishu = await send_feishu(build_feishu_text(date, papers, profile))
        email = await send_email(f"📚 PaperRadar 日报 - {date}", build_email_html(date, papers, profile))
        results = {"feishu": feishu, "smtp": email}
    return results


async def send_alert(text: str) -> None:
    """异常告警（0 篇/运行失败）：只走飞书；未配置则记日志。"""
    settings = get_settings()
    if not settings.feishu_webhook_url:
        logger.error("告警（未配置飞书，仅日志）: %s", text)
        return
    await send_feishu(text)


async def push_test() -> dict:
    """推送配置自检：向所有已配置渠道发测试消息。"""
    results = {}
    settings = get_settings()
    results["feishu"] = await send_feishu("✅ PaperRadar 推送测试：配置正常。")
    results["smtp"] = await send_email(
        "📚 PaperRadar 推送测试", "<p>✅ PaperRadar 推送测试：配置正常。</p>"
    )
    results["digest_dir"] = {"status": "ok", "path": str(digest_dir())}
    results["db"] = {"status": "ok", "path": str(db_path())}
    results["_configured"] = {
        "feishu": bool(settings.feishu_webhook_url),
        "smtp": bool(settings.smtp_host),
    }
    return results
