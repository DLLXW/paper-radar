# PaperRadar（论文雷达）— 实施计划（已确认版）

> 状态：已与项目所有者确认，待实施。开发约束见根目录 [AGENTS.md](../AGENTS.md)。
> 确认日期：2026-10-18。

## 定位（已确认的产品决策）

**全新独立项目**（本仓库），与 local_ai_toolkit 完全解耦：

- **纯 API 驱动，零本地模型**：抓取用 arXiv/HF 免费 API，打分用 DeepSeek（任何 OpenAI 兼容
  API），推送用飞书/邮件。用户只需配置一个 API key。这是对竞品调研结论「双本地模型筛掉 95%
  用户」的直接回应。
- **单进程部署**：FastAPI 同时服务 API + 前端静态文件，`docker run` 一行命令（或 `uv run` 裸跑）。
- **精读闭环 = 可选插件**：设置里填 reader 服务地址（指向 local_ai_toolkit 实例，复用其现成
  `POST /api/upload/url`），速递卡片即出现「去精读」；不配置不影响使用。

## 与现有项目的区别（README 定位素材，浓缩版）

详见 [DIFFERENTIATION.md](DIFFERENTIATION.md)：

- vs **cv-arxiv-daily 类**（关键词订阅）：他们纯关键词命中即推、单向只读、fork 门槛高
  （改 yaml+secrets+Actions 权限）、状态文件只增不减；我们 LLM 打分排序 + 交互式速递台 + SQLite 保留期。
- vs **ChatPaper**（19.9k★已停更）：他们 GPT 全文摘要贵且慢、硬编码模型已不可用、无去重；
  我们摘要层做轻（分数+理由），重活交给可选的全文精读闭环。
- vs **DeepSeek 日报类**（HF中文解读/邮件摘要）：他们无过滤全量翻译或硬顶 5 篇、单向推送；
  我们按研究方向打分、可交互、多渠道。
- vs **MCP server 类**：他们是按需检索工具，无"我的方向"、无定时、无推送；我们是常驻速递台，
  且数据模型为未来 MCP/Skill 预留。
- **独有组合**：相关度分数+一句话理由（竞品调研确认的全市场空白）、精读闭环（无主之地）、
  v2 更新检测、30 秒部署、前人事故教训全部绕开。
  诚实边界：单点都不新奇，赌的是组合+门槛+打磨。

## 技术栈

Python 3.11+ / FastAPI / httpx / pydantic-settings / uvicorn（uv 管理）；存储 stdlib sqlite3；
调度 asyncio lifespan；前端 Vite + 原生 JS（复用 local_ai_toolkit 设计语言与
marked/KaTeX/DOMPurify）。**无任何 ML/调度器/LLM SDK 依赖**——LLM 调用手写 OpenAI 兼容请求。

## 项目结构

```
paper-radar/
├── backend/
│   ├── pyproject.toml  .env.example
│   └── app/
│       ├── main.py            # FastAPI + lifespan 调度 + 托管 frontend/dist
│       ├── core/config.py     # pydantic-settings，RADAR_ 前缀
│       ├── db/store.py        # sqlite3：papers/runs/profile
│       ├── api/papers.py  api/profile.py  api/system.py
│       └── services/
│           ├── sources.py     # arXiv Atom + HF daily_papers（防坑版抓取）
│           ├── tracker.py     # run_once 编排 + 0篇/失败告警
│           ├── llm.py         # DeepSeek 批量打分
│           ├── push.py        # 飞书 + SMTP + digest 工件
│           └── reader.py      # 可选精读集成连接器
├── frontend/                  # Vite + vanilla JS：速递视图 + 设置面板
├── Dockerfile  docker-compose.yml
└── README.md  docs/
```

配置门槛：唯一必填 `RADAR_LLM_API_KEY`；研究画像存数据库 + UI 设置面板编辑
（首次启动引导）；其余（抓取时间 `RADAR_PAPER_FETCH_HOUR`、推送渠道、reader 地址、
`RADAR_PAPER_RETENTION_DAYS`）均为可选 env。

## API 设计（API-first，为 MCP/Skill 留路）

`GET /api/papers?date=&status=&min_score=`、`GET /api/papers/status`、`POST /api/papers/fetch`
（重入 409）、`GET|PUT /api/profile`、`PATCH /api/papers/{id}`、`POST /api/papers/{id}/read`
（代理 reader 并回写，未配置返回 400+引导）、`POST /api/push/test`、`GET /api/digests/{date}.md`、
`GET /` 托管 SPA。输入统一过 `normalize_paper_input()`（abs/pdf/HF 链接/裸 id）。
响应 JSON-first、结构化状态负载（`has_more`/`next_start`/`{status, retry_after_seconds}` 风格）。

## 调研防坑清单（落进实现，详细证据见 research/）

1. 原生 Atom API（不用 arxiv PyPI 包）；间隔 ≥3s；429/5xx 指数退避；识别 HTTP 200 `"Rate exceeded."`
2. 查询串程序化拼接 `(kw1 OR "kw 2") AND (cat:A OR cat:B)`；显式 submittedDate 日期窗
   （自上次成功运行）+ max_results=3×上限
3. 抓取循环零富化调用；单查询独立 try/except；**0 篇 → 飞书告警而非静默成功**
4. 裸 arXiv ID 主键 + version 字段（v2 →「已更新」徽章）；first_seen_at + RETENTION_DAYS=90 清理；
   结构化存储、列表保持 list
5. LLM 批量 10-15 篇/次 + json_object + schema 校验；句子边界截 600 字；批间 3s；每批 checkpoint；
   失败降级 score=null；无 key 可跑
6. 卡片：分数三档配色 + 一句话理由 + 日期徽章（今日/昨日/N天前）+ v2 徽章
  （调研确认此组合为全市场空白）

## 实施阶段

**Phase 0 脚手架**：backend 骨架（config/db/health/静态托管）；frontend 骨架（速递/设置导航壳）；
Dockerfile+compose；README 骨架。✅ 一键起服务。

**Phase 1 情报层**：sources + sqlite 表 + tracker + scheduler + papers API。✅ fetch 后库里有
去重数据、版本检测生效、重跑不重复。

**Phase 2 打分层**：llm + profile 服务 + checkpoint + 降级。✅ 配 key 出分数理由；
kill 重跑不重复计费；无 key 跳过。

**Phase 3 前端**：速递视图（筛选/刷新轮询/摘要折叠/markdown 渲染）+ 设置面板
（画像编辑 + 服务状态）+ 空状态引导。✅ 浏览器全流程。

**Phase 4 推送层**：飞书 text + SMTP HTML 卡片（编号/标题/分数徽章/理由块/双链接，主题
`📚 PaperRadar 日报 - YYYY-MM-DD`）+ 失败告警 + digest 工件 + push-test API。✅ 飞书/邮箱收到。

**Phase 5 精读集成 + 收尾**：reader 连接器（配地址→「去精读」→代理导入→状态回写）；
README 双语完整版（30 秒上手 + docker 一行 + GIF 占位）；.env.example 全注释。
✅ 配 reader 后全链路联调。

## 明确不做（后续独立计划）

GitHub Actions 零部署版、MCP/Agent Skill 暴露（数据模型已预留）、周报、发布运营
（GitHub 重名查重、Show HN/r LocalLLaMA、demo 视频）。不动 local_ai_toolkit 仓库
（其 tasks.py 有未提交改动）。
