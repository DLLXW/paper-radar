"""sqlite3 存储层：papers / runs / profile 的连接管理、建表与全部查询。

只用 stdlib sqlite3（单文件库）。list 型字段（authors/categories/matched_keywords）
以 JSON 文本落库，出库时由 `_row_to_paper` 还原为 list。
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import get_settings
from app.services.venue import extract_venue

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id                TEXT PRIMARY KEY,   -- 裸 arXiv id（去版本号）；version_update 行为 {bare}v{N}
    version           TEXT NOT NULL,
    kind              TEXT NOT NULL DEFAULT 'paper',  -- paper | version_update
    source            TEXT NOT NULL,      -- arxiv | hf_daily
    title             TEXT NOT NULL,
    authors           TEXT NOT NULL DEFAULT '[]',     -- JSON list
    abstract          TEXT NOT NULL,
    categories        TEXT NOT NULL DEFAULT '[]',     -- JSON list
    primary_category  TEXT,
    arxiv_url         TEXT NOT NULL,
    pdf_url           TEXT,
    published_at      TEXT NOT NULL,      -- ISO 8601 UTC
    first_seen_at     TEXT NOT NULL,
    fetched_date      TEXT NOT NULL,      -- YYYY-MM-DD（本地时区）
    score             REAL,               -- NULL = 未打分/打分失败
    reason            TEXT,
    summary           TEXT,               -- LLM 中文摘要（基于标题+摘要归纳）
    topic             TEXT,               -- LLM 归类的 topic 名（画像 topics[].name），NULL = 未分类
    matched_keywords  TEXT NOT NULL DEFAULT '[]',     -- JSON list
    status            TEXT NOT NULL DEFAULT 'new',    -- new | starred | dismissed
    read_doc_name     TEXT,
    comment           TEXT,               -- arXiv 作者备注（常含接收会议信息）
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_fetched_date ON papers (fetched_date);
CREATE INDEX IF NOT EXISTS idx_papers_status ON papers (status);
CREATE INDEX IF NOT EXISTS idx_papers_published_at ON papers (published_at);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,           -- running | success | error
    trigger      TEXT NOT NULL,           -- scheduled | manual
    fetched      INTEGER NOT NULL DEFAULT 0,
    scored       INTEGER NOT NULL DEFAULT 0,
    alerted      INTEGER NOT NULL DEFAULT 0,
    error        TEXT
);

-- 研究画像：单行表（id 恒为 1），经 UI 设置面板编辑，不进 env
CREATE TABLE IF NOT EXISTS profile (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    categories       TEXT NOT NULL DEFAULT '[]',   -- JSON list，如 ["cs.CL"]
    keywords         TEXT NOT NULL DEFAULT '[]',   -- JSON list（旧版扁平结构，已由 topics 取代）
    topics           TEXT NOT NULL DEFAULT '[]',   -- JSON list[{group,name,description,keywords}]
    exclude_keywords TEXT NOT NULL DEFAULT '[]',   -- JSON list
    daily_limit      INTEGER NOT NULL DEFAULT 30,
    updated_at       TEXT NOT NULL
);
"""

JSON_FIELDS = ("authors", "categories", "matched_keywords")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def db_path() -> Path:
    return get_settings().data_dir / "radar.db"


def get_conn() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with closing(get_conn()) as conn:
        conn.executescript(_SCHEMA)
        try:  # 旧库迁移：v0.1 建表时没有 comment 列
            conn.execute("ALTER TABLE papers ADD COLUMN comment TEXT")
        except sqlite3.OperationalError:
            pass
        try:  # 旧库迁移：打分层 v2 增加的 summary 列
            conn.execute("ALTER TABLE papers ADD COLUMN summary TEXT")
        except sqlite3.OperationalError:
            pass
        try:  # 旧库迁移：topic 分组功能增加的列
            conn.execute("ALTER TABLE papers ADD COLUMN topic TEXT")
        except sqlite3.OperationalError:
            pass
        try:  # 旧库迁移：profile 表增加 topics 列
            conn.execute("ALTER TABLE profile ADD COLUMN topics TEXT NOT NULL DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass
        conn.commit()


def _row_to_paper(row: sqlite3.Row) -> dict:
    paper = dict(row)
    for field in JSON_FIELDS:
        try:
            paper[field] = json.loads(paper.get(field) or "[]")
        except (TypeError, ValueError):
            paper[field] = []
    paper["venue"] = extract_venue(paper.get("comment"))
    return paper


# ---------------- papers ----------------

def upsert_paper(data: dict) -> str:
    """写入一篇论文，返回动作：inserted | version_update | unchanged。

    裸 id 为 kind=paper 行的主键；检测到更高版本时更新主行，
    并额外插入一条 id={bare}v{N}、kind=version_update 的行（幂等）。
    """
    now = utc_now_iso()
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT id, version, comment FROM papers WHERE id = ? AND kind = 'paper'", (data["id"],)
        ).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO papers (id, version, kind, source, title, authors, abstract,
                     categories, primary_category, arxiv_url, pdf_url, published_at,
                     first_seen_at, fetched_date, matched_keywords, status, comment, created_at, updated_at)
                   VALUES (?, ?, 'paper', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)""",
                (
                    data["id"], data["version"], data["source"], data["title"],
                    json.dumps(data.get("authors", []), ensure_ascii=False),
                    data["abstract"],
                    json.dumps(data.get("categories", []), ensure_ascii=False),
                    data.get("primary_category"),
                    data["arxiv_url"], data.get("pdf_url"), data["published_at"],
                    now, data["fetched_date"],
                    json.dumps(data.get("matched_keywords", []), ensure_ascii=False),
                    data.get("comment"), now, now,
                ),
            )
            conn.commit()
            return "inserted"

        old_ver = _version_number(row["version"])
        new_ver = _version_number(data["version"])
        if new_ver > old_ver:
            conn.execute(
                """UPDATE papers SET version = ?, title = ?, abstract = ?, authors = ?,
                     categories = ?, primary_category = ?, pdf_url = ?, published_at = ?,
                     comment = ?, updated_at = ? WHERE id = ? AND kind = 'paper'""",
                (
                    data["version"], data["title"], data["abstract"],
                    json.dumps(data.get("authors", []), ensure_ascii=False),
                    json.dumps(data.get("categories", []), ensure_ascii=False),
                    data.get("primary_category"), data.get("pdf_url"),
                    data["published_at"], data.get("comment"), now, data["id"],
                ),
            )
            vu_id = f"{data['id']}v{new_ver}"
            exists = conn.execute("SELECT 1 FROM papers WHERE id = ?", (vu_id,)).fetchone()
            if exists is None:
                conn.execute(
                    """INSERT INTO papers (id, version, kind, source, title, authors, abstract,
                         categories, primary_category, arxiv_url, pdf_url, published_at,
                         first_seen_at, fetched_date, matched_keywords, status, comment, created_at, updated_at)
                       VALUES (?, ?, 'version_update', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)""",
                    (
                        vu_id, data["version"], data["source"], data["title"],
                        json.dumps(data.get("authors", []), ensure_ascii=False),
                        data["abstract"],
                        json.dumps(data.get("categories", []), ensure_ascii=False),
                        data.get("primary_category"),
                        data["arxiv_url"], data.get("pdf_url"), data["published_at"],
                        now, data["fetched_date"],
                        json.dumps(data.get("matched_keywords", []), ensure_ascii=False),
                        data.get("comment"), now, now,
                    ),
                )
            conn.commit()
            return "version_update"

        # 内容未变，但旧记录缺 comment（如本功能上线前的存量）→ 补上
        if data.get("comment") and row["comment"] is None:
            conn.execute(
                "UPDATE papers SET comment = ?, updated_at = ? WHERE id = ? AND kind = 'paper'",
                (data["comment"], now, data["id"]),
            )
            conn.commit()
        return "unchanged"


def _version_number(version: str | None) -> int:
    try:
        return int(str(version or "v1").lstrip("vV"))
    except ValueError:
        return 1


def list_papers(
    date: str | None = None,
    status: str | None = None,
    min_score: float | None = None,
    topic: str | None = None,
    limit: int = 60,
    start: int = 0,
) -> tuple[list[dict], int]:
    where, params = [], []
    if date:
        where.append("fetched_date = ?")
        params.append(date)
    if status:
        where.append("status = ?")
        params.append(status)
    if min_score is not None:
        where.append("score >= ?")
        params.append(min_score)
    if topic:
        where.append("topic = ?")
        params.append(topic)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with closing(get_conn()) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM papers {clause}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""SELECT * FROM papers {clause}
                ORDER BY (score IS NULL), score DESC, published_at DESC
                LIMIT ? OFFSET ?""",
            [*params, limit, start],
        ).fetchall()
    return [_row_to_paper(r) for r in rows], total


def get_paper(paper_id: str) -> dict | None:
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM papers WHERE id = ? AND kind = 'paper'", (paper_id,)
        ).fetchone()
    return _row_to_paper(row) if row else None


def update_paper_status(paper_id: str, status: str) -> dict | None:
    with closing(get_conn()) as conn:
        cur = conn.execute(
            "UPDATE papers SET status = ?, updated_at = ? WHERE id = ? AND kind = 'paper'",
            (status, utc_now_iso(), paper_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return get_paper(paper_id)


def set_paper_score(
    paper_id: str,
    score: float | None,
    reason: str | None,
    summary: str | None = None,
    topic: str | None = None,
) -> None:
    with closing(get_conn()) as conn:
        conn.execute(
            "UPDATE papers SET score = ?, reason = ?, summary = ?, topic = ?, updated_at = ? WHERE id = ?",
            (score, reason, summary, topic, utc_now_iso(), paper_id),
        )
        conn.commit()


def get_unscored_papers(limit: int = 500) -> list[dict]:
    with closing(get_conn()) as conn:
        rows = conn.execute(
            """SELECT * FROM papers
               WHERE score IS NULL AND kind = 'paper' AND status IN ('new', 'starred')
               ORDER BY fetched_date DESC, published_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [_row_to_paper(r) for r in rows]


def mark_paper_read(paper_id: str, doc_name: str) -> None:
    with closing(get_conn()) as conn:
        conn.execute(
            "UPDATE papers SET read_doc_name = ?, updated_at = ? WHERE id = ?",
            (doc_name, utc_now_iso(), paper_id),
        )
        conn.commit()


def ids_missing_comment(limit: int = 200) -> list[str]:
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT id FROM papers WHERE kind = 'paper' AND comment IS NULL LIMIT ?", (limit,)
        ).fetchall()
    return [r["id"] for r in rows]


def set_paper_comment(paper_id: str, comment: str | None) -> None:
    if not comment:
        return
    with closing(get_conn()) as conn:
        conn.execute(
            "UPDATE papers SET comment = ?, updated_at = ? WHERE id = ? AND comment IS NULL",
            (comment, utc_now_iso(), paper_id),
        )
        conn.commit()


def papers_for_digest(date: str, limit: int) -> list[dict]:
    with closing(get_conn()) as conn:
        rows = conn.execute(
            """SELECT * FROM papers
               WHERE fetched_date = ? AND kind = 'paper' AND status != 'dismissed'
               ORDER BY (score IS NULL), score DESC, published_at DESC LIMIT ?""",
            (date, limit),
        ).fetchall()
    return [_row_to_paper(r) for r in rows]


def count_papers_on_date(date: str) -> int:
    with closing(get_conn()) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM papers WHERE fetched_date = ? AND kind = 'paper'", (date,)
        ).fetchone()[0]


def prune_papers(retention_days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    with closing(get_conn()) as conn:
        cur = conn.execute("DELETE FROM papers WHERE first_seen_at < ?", (cutoff_iso,))
        conn.commit()
        return cur.rowcount


# ---------------- runs ----------------

def create_run(trigger: str) -> int:
    with closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at, status, trigger) VALUES (?, 'running', ?)",
            (utc_now_iso(), trigger),
        )
        conn.commit()
        return cur.lastrowid


def finish_run(
    run_id: int,
    status: str,
    fetched: int = 0,
    scored: int = 0,
    alerted: int = 0,
    error: str | None = None,
) -> None:
    with closing(get_conn()) as conn:
        conn.execute(
            """UPDATE runs SET finished_at = ?, status = ?, fetched = ?, scored = ?,
                 alerted = ?, error = ? WHERE id = ?""",
            (utc_now_iso(), status, fetched, scored, alerted, error, run_id),
        )
        conn.commit()


def get_last_successful_run() -> dict | None:
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE status = 'success' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_latest_run() -> dict | None:
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def prune_runs(keep: int = 100) -> None:
    with closing(get_conn()) as conn:
        conn.execute(
            f"DELETE FROM runs WHERE id <= (SELECT MAX(id) FROM runs) - {int(keep)}"
        )
        conn.commit()


# ---------------- profile ----------------

PROFILE_DEFAULTS = {
    "categories": ["cs.CL", "cs.IR", "cs.LG"],
    "topics": [
        {
            "group": "大模型与检索",
            "name": "LLM 与 RAG",
            "description": "大模型、检索增强生成与智能体相关研究",
            "keywords": ["large language model", "retrieval augmented generation", "agent"],
        }
    ],
    "exclude_keywords": [],
    "daily_limit": 30,
}


def get_profile() -> dict:
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    if row is None:
        # 首次启动给一组通用默认画像，降低上手门槛；用户可随时在设置面板改
        save_profile(**PROFILE_DEFAULTS)
        return {**PROFILE_DEFAULTS, "updated_at": utc_now_iso()}
    profile = dict(row)
    for field in ("categories", "keywords", "topics", "exclude_keywords"):
        try:
            profile[field] = json.loads(profile.get(field) or "[]")
        except (TypeError, ValueError):
            profile[field] = []
    # 旧库迁移：扁平 keywords → 包成一个默认 topic（读时升级并回写）
    if not profile.get("topics") and profile.get("keywords"):
        profile["topics"] = [
            {
                "group": "默认方向",
                "name": "未分组",
                "description": "旧版扁平关键词迁移而来，请在设置面板重新组织",
                "keywords": profile["keywords"],
            }
        ]
        save_profile(
            profile["categories"], profile["topics"],
            profile["exclude_keywords"], profile["daily_limit"],
        )
        profile["updated_at"] = utc_now_iso()
    profile.pop("keywords", None)
    return profile


def save_profile(
    categories: list[str], topics: list[dict], exclude_keywords: list[str], daily_limit: int
) -> dict:
    now = utc_now_iso()
    with closing(get_conn()) as conn:
        conn.execute(
            """INSERT INTO profile (id, categories, topics, exclude_keywords, daily_limit, updated_at)
               VALUES (1, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET categories = excluded.categories,
                 topics = excluded.topics, exclude_keywords = excluded.exclude_keywords,
                 daily_limit = excluded.daily_limit, updated_at = excluded.updated_at""",
            (
                json.dumps(categories, ensure_ascii=False),
                json.dumps(topics, ensure_ascii=False),
                json.dumps(exclude_keywords, ensure_ascii=False),
                daily_limit,
                now,
            ),
        )
        conn.commit()
    return {
        "categories": categories,
        "topics": topics,
        "exclude_keywords": exclude_keywords,
        "daily_limit": daily_limit,
        "updated_at": now,
    }
