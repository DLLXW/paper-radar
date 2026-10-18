# PaperRadar 与现有项目的区别（README「Why PaperRadar」素材）

> 调研日期：2026-10-18。逐项目的源码级证据见 [research/](research/) 三份报告。

## 逐维度对比

| 维度 | cv-arxiv-daily 类<br>（关键词订阅派，1.5k★） | ChatPaper<br>（LLM 摘要派，19.9k★已停更） | DeepSeek 日报类<br>（HF中文解读/邮件摘要） | MCP server 类<br>（paper-search-mcp 2.8k★等） | **PaperRadar** |
|---|---|---|---|---|---|
| 形态 | fork 模板，Actions 定时 commit README | CLI 工具 + 已冷清网站 | fork + Actions，产出邮件/期刊文件 | 给 Agent 按需检索的工具 | 独立 Web 速递台（常驻服务） |
| 过滤 | 纯关键词，命中即上墙 | 关键词+日期窗，无排序 | 基本无过滤（HF 榜单全量翻译）或硬顶 5 篇 | 无（用户主动搜） | 关键词粗筛 + **LLM 按研究方向打分排序** |
| 输出深度 | 标题+作者+链接表格 | GPT 全文六段卡（贵、慢，3篇/3小时） | 翻译摘要，逐篇调用 | 原始文本/分节读取 | **分数 + 一句话理由**；可选全文双语精读 |
| 交互 | 无（单向只读） | 无 | 无（单向推送） | 有，但无「我的方向」概念 | **可筛选/收藏/忽略**，反馈为未来推荐埋点 |
| 交付渠道 | commit README / GitHub Pages | CLI / chatpaper.com | 邮件 HTML / repo md / GitHub issue | MCP 工具结果 | Web 台 + 飞书 + 邮件 + 每日 md 工件 |
| 上手门槛 | fork→改 yaml→配 secrets→开 Actions 写权限 | pip 装 + OpenAI key（**硬编码 GPT-3.5，已不可用**） | fork + 配 secrets | 配 MCP 环境 | **一个 DeepSeek key，docker 一行命令** |
| 版本更新检测 | 无 | 无 | 无 | 有（本地文件层面） | **有**（v2 →「已更新」徽章） |
| 工程可靠性 | 曾静默停更 2 个月；状态文件只增不减（有 fork README 膨胀到 1.4MB 不渲染） | 启动加载 tokenizer、缓存坏即崩、明文 key | 已较好（checkpoint、失败告警） | 已较好（限速、退避） | 吸收全部前人事故教训 |

## 五个真正拉开差距的点

1. **「相关度打分 + 一句话理由」没有人做**——调研中最明确的空白。RunRiot 有启发式质量分，
   但那是通用「论文质量」（摘要长度、作者数、标题热词），与用户研究方向无关；其余项目全是
   关键词命中即推，噪音靠人肉过滤。40 篇/天规模下，「先看分数和理由再决定点不点」是体验代差。
2. **交互式速递台 vs 单向广播**——现有项目把 digest 当「发布物」（commit 到 README、发邮件、
   发 issue），用户只能被动接收。PaperRadar 的 digest 可操作：收藏、忽略、筛选、手动刷新，
   这些操作数据为将来的个性化推荐铺路（前人完全没建模「用户在乎什么」的动态学习）。
3. **精读闭环是无主之地**——Agent 派调研原文结论：「没有人闭环到全文处理：没有 OCR、
   没有翻译、没有双语对齐」。所有项目到摘要为止，读全文用户自己开 PDF 忍英文。
   PaperRadar 通过可选连接器把 local_ai_toolkit 变成插件，是唯一把「发现」和「读懂」
   接起来的设计，也是别人短期抄不走的（他们没有现成的本地 OCR+翻译+双语渲染栈）。
4. **部署门槛低一个数量级**——cv-arxiv-daily 类要 fork、改 yaml、配 secrets、开 Actions 写权限
   （其 issue 区高频求助）；ChatPaper 硬编码模型已不可用。PaperRadar：填一个 key，`docker run`，
   30 秒出界面。
5. **可靠性是被前人事故倒逼出来的**——抓取循环零外部依赖、零结果主动告警、批次 checkpoint、
   无 key 降级、状态保留期清理。cv-arxiv-daily 恰恰是靠用户发现「README 日期冻结」才知道
   管道死了 2 个月。

## 诚实的边界

以上**没有任何单点是革命性的**——arXiv 抓取、LLM 摘要、飞书推送、Web UI 每个都有人做过。
PaperRadar 的赌注是：**组合（过滤→排序→交互→精读的完整链路）+ 门槛（一个 key 即用）+
打磨（前人的坑全部绕开）**。这是从「又一个 arxiv daily」里跳出来的三条腿。
