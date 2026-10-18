# 📡 PaperRadar（论文雷达）

> 每天自动追踪 arXiv 与 Hugging Face Daily Papers，按**你的研究画像**用 LLM 打分排序，
> 生成可交互的速递台，并推送到飞书 / 邮箱。纯 API 驱动，零本地模型，**一个 API key 即可运行**。

<!-- ![demo](docs/assets/demo.gif) — GIF 占位：速递台全流程演示，发布前录制后取消注释 -->

## ✨ 它长什么样

- **每日速递台**：论文卡片带相关度分数三档徽章（🔥高 / ⭐中 / 低）、一句话打分理由、
  今日/昨日/N天前日期徽章、`已更新 v2` 版本徽章、命中关键词；摘要折叠展开、LaTeX 公式渲染。
- **交互而非单向推送**：星标 / 忽略 / 恢复，按日期、状态、分数筛选，手动抓取 + 运行进度轮询。
- **推送**：飞书文本卡片 + 邮件 HTML 卡片（编号/标题/分数徽章/理由/abs+pdf 双链接），
  每日 digest 同时落一份不可变 markdown 工件（`GET /api/digests/YYYY-MM-DD.md`）。
- **可选精读闭环**：配置 reader 服务（如 [local_ai_toolkit](https://github.com/) 实例）后，
  速递卡片出现「去精读」——全文 OCR + 翻译 + 双语阅读，一键把论文变成可读文档。
- **工程上的克制**：单进程、单 SQLite 文件、5 个 Python 依赖、无 SDK / ORM / 调度器框架；
  LLM、推送、reader 任何一个挂了都不影响核心的抓取与浏览（降级即产品）。

## 🚀 30 秒上手

```bash
# 1. 构建前端 + 装后端依赖
cd frontend && npm install && npm run build && cd ../backend && uv sync

# 2. 填上你的 DeepSeek key（不填也能跑，只是没有分数）
cp .env.example .env && echo 'RADAR_LLM_API_KEY=sk-xxx' >> .env

# 3. 起服务（同时托管前端）
uv run uvicorn app.main:app --port 8300
```

打开 <http://127.0.0.1:8300> → 「设置」里改好研究画像 → 点「🛰 立即抓取」。
之后每天 `RADAR_PAPER_FETCH_HOUR`（默认 8 点）自动运行；服务停超过 20 小时，重启会自动补跑。

### Docker 一行部署

```bash
docker compose up --build -d        # 数据持久化在 radar-data 卷，改 .env 后需重建容器
```

## 为什么是 PaperRadar（竞品调研结论）

现有方案的三种形态都没解决「按我的方向排序 + 可交互」：

| 形态 | 代表 | 痛点 | PaperRadar |
|---|---|---|---|
| 关键词订阅 | cv-arxiv-daily 系 | 命中即推、噪音靠人肉、改 yaml+secrets 才能定制 | LLM 打分排序 + UI 里改画像 + 状态管理 |
| LLM 全文摘要 | ChatPaper（已停更） | 贵、慢、硬编码模型已不可用、无去重 | 只做轻量「分数+理由」，重活交给可选精读闭环 |
| 检索工具 | paper-search-mcp 等 | 按需检索，没有「我的方向」与常驻追踪 | 常驻速递台 + 定时抓取 + 推送 |

**独有组合**：相关度分数 + 一句话理由 + v2 更新检测 + 可选全文精读闭环 + 30 秒部署。
诚实边界：单点都不新奇，赌的是组合、门槛与打磨。逐维度对比见
[docs/DIFFERENTIATION.md](docs/DIFFERENTIATION.md)，三份竞品源码级调研见 [docs/research/](docs/research/)。

实现上把前人事故全部绕开（详见 [AGENTS.md](AGENTS.md) 防坑清单）：arXiv 限速 ≥3s +
软限速识别、显式时间窗禁止排序截断、0 篇告警绝不静默、裸 ID 去重 + 版本检测、
LLM 每批 checkpoint 崩溃不重复计费、无 key 优雅降级。

## ⚙️ 配置

唯一必填 `RADAR_LLM_API_KEY`；其余可选。**研究画像（arXiv 分类 / 关键词 / 排除词 /
每日上限）存数据库，在 Web 设置面板编辑，不进配置文件。**

| 变量 | 默认 | 说明 |
|---|---|---|
| `RADAR_LLM_API_KEY` | — | **唯一必填**。OpenAI 兼容 key（默认 DeepSeek） |
| `RADAR_LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容 base URL |
| `RADAR_LLM_MODEL` | `deepseek-chat` | 模型名 |
| `RADAR_PAPER_FETCH_HOUR` | `8` | 每日抓取时刻（本地时间小时） |
| `RADAR_PAPER_RETENTION_DAYS` | `90` | 论文保留天数 |
| `RADAR_FEISHU_WEBHOOK_URL` | — | 飞书自定义机器人 webhook |
| `RADAR_SMTP_HOST/PORT/USER/PASSWORD/FROM/TO` | 587 | 邮件推送（STARTTLS，用授权码） |
| `RADAR_READER_BASE_URL` | — | 精读服务地址（local_ai_toolkit 等） |
| `RADAR_DATA_DIR` | `backend/data/` | SQLite 与 digest 工件目录 |
| `RADAR_PORT` | `8300` | 服务端口 |

申请指引（DeepSeek key、飞书机器人、QQ/163/Gmail 授权码）都写在
[backend/.env.example](backend/.env.example) 的注释里。

## 🔌 API（JSON-first，为 MCP/Skill 预留）

```
GET  /api/papers?date=&status=&min_score=&limit=&start=   速递列表（score desc → published desc）
GET  /api/papers/status                                    运行状态 / 上轮摘要 / 下次运行
POST /api/papers/fetch                                     手动触发（运行中 409）
PATCH /api/papers/{id}                                     状态变更 new|starred|dismissed
POST /api/papers/{id}/read                                 代理精读服务（未配置 400+引导）
GET|PUT /api/profile                                       研究画像
POST /api/push/test                                        推送配置自检
GET  /api/digests/{date}.md                                不可变 digest 工件
GET  /api/health                                           健康检查
```

`{id}` 接受裸 id、`arxiv.org/abs|pdf` 链接、`huggingface.co/papers` 链接（统一过
`normalize_paper_input()`）。分页与状态走结构化负载（`has_more` / `next_start` /
`{status, retry_after_seconds}`）。

## 🧭 路线图（本期不做，数据模型已预留）

MCP / Agent Skill 暴露、GitHub Actions 零部署版、周报回顾。

## English

**PaperRadar** tracks arXiv and Hugging Face Daily Papers daily, scores every paper
against *your* research profile with an LLM (0–100 + one-line reason), and serves an
interactive digest web UI with Feishu/email push. Pure-API, zero local models — one
API key to run.

- 30s start: build the frontend (`npm i && npm run build`), `uv sync`, set
  `RADAR_LLM_API_KEY`, `uv run uvicorn app.main:app --port 8300`; or `docker compose up --build -d`.
- Graceful degradation everywhere: no LLM key → digest still works (unscored);
  push/reader down → fetching and browsing unaffected.
- Honed by prior incidents (see [AGENTS.md](AGENTS.md)): arXiv rate-limit compliance,
  explicit time windows, zero-paper alerts, bare-ID dedup with v2 detection,
  per-batch scoring checkpoints.

Docs: [PLAN.md](docs/PLAN.md) · [DIFFERENTIATION.md](docs/DIFFERENTIATION.md) ·
[AGENTS.md](AGENTS.md) · [research/](docs/research/)
