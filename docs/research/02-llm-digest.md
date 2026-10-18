# 竞品调研 02：LLM 摘要派（ChatPaper / HF 日报中文解读 / RunRiot / ChatDailyPapers）

> 调研日期：2026-10-18。方法：通过 raw.githubusercontent.com 与 GitHub API 直接阅读源码、
> workflow 与 issue。所有结论附文件路径/URL 证据。
> 对 PaperRadar 的意义：我们的打分层用 DeepSeek 批量调用，这批项目的 prompt 设计、
> 成本控制、checkpoint 模式、推送格式、衰落原因都值得直接吸收或规避。

## 一、2404589803/hf-daily-paper-newsletter-chinese（DeepSeek 中文解读 HF Daily Papers）

文件：`Paper_metadata_download.py`、`HF-day-paper-deepseek.py`、`newsletter.py`、`utils.py`、
`.github/workflows/Paper_metadata_download.yaml`、`.github/workflows/HF-day-paper-deepseek.yaml`、
`upload_json_to_feishu.py`。

### 抓取端点
`GET https://huggingface.co/api/daily_papers?date={YYYY-MM-DD}`（Paper_metadata_download.py）。
每条校验含 `paper.title/summary/id/authors` + `publishedAt` 后存
`Paper_metadata_download/{date}.json`。日期按北京时间（Asia/Shanghai）。

### 调度——双 workflow 链
- Workflow A `Paper_metadata_download`：cron `45 7 * * 1-5`（UTC，周一至周五）。把日期写进
  `date.txt`，作为 workflow artifact 上传（1 天保留），JSON commit 回仓库，**并把原始 JSON 上传到
  飞书知识库节点**（`upload_json_to_feishu.py`，用 secrets `FEISHU_APP_ID`/`FEISHU_APP_SECRET` +
  变量 `FEISHU_WIKI_NODE_TOKEN`，需要 Lark 云文档文件上传权限）。
- Workflow B `HF-day-paper-deepseek`：cron `0 8 * * 1-5` **外加** `on: workflow_run` 触发于 A 完成。
  下载 artifact 恢复确切日期（多层兜底：date.txt → 当前日期，正则校验 `^[0-9]{4}-...$`），
  先检查 `Paper_metadata_download/{date}.json` 非空且 >0 篇再干活。
  用 `concurrency: group: hf-daily-paper-${{ github.ref }}, cancel-in-progress: false` 串行化。

### DeepSeek 用法——严格逐篇，无批量
- OpenAI 兼容客户端：`OpenAI(api_key=..., base_url="https://api.deepseek.com")`。
- Prompt 仅翻译，一篇一次调用：
  `"请将以下论文标题和摘要翻译成中文，保持学术性和专业性：\n标题：{title}\n摘要：{summary}\n请按照以下格式返回：\n标题：[中文标题]\n摘要：[中文摘要]"`
  system prompt：`"你是一个专业的学术翻译助手..."`。
- **输出校验是字符串包含而非 JSON**：`if translation and '标题：' in translation and '摘要：' in translation`。
  后续阶段（海报、期刊）要再用 `r"标题[:：]\s*([^\n]+)(?=\s*\n\s*摘要[:：]|\Z)"` 这类正则
  重新抽取字段——**整整一层正则抽取的存在只因 LLM 输出非结构化**。
- 三层重试：`@tenacity.retry(stop_after_attempt(3), wait_exponential(1, min=4, max=10))`
  包住 API 函数，函数内再套 `while` 3 次尝试 + `time.sleep(2**n)` 退避，论文间 `time.sleep(3)` 限速。
- **崩溃韧性**：每处理完一篇就重写中间结果 JSON（临时文件在最终保存后才删）——
  重跑时靠 `processed_titles` 集合跳过已处理标题，恢复成本低。
- 摘要**不截断、不预过滤**：每天全量处理 ~50 篇 HF 日报（2026-10-16 期刊实测 50 篇），
  成本能压住只因 prompt 是便宜的翻译任务。

### 去重
仅运行内——`processed_titles` 精确标题集合。跨运行去重是隐式的：每次只处理一个日期，
日期即存储键。**没有持久化的「已见」库。**

### 输出/发布
写 `HF-day-paper-deepseek/{date}_HF_deepseek_clean.json`、PIL 渲染的海报 PNG、词云 + 趋势统计、
TTS mp3、Jinja2 模板期刊固定路径 `newsletters/{date}_daily_paper.md` + `.html`，由
`github-actions[bot]` commit 回仓库——**GitHub 本身就是 digest UI**。期刊每篇字段：编号中文标题、
英文原标题、完整翻译摘要、双链接 `[HuggingFace](https://huggingface.co/papers/{id}) |
[arXiv](https://arxiv.org/abs/{id})`、可选代码链接；头部有总数 + 热点关键词。
- commit 竞争处理值得注意：commit 后执行
  `git fetch origin main; git reset --hard origin/main; git cherry-pick -X theirs $REPORT_COMMIT`
  循环 3 次、间隔 5s——可在两个 run 写同一天数据时存活。
- **fork 滥用防护**：`verify` job 检查 `remote.origin.url` 含 `2404589803/hf-daily-paper-newsletter-chinese`
  否则跳过；`require_auth` 装饰器 + `is_original_repo()`（读 `GITHUB_REPOSITORY`）确保 fork 必须填
  自己的 key（朴素校验：len>=32 且 `sk-` 开头）。
- **失败告警**：workflow 失败自动开 GitHub issue，标题 `🚨 工作流运行失败 - {timestamp}`，
  内容是失败 run 的链接（issue #2、#3、#4 均由此产生）。

## 二、RunRiotComeOn/arXiv-Daily-Summarizer（DeepSeek 摘要发邮件）

文件：`fetch_papers.py`（902 行单文件）、`.github/workflows/daily_arxiv.yml`、`.env.example`、
`LANGUAGE_CONFIG.md`、`README.md`。

### 抓取
`arxiv` PyPI 客户端。每分类 `arxiv.Search(query='cat:cs.AI', max_results=MAX_RESULTS*2,
sort_by=SubmittedDate, Descending)`，分类 `['cs.AI','cs.CV','cs.CL']`，跨分类 `seen_ids` 集合。

### 成本控制——LLM 之前先过滤，硬顶 5 篇/天
- `MAX_RESULTS = 5`，`MIN_PAPERS_PER_CATEGORY = 1`。
- 启发式 `calculate_paper_quality_score()` 在**任何 LLM 调用之前**跑：摘要长度（>500 字 +2.0，
  <100 字 −2.0）、作者数（3–8 最佳 +1.0）、标题关键词加分（"novel"/"benchmark"/"transformer"
  各 +0.5）、新近度（今天 +3.0、昨天 +1.5、之后每天 −0.3 衰减）。按分排序，每分类保底 1 篇，
  余量按分填充。**只有活下来的 5 篇进 LLM。**

### DeepSeek prompt（逐篇、自由文本、无 JSON）
```
请用中文总结以下学术论文，包括以下几个方面：
1. 研究背景和动机（1-2句话）
2. 主要方法和创新点（2-3句话）
3. 实验结果和结论（1-2句话）
4. 潜在应用价值（1句话）
论文标题：{title}
论文摘要：
{abstract}
```
- **用 ModelScope 免费托管 DeepSeek**：`base_url='https://api-inference.modelscope.cn/v1'`，
  `model='deepseek-ai/DeepSeek-V3.2-Exp'`（README 原话「本项目使用的是 ModelScope 的免费 api」，
  并宣传「完全免费：所有服务都在免费额度内」）。
- 流式输出处理 `reasoning_content`：跳过 `delta.reasoning_content` 块、只累积 `delta.content`
  ——**推理模型感知**，否则思维链会污染输出。
- 优雅失败：API 出错时摘要置为本地化兜底文案 `"摘要生成失败，请直接查看原文。"`，**邮件照发**。
- 双语模式：zh 与 en 摘要分开两次调用，标注 `🇨🇳 中文摘要` / `🇬🇧 English Summary`。

### 去重
两层，都仅运行内：精确 `entry_id` 集合 + 标题模糊去重（`difflib.SequenceMatcher` 对规范化标题，
`SIMILARITY_THRESHOLD = 0.85`，保留得分高的副本）。**无跨运行持久化。**
去重做不到时用诚实的标注兜底：每篇日期徽章（`NEW TODAY` 绿 / `YESTERDAY` 蓝 / `N DAYS AGO` 红）
+ 邮件顶部提示「本刊 5 篇中 3 篇今日发表，2 篇发表于 2 天前（可能已读过）」。
issue #1（"modify date problems"）表明 arXiv 提交日期 vs 公布日期的混淆是真实用户投诉。

### 邮件格式（纯 HTML，内联 CSS）
- 主题：`📚 arXiv Daily Paper Digest - {YYYY-MM-DD}`。
- `MIMEMultipart('alternative')`，单一 HTML part，SMTP `starttls` 587 端口；
  README 写明 Gmail 应用专用密码、QQ 邮箱（smtp.qq.com）、163（smtp.163.com）授权码。
- 每篇卡片：编号标题 + 日期徽章 + 质量徽章（`score >= 5.0` → `⭐ HIGH QUALITY` 金色）、
  meta 行（作者截断 200 字、发表时间、前 3 个分类做 pill 标签、**保留 1 位小数的质量分**）、
  彩色左边框的 AI 摘要块、「View PDF」链接按钮。
- 头部：渐变横幅 + 日期。脚注：「Generated automatically by arXiv Daily Summarizer /
  Powered by DeepSeek AI」。

### 调度
GH Actions cron `0 0 * * *` UTC 每日（北京 08:00）+ `workflow_dispatch`；
secrets `DEEPSEEK_API_KEY, SENDER_EMAIL, SENDER_PASSWORD, RECEIVER_EMAIL, SMTP_SERVER, SMTP_PORT`；
`EMAIL_LANGUAGE` = `zh|en|both`。

## 三、justchenhao/ChatDailyPapers（关键词 + ChatGPT + GitHub issue）

文件：`config.py`、`main.py`、`get_paper_from_pdf.py`、`github_issue.py`、
`.github/workflows/main.yml`。本质是 ChatPaper `chat_arxiv.py` 的精简衍生
（`Reader` 类、prompt、token 裁剪代码几乎相同）。

- **配置**：纯 Python `config.py`——`USERNAME/TOKEN`（GitHub PAT）、`REPO_OWNER/REPO_NAME`、
  `NEW_SUB_URL='https://arxiv.org/list/cs/new'`、`KEYWORD_LIST`、`OPENAI_API_KEYS`（**列表**，
  每次调用轮换）、`LANGUAGE = "zh"|"en"`。所有值以空字符串提交进仓库，无 Secrets 接线——
  **用户填 key 后直接 commit 明文凭证（反模式）**。
- 管道：按关键词组 arXiv 查询 `all:{word} AND all:{word}` → 时间过滤 `result.updated` 近 1.1 天 →
  **关键词门控要求摘要包含全部空格分隔的关键词**（`meet_num == len(filter_keys.split())`）→
  下载并全文解析 PDF → 三段式 GPT 摘要（`chat_summary` 吃 title/abs/intro → `chat_method`
  吃方法节 → `chat_conclusion` 吃结论节，附 innovation/performance/workload 三维评述）。
  `gpt-3.5-turbo` 硬编码，pre-1.0 `openai.ChatCompletion` API。
- **上下文控制**：`max_token_num = 4096`，按字符比例裁剪
  `clip_text_index = int(len(text) * (max_token_num - prompt_token) / text_token)`；
  遇 "maximum context" 报错时从错误串解析出 token 数、扩大预留后重试。
- **推送渠道**：**GitHub Issue**——`POST https://api.github.com/repos/{owner}/{repo}/import/issues`，
  标题=运行时间戳，正文=完整 markdown digest，labels=关键词列表。markdown 同时 commit 到
  `export/{date}.md`。
- 调度：cron `"10 20 * * 1,2,3,4,5,6"`（UTC 20:10，周一至周六）+ 每次 push main 也跑
  （浪费——每次 push 都全管道+LLM）。

## 四、kaixindelele/ChatPaper（19.9k★）

GitHub API：`stargazers_count: 19939`、`archived: False`、`pushed_at: 2026-03-02`
——**未正式归档但实质冻结**：全部实质功能停留在 2023 年 3–7 月（README changelog 止于
2023-07-23），84 个 open issue。根目录 `chat_arxiv.py` 754 行。

### 管道
arXiv API 查询带分页（`--query --page_num --max_results --days`），同样的关键词-AND-摘要过滤与
1.1 天窗口，然后**全文 PDF 下载 + 解析（PyMuPDF）**喂给分段 GPT 调用。

### 它为什么火——「一分钟摘要」prompt
`chat_summary` 产出固定六字段论文卡：
1. 标题（含中文翻译）2. 作者（英文）3. 一作单位（翻译）4. 关键词 5. 论文 URL + GitHub 代码链接
（无则 "Github:None"）6. 四点摘要：(1) 研究背景 (2) 过去方法及其问题+动机是否成立 (3) 方法论
(4) 任务与达成性能 +「性能能否支撑其目标」。
严格格式指令（"Follow the format of the output that follows... 1. Title: xxx\n\n 2. Authors: xxx ...
statements as concise and academic as possible ... strictly follow the format"）+ 按领域自适应的
system prompt：`"You are a researcher in the field of [" + key_word + "]..."`。
链式追问（`chat_method` 编号步骤；`chat_conclusion` 按 innovation/performance/workload 给显著性与
优缺点）回喂前一轮 `<summary>`。**全文（非摘要）grounding + chatpaper.org 网页 demo + B 站教程**
共同造就了 star 增长。

### 它为什么衰落（具体技术债）
- **硬编码 `model="gpt-3.5-turbo"` + pre-1.0 `openai.ChatCompletion` SDK**、全局
  `openai.api_key` 变更——openai>=1.0 后损坏，且根脚本无 `base_url` 开关，用户只能挂代理
  （issue #225 就是求第三方 OpenAI 代理支持）。
- **明文 API key**：`apikey.ini` 直接提交进仓库（甚至树里有 `HuggingFaceDeploy/Private/apikey.ini`）。
- **tokenizer 成了运行时依赖**：启动即 `tiktoken.get_encoding("gpt2")` 做裁剪；issue #275 是
  tiktoken 缓存损坏导致的 `json.decoder.JSONDecodeError: Unterminated string starting at line 1
  column 687651`——**整个工具在干任何事之前就死掉**。
- 裁剪按字符比例，句子中间截断 → 输出乱码；issue #99：「max_results=3 跑了 3 小时，输出很乱」
  （重试风暴 + 不稳定代理 + 烂截断的复合）。issue #72：prompt 仍超 4097 token。
- key 轮换差一错误：`self.cur_api = 0 if self.cur_api >= len(self.chat_api_list) - 1 else
  self.cur_api` 导致永远用不上最后一个 key。
- **无持久化/去重、monorepo 摊大饼**（auto_survey、ChatReviewerAndResponse、ChatPaper2Xmind
  子模块）——每次运行全部重抓，README 自认「仓库的文件做了一个整理，可能会有些路径和bug」。
- README「常见报错」区被 OpenAI 封号/代理问题统治（「API被OpenAI禁了... 千万不能用大陆和香港的
  节点」）+ `urllib3==1.25.11` 钉版本的 workaround——**基础设施脆弱，而非产品问题**。

## 五、教训与坑清单（面向 DeepSeek 批量打分 + 飞书/邮件推送）

### DeepSeek 批量打分
1. **没有任何项目做批量**——四个全是逐篇调用。对 50–200 篇/天的打分场景，
   单次调用多篇 JSON 打分（如 10 篇/次 + `response_format=json_object`）是对全体样本的明确改进；
   注意 DeepSeek 长批量 prompt 需要大 `max_tokens` 和超时预算。
2. **RunRiot 是成本控制范本**：LLM 之前启发式预打分 + 硬顶（5 篇/天）。
   HF-newsletter 是反面：不过滤全翻 ~50 篇，只因 DeepSeek 便宜 + prompt 短才活下来。
   200 篇/天必须有 LLM 前置过滤（新近窗 + 关键词/启发式分门）+ LLM 预算硬顶。
3. **永远不要无校验地信任 LLM 结构**。HF-newsletter 用 `'标题：' in translation` 校验再用脆弱正则
   反解析；ChatPaper 的死板 `xxx` 占位格式让它可读但不可解析。**要求 JSON、用 schema 校验、
   配确定性兜底**（RunRiot 的「摘要生成失败，请直接查看原文」模式），digest 永远能发出去。
4. **实践中有效的重试阶梯**：tenacity 指数退避（3 次）+ 单条内层重试 + 调用间固定 sleep(3s)
   + **每条处理完就把结果 checkpoint 到磁盘**（HF-newsletter 的临时文件模式）——
   跑 2 小时在第 49 篇崩溃也不损失任何东西。
5. **处理推理模型**：跳过 `delta.reasoning_content`、只留 `delta.content`（RunRiot），
   否则思维链污染输出。
6. **存在免费托管**：ModelScope `api-inference.modelscope.cn/v1` 提供 DeepSeek 模型免费推理
  （RunRiot）——个人追踪器可用，但预期有限速；可移植性还是要按 OpenAI 兼容
   `api.deepseek.com` 客户端模式写。

### 去重
7. **四个项目都没有跨运行去重——这是最大的共同缺口。** 存储键思路可抄（一天一文件），
   但要加持久的已见 paper ID 集合（arXiv ID 稳定；HF paper id == arXiv id），而非标题集合。
8. 运行内模糊标题去重（`SequenceMatcher` 阈值 0.85，RunRiot）能抓 arXiv 交叉列表重复；
   与跨运行 ID 去重组合使用。
9. 硬去重做不到时，RunRiot 的诚实兜底是极佳 UX：日期徽章（NEW TODAY / YESTERDAY / N DAYS AGO）
   + 横幅「X/Y 篇发表于 2 天前（可能已读过）」。
10. **提防公布日 vs 提交日陷阱**（RunRiot issue #1）：arXiv `published`/`updated` 与 HF
   `publishedAt` 互相不一致；窗口语义要显式固定并写进文档。

### 飞书/邮件推送格式
11. 已被验证的推送格式：(a) **内联 CSS 的 HTML 邮件卡片**——渐变头、编号标题 + 彩色日期徽章 +
    分数徽章、meta 行、左彩边摘要块、一个醒目链接按钮（RunRiot）；(b) **commit 到固定路径的
    markdown+HTML 期刊**（`newsletters/{date}_daily_paper.md`）——GitHub 直接渲染、零托管
   （HF-newsletter）；(c) GitHub issue + 标签做主题（ChatDailyPapers）；(d) 飞书知识库文件上传
    走 Lark 应用（`FEISHU_APP_ID/SECRET` + wiki 节点 token）——HF-newsletter 已用它传原始 JSON；
    同样的应用凭证模式适用于飞书机器人 webhook 消息。
12. 主题行惯例：`📚 arXiv Daily Paper Digest - YYYY-MM-DD`（日期进主题 + emoji 圈定范围）。
    每篇被验证有用的字段：编号排名、原文 + 译名标题、作者（截断 ~200 字）、日期徽章、分类 pill、
    **保留 1 位小数的数字分**、AI 摘要、且**恰好两个链接**（abs 页 + PDF）。
13. 邮件/飞书卡片的头部统计块（总数、热点）+ 脚注（"auto-generated / powered by DeepSeek"）
    ——两个项目独立收敛到同一设计。
14. Gmail/QQ/163 要用应用专用密码/授权码而非登录密码；587 端口 + STARTTLS
   （RunRiot README 三个提供商都写了）。

### 调度/运维
15. HF-newsletter 的 workflow 链是鲁棒模式：抓取 job（早 cron）→ artifact 交接 `date.txt` →
    处理 job 由 `workflow_run` + 兜底 cron 双触发，配 `concurrency` 组和「无数据/无变更即跳过」
   （`git diff --cached --quiet`）。
16. 竞争安全的 git push 需要 cherry-pick `-X theirs` 重试循环（3 次、间隔 5s）——
    定时并发下裸 `git push` 经常失败。
17. **保护 API key 不被 fork 消耗**：花钱的 job 前校验 `remote.origin.url`/`GITHUB_REPOSITORY`
   （HF-newsletter 的 `verify` job + `require_auth`）。
18. workflow 失败自动开 GitHub issue 附日志链接（HF-newsletter 的 `🚨 工作流运行失败` issue）——
    最便宜的告警；飞书 webhook 是自然升级。

### Digest UI / 分数呈现
19. ChatPaper 的六字段卡（标题+译名、作者、单位、关键词、链接+代码、四点摘要）是可扫读单元的
    金标准；RunRiot 补上了 ChatPaper 缺的：**数字分（1 位小数）+ 徽章阈值**（score >= 5 →
    "HIGH QUALITY"）让用户能分诊。对 DeepSeek 打分而言，「数字分 + 一句话理由」的组合
    **四个项目全都缺席**——这正是 PaperRadar 的空白点。
20. 衰落避免清单：不硬编码模型名/SDK 全局客户端（用可配 `base_url`/model）、启动路径不加载
    tokenizer（按字符数估算 token 即可——ChatPaper 的 tiktoken 缓存损坏 issue #275 直接弄死安装）、
    不提交凭证、不做字符比例截断（按句子边界截）、仓库保持单一用途
   （ChatPaper 的 monorepo 摊大饼弄坏了路径，README 自认）。
21. 吞吐预期管理：ChatPaper issue #99（3 篇/3 小时）说明顺序执行 + 重试 + 烂网络会恶性复合；
    50 篇 ×（1 次调用 + 3s sleep）没问题，但超过 ~100 篇/天要预算小并发池（每 worker 独立 key）。

### 关键证据 URL
- https://raw.githubusercontent.com/2404589803/hf-daily-paper-newsletter-chinese/main/Paper_metadata_download.py（HF API 端点）
- https://raw.githubusercontent.com/2404589803/hf-daily-paper-newsletter-chinese/main/HF-day-paper-deepseek.py（逐篇 prompt、重试、sleep(3)、临时文件 checkpoint）
- https://raw.githubusercontent.com/2404589803/hf-daily-paper-newsletter-chinese/main/.github/workflows/HF-day-paper-deepseek.yaml（workflow_run 链、artifact 日期交接、cherry-pick push、verify job）
- https://raw.githubusercontent.com/2404589803/hf-daily-paper-newsletter-chinese/main/.github/workflows/Paper_metadata_download.yaml（飞书 wiki 上传步骤）
- https://raw.githubusercontent.com/2404589803/hf-daily-paper-newsletter-chinese/main/newsletters/2026-10-16_daily_paper.md（输出格式样例）
- https://raw.githubusercontent.com/RunRiotComeOn/arXiv-Daily-Summarizer/main/fetch_papers.py（质量分、去重、ModelScope DeepSeek、HTML 邮件）
- https://raw.githubusercontent.com/RunRiotComeOn/arXiv-Daily-Summarizer/main/.github/workflows/daily_arxiv.yml（cron + secrets）
- https://raw.githubusercontent.com/justchenhao/ChatDailyPapers/main/main.py 及 /config.py、/github_issue.py（关键词门控、GPT 分段、issue 推送）
- https://raw.githubusercontent.com/kaixindelele/ChatPaper/main/chat_arxiv.py（六点 prompt、裁剪、key 轮换）
- https://api.github.com/repos/kaixindelele/ChatPaper（19,939 stars、archived=false）
- Issues：kaixindelele/ChatPaper #275（tiktoken JSONDecodeError）、#72（token>4097）、#99（3篇/3小时、输出乱）、#225（代理支持诉求）；2404589803/hf-daily-paper-newsletter-chinese #2/#3/#4（自动失败告警）；RunRiotComeOn/arXiv-Daily-Summarizer #1（日期问题）
