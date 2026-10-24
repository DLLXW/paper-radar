# AGENTS.md — 开发 Agent 交接上下文

本文档是 PaperRadar 的开发约束汇总。开始写代码前请先通读本文与 [docs/PLAN.md](docs/PLAN.md)，
并参考 [docs/research/](docs/research/) 三份竞品调研——**防坑清单里每一条都对应前人真实的事故**，不是风格偏好。

## 项目是什么

PaperRadar（论文雷达）：每日定时抓取 arXiv 与 Hugging Face Daily Papers → 按用户研究画像
做关键词粗筛 + LLM（DeepSeek）相关度打分 → Web 速递台展示（分数 + 一句话理由）→
推送飞书/邮件摘要。可选地连接一个「精读服务」（用户的 local_ai_toolkit 实例）实现
全文 OCR + 翻译 + 双语阅读闭环。

## 已确认的产品决策（不要重新讨论）

1. **独立新项目**，与 local_ai_toolkit 仓库完全解耦；不得修改 local_ai_toolkit（其 `app/backend/app/api/tasks.py` 有用户未提交的改动）。
2. **纯 API 驱动，零本地模型**：抓取用 arXiv/HF 免费 API，打分用 OpenAI 兼容 API（默认 DeepSeek），
   推送用飞书 webhook / SMTP。目标门槛：用户只填一个 key 就能跑。
3. **单进程部署**：FastAPI 同时服务 API 与前端构建产物（SPA）。`docker run` 一行命令，或 `uv run` 裸跑。
4. **精读闭环是可选插件**：设置里配置 reader 服务 base URL（指向 local_ai_toolkit 的
   `POST /api/upload/url`）后，速递卡片才出现「去精读」；未配置时完全不影响核心体验。
5. 前端为 Vite + 原生 JS 单页应用（无框架），复用 marked / KaTeX / DOMPurify 组合；
   UI 主要文案用中文（首发面向中文开发者），发布时另出英文 README。
6. 项目名 PaperRadar，目录 `/Users/limzero/qyl/paper-radar`；发布前需做 GitHub 重名查重。

## 技术栈硬约束（最小依赖路线）

- Python 3.11+，包管理用 **uv**（`pyproject.toml` + `uv.lock`，`[tool.uv] package = false` 风格可参考）。
- 后端依赖**只允许**：`fastapi`、`httpx`、`pydantic-settings`、`python-multipart`、`uvicorn`。
  - **禁止**引入：openai/anthropic SDK（LLM 调用手写 OpenAI 兼容 HTTP 请求）、
    APScheduler（调度用 asyncio lifespan 循环）、arxiv PyPI 包（该包曾破坏性改 API，用原生 Atom API）、
    feedparser（xml.etree 解析 Atom）、SQLAlchemy/ORM（用 stdlib `sqlite3`）、前端框架。
- 存储：stdlib `sqlite3`，单文件库；表：papers / runs / profile。
- 配置：pydantic-settings，env 前缀 `RADAR_`，`.env.example` 全注释。
  **唯一必填 `RADAR_LLM_API_KEY`**；研究画像不进 env，存数据库并经 UI 设置面板编辑。
  画像按「方向(group) → topic → 关键词」三层组织（`profile.topics`，上限 12 topic /
  单 topic 10 词 / 全局 60 词 + 20 分类）：LLM 打分时同时把论文归入 topic
  （`papers.topic` 列，不在画像内的归 NULL），推送与 digest 按 方向→topic 两级分组；
  关键词支持 `@分类` 后缀做单查询分类覆盖（如 `scaling law @cs.IR`，逗号分隔多个），
  无后缀关键词用全局分类过滤；旧库扁平 keywords 读取时自动迁移为单个默认 topic。
  其余（抓取时刻、推送渠道、reader 地址、保留天数）均为可选 env。

## API 设计（已确认，勿随意改形态）

所有路由前缀 `/api`；输入统一过 `normalize_paper_input()`（接受 arxiv.org/abs、/pdf、HF 链接、裸 id 含版本号）。

- `GET /api/papers?date=&status=&min_score=&topic=&limit=` — 排序：score desc（null 最后）→ published desc；topic 为画像 topic 名精确过滤
- `GET /api/papers/status` — { running, last_run_at, last_run_summary{fetched, scored, alerted}, next_run_at }
- `POST /api/papers/fetch` — 手动触发一轮抓取+打分；已在跑返回 409
- `GET /api/profile` / `PUT /api/profile` — 研究画像读写
- `PATCH /api/papers/{id}` — status 变更（new/starred/dismissed）
- `POST /api/papers/{id}/read` — 代理转发到 reader 服务的 `/api/upload/url` 并回写 read_doc_name；未配置 reader 返回 400 + 引导信息
- `POST /api/push/test` — 推送配置自检
- `GET /api/digests/{date}.md` — 不可变 digest 工件（未来 MCP/Agent skill 直读）
- `GET /` — 托管 frontend/dist（SPA fallback 到 index.html）

响应遵循「JSON-first、结构化状态负载」：分页/状态放进 payload（`has_more`、`next_start`、
`{status: ok|rate_limited|error, retry_after_seconds}` 风格），为未来暴露 MCP 工具铺路。

## 防坑清单（每条都有前人事故背书，是硬性要求）

调研证据见 docs/research/，此处为必须落实的实现要求：

1. **抓取循环内零外部富化调用**。任何可选增强（代码链接检测等）都不得进入抓取主循环——
   cv-arxiv-daily 因在循环内调已死的 PapersWithCode API 静默停更 2 个月。
2. **arXiv 限速**：请求间隔 ≥3 秒（官方 TOU）；对 429/500/502/503/504 指数退避重试；
   识别 HTTP 200 但 body 为 `"Rate exceeded."` 的软限速（paper-search-mcp 的做法）。
3. **查询串程序化拼接** `(kw1 OR "multi word") AND (cat:A OR cat:B)`，禁止用户手写原始查询串
   （`"a"OR"b"` 缺空格是错语法但返回错而非空的结果，极难排查）。
4. **显式时间窗**：以上次成功运行的 last_run_at 计算 submittedDate 下界；max_results 取
   daily 上限的 3 倍。禁止「排序+截断」式隐式窗口（爆发 >N 篇会被静默丢弃）。
5. **0 篇告警**：一轮抓取全部查询都得 0 篇时，判定为异常并触发飞书告警，绝不静默成功；
   单个关键词查询失败仅记日志，不拖垮整轮。
6. **数据模型**：裸 arXiv ID（去版本号）为主键 + 单独 version 字段；检测到新版本 → 生成
   kind=version_update 条目（前端显示「已更新」徽章）；存 first_seen_at；按
   `RADAR_PAPER_RETENTION_DAYS`（默认 90）清理；只存结构化字段，authors/categories 保持 list。
7. **LLM 打分**：批量 10–15 篇/次；`response_format={"type":"json_object"}` + schema 校验；
   摘要按**句子边界**截断 ~600 字（禁止字符比例截断）；批间 sleep 3s；**每批立即写库
   checkpoint**（崩溃重跑不重复计费）；单批解析失败 stricter 重试 1 次，仍失败该批
   score=null 照常入库。未配 key 或调用失败 → 整体跳过打分，服务照常可用。
8. **降级即产品**：任何外部依赖（LLM/推送/reader）失败都不能让抓取与速递台不可用。
9. **token 预算靠字符估算**，不得在启动路径加载 tokenizer（ChatPaper 因 tiktoken 缓存损坏整体崩死的教训）。
10. **凭证只进 env/secrets**，任何配置文件不得提交 key。

## 数据模型要点（papers 表核心字段）

`id`（裸 arXiv id, PK）、`version`、`kind`（paper | version_update）、`source`（arxiv | hf_daily）、
`title`、`authors`(list)、`abstract`、`categories`(list)、`primary_category`、`arxiv_url`、`pdf_url`、
`published_at`、`first_seen_at`、`fetched_date`、`score`(REAL NULL)、`reason`(TEXT NULL)、
`summary`(TEXT NULL)、`topic`(TEXT NULL，LLM 归类的画像 topic 名)、
`matched_keywords`(list)、`status`（new | starred | dismissed）、`read_doc_name`(NULL)、
`created_at`、`updated_at`。

## 实施阶段顺序（每阶段有独立验证点，完成一个再进下一个）

- **Phase 0 脚手架**：backend 骨架（config/db/health/静态托管 dist）+ frontend 骨架（速递/设置导航壳）+ Dockerfile + compose + README 骨架。✅ 一键起服务，浏览器打开 SPA 壳。
- **Phase 1 情报层**：sources.py（防坑抓取）+ sqlite 表 + tracker 编排 + scheduler + papers API。✅ `curl POST /api/papers/fetch` 后库里有去重数据；重跑不重复；v2 检测生效。
- **Phase 2 打分层**：llm.py + profile 服务 + checkpoint + 降级。✅ 配 key 出分数与理由；中途 kill 重跑不重复计费；无 key 优雅跳过。
- **Phase 3 前端**：速递视图（日期/状态/分数筛选、手动刷新轮询、摘要折叠、markdown+公式渲染、分数三档徽章、日期徽章、v2 徽章）+ 设置面板（画像表单 + 服务状态一览）+ 空状态引导。✅ 浏览器全流程。
- **Phase 4 推送层**：飞书 text + SMTP HTML 卡片（编号/标题/分数徽章/理由块/abs+PDF 双链接；主题 `📚 PaperRadar 日报 - YYYY-MM-DD`；587 STARTTLS）+ 失败告警 + digest 工件 + push-test API。✅ 飞书收消息、邮箱收 HTML。
- **Phase 5 精读集成 + 收尾**：reader 连接器；README 双语完整版（30 秒上手 + docker 一行 + GIF 占位）；`.env.example` 全注释（DeepSeek key 申请、飞书机器人创建、QQ/163/Gmail 授权码）。✅ 配 reader 后全链路：速递 → 去精读 → OCR+翻译 → 双语阅读。

## 明确不在本期范围

GitHub Actions 零部署版、MCP/Agent Skill 暴露（数据模型已预留）、周报、发布运营
（重名查重、Show HN、r/LocalLLaMA、demo 视频）。不要提前实现。

## 参考实现

用户的 local_ai_toolkit（`/Users/limzero/local_ai_toolkit`）可作为代码风格参考：
FastAPI 路由/服务分层、手写 OpenAI 兼容调用、pydantic-settings 用法、vanilla JS 前端的
fetchJson/pollTask/事件委托/markdown 渲染模式。只读参考，不得修改该仓库。
