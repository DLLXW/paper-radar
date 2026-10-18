# 竞品调研 01：关键词订阅派（cv-arxiv-daily 全家桶）

> 调研日期：2026-10-18。方法：通过 GitHub 页面与 raw.githubusercontent.com 直接阅读源码、
> 配置文件、Actions workflow 与 issue 区。所有结论均附文件/URL 证据。

## 零、arXiv API 关键事实（适用所有项目）

- **礼貌限速**：arXiv 官方 API 手册（https://info.arxiv.org/help/api/user-manual.html）
  原文要求：*"In cases where the API needs to be called multiple times in a row, we encourage
  you to play nice and incorporate a 3 second delay in your code."* 这就是所有项目 sleep ~3s 的出处。
  cv-arxiv-daily 显式配置：`arxiv.Client(page_size=max_results, delay_seconds=3, num_retries=5)`
  （daily_arxiv.py 214、242 行）。
- **查询模式**：cv-arxiv-daily 构造 `(filter1 OR "multi word filter") AND (cat:cs.CV OR cat:cs.RO)`
  ——裸词项（不加 `abs:`/`ti:` 前缀，全字段搜索），并在 **Python 侧对 `primary_category` 后过滤**
  以剔除交叉列表（仅 `cat:` 过滤会命中 cross-list）。按 `arxiv.SortCriterion.SubmittedDate` 降序。
  无任何项目使用 `submittedDate:[range]` 子句——新近窗口全靠排序 + 小 max_results 隐式实现。
- **去重**：无项目用"已见数据库"或解析 git 历史。三者都在仓库里维护一个 **以 arXiv ID 为键的
  JSON 状态文件**，每次运行 `dict.update()` 合并。版本后缀被剥离（`2108.09112v1` → `2108.09112`），
  因此 v2 更新原地覆盖 v1。

## 一、Vincentqyw/cv-arxiv-daily（~1.5k★，Apache-2.0，活跃维护中）

证据：https://github.com/Vincentqyw/cv-arxiv-daily ，关键文件
`config.yaml`、`daily_arxiv.py`、`.github/workflows/cv-arxiv-daily.yml`、
`.github/workflows/update_paper_links.yml`、`docs/README.md`。
GitHub API 元数据：1506 stars / 567 forks，GitHub Pages 站 https://papers.vincentqin.tech ，
最后 push 2026-10-18（调研当日）。

### 架构
双文件 Python 应用（`daily_arxiv.py` + `config.yaml`，依赖仅 `requests`、`arxiv`、`pyyaml`），
由两个 GitHub Actions workflow 驱动并把生成文件 commit 回仓库。README 和 Jekyll GitHub Pages
站点从同一份 JSON 状态渲染。

### 配置格式（`config.yaml` 实例）
```yaml
max_results: 10                      # 每关键词每次运行
publish_readme: True
publish_gitpage: True
publish_wechat: False                # 只是生成 wechat.md 文件，非真实推送
json_readme_path: './docs/cv-arxiv-daily.json'   # 去重/状态库
md_readme_path: 'README.md'
md_gitpage_path: './docs/index.md'
max_papers_in_readme: 200            # GitHub 超过 512KB 的 markdown 不再渲染
archive_dir: './docs/archive'
code_check_days: 180
max_code_checks: 300
max_code_search_minutes: 40
code_checks_path: './docs/code-checks.json'
keywords:
    "SLAM":
        filters: ["SLAM", "Visual Odometry"]
    "World Model":
        filters: ["World Model", "World Simulator"]
        categories: ["cs.CV", "cs.RO", "cs.AI", "cs.LG", "cs.CL"]   # 按关键词覆盖分类
```
查询构造（`load_config`/`pretty_filters`）：多词 filter 加双引号、`" OR "` 连接、再包
`({query}) AND ({cats})`。

### 去重/状态
`update_json_file()` 读写 `docs/cv-arxiv-daily.json`（`{keyword: {arxiv_id: table_row}}`），
新抓取 `json_data[keyword].update(papers)` 合并后整文件重写。markdown 每次从 JSON 全量重建。
后果：JSON 只增不减，靠 (a) `max_papers_in_readme` 渲染截断 + (b) **按年归档**
（`write_archive()` 写 `docs/archive/<year>/<Keyword>.md` + 年度索引）缓解——明确原因是
"github 停止渲染超过 512KB 的 markdown"。

### 输出格式
- `README.md`：`## Updated on 2026.10.18` + 可折叠目录 + 每关键词一节，表格
  `|Publish Date|Title|Authors|PDF|Code|` + 回顶链接 + 归档表。表格行是**预渲染好的 markdown
  字符串存在 JSON 里**。
- `docs/index.md`：同数据加 Jekyll front matter（`layout: default`），HTML 锚点。
- `docs/wechat.md`：列表式输出供手动粘贴微信（非自动推送）。
- 标题里的 LaTeX 用 `pretty_math()` 正则保住 `$...$` 不破坏表格。

### GitHub Actions
- 每日 workflow：`cron: "0 0 */5 * *"`（每 5 天 00:00 **UTC**；无 TZ 环境变量——Actions cron
  恒为 UTC，他们接受现状）+ `workflow_dispatch`。
- 每周 workflow：`cron: "0 8 * * 1"` 跑 `python daily_arxiv.py --update_paper_links`
  （只回填代码链接，跳过 arXiv 抓取）。
- 两个 workflow 共享 `concurrency: {group: update-papers, cancel-in-progress: false}`
  （commit 说明：都写同一批生成文件，绝不能同时跑）。
- 用 `github-actions-x/commit@v2.9` 提交，`rebase: 'true'` + **显式文件列表**（README.md、
  两个 JSON、docs/archive、docs/code-checks.json），避免误提交其他东西。
- 使用文档（docs/README.md）提醒 fork 者去 Settings → Actions → Workflow permissions 开
  「Read and write permissions」——fork 用户的经典坑。

### 代码链接策略（PapersWithCode 死后）
PapersWithCode API 已死，现行 `get_code_link()` 管道：(1) 对摘要+comment 正则抓 github.com 链接
（免费）；(2) HF papers API `https://huggingface.co/api/papers/<arxiv_id>` 的 `githubRepo` 字段；
(3) 每周一次 GitHub code search `"arxiv_id" filename:README.md`（需 GITHUB_TOKEN，遵守 10 次/分钟
限制，间隔 ≥6.5s，尊重 `X-RateLimit-Remaining`/`Reset`/`Retry-After`，sleep 上限 120s，
`max_code_search_minutes: 40` 时间盒，每轮最多 300 篇，最久未查优先，
检查时间戳持久化在 `docs/code-checks.json`）。启发式拒绝 fork、"awesome/paper/list/zoo" 类仓库，
要求仓库名匹配论文标题前缀或是根 README 唯一引用。

### 失败处理
每关键词独立 try/except（注释原话：*arxiv is flaky, one failing query should not block the
other keywords*）；**全部关键词 0 篇时 raise RuntimeError**（防止 commit 一轮空更新）。
摘要批量重取用 `arxiv.Search(id_list=[...])`（每次 100 个 id）。

## 二、yyyanbj/arxiv-daily（179★，独立实现，部分已坏）

证据：https://github.com/yyyanbj/arxiv-daily ，文件 `config.py`、`daily_arxiv.py`、`main.py`、
`database/topic.yml`、`.github/workflows/arxiv-daily.yml`。最后 push 2026-10-18 但核心逻辑未动；
简介「Update Every 12th hours」。

与 cv-arxiv-daily 的差异：
- **配置**：`database/topic.yml`，嵌套 `topic → subtopic → 手写原始 arXiv 查询串`，如
  `"Federated Learning"AND("Personalized")`。**bug 模式**：`AND`/`OR` 两侧无空格
  （`'"Keypoint Detection"OR"Feature Descriptor"'`）是非法 arXiv 查询语法、返回错误结果
  （cv-arxiv-daily 曾有一模一样的 bug，靠 PR #9 修复）。该文件还展示了配置腐烂——
  "Edge Computing" 条目是约 100 词的 OR 链，含复制粘贴重复项。
- **两条代码路径**：`daily_arxiv.py`（顺序执行，`max_results=10`）和 `main.py`——基于 gevent
  `monkey.patch_all()`、16–32 个 greenlet 并发查 arXiv（限速风险），`max_results=30`，
  `fire` CLI（`python main.py run --env=production --power=16`），loguru 日志，pytz Asia/Shanghai。
- **去重**：同模式——根目录 `arxiv-daily.json`，id 为键，`dict.update()` 合并。
- **输出**：重建 `README.md`；**MkDocs 站**（`mkdocs.yml`，每主题 `docs/<topic>/<subtopic>.md`）；
  每日快照 `database/storage/storage_<YYYY-MM-DD>.md` 拷贝进 `docs/history/`
  （另一种归档哲学：按天快照 vs cv-arxiv-daily 按年按关键词）。
- **Workflow**：`cron: "* 0/12 * * *"`——**畸形 cron**（分钟通配 + 小时步进），实际在 0 点和
  12 点期间每分钟触发；设 `TZ: "Asia/Shanghai"` 环境变量（只影响日志/进程时间，不影响 cron 解析）；
  提交用 `git diff --exit-code || git commit` 然后 **`git push --force`**；MkDocs 部署到 gh-pages。
- **至今仍在调用已死的 PapersWithCode API**（`https://arxiv.paperswithcode.com/api/v0/papers/`，
  在抓取循环内逐篇调用）+ pre-2.0 的 `arxiv.Search.results()` API——两者今天都已坏。

## 三、lidq92/arxiv-daily（79★，停滞 fork）

证据：https://github.com/limzero 不适用；GitHub API 确认 `"fork": true, "parent":
"Vincentqyw/cv-arxiv-daily"`；简介原文 "[NOT UPDATED][To be updated with
http://export.arxiv.org/api/query!]"。最后 push 2025-09-21。

- 与父仓库相同的 config.yaml schema，`max_results: 20`，不支持按关键词覆盖分类，
  仍是**无空格的 OR 连接**（上游 PR #9 已修的查询 bug）。
- 代码链接查找更粗糙：未认证 GitHub `search/repositories` 按 star 排序取第一个
  （幻觉率高的启发式）+ 已死的 PapersWithCode API。
- Workflow：`cron: "0 0/12 * * *"`（合法，每 12h），同样的 commit action。
- **警示细节**：其 `README.md` 达 1,428,466 字节（1.4MB）——远超 GitHub 512KB markdown 渲染上限，
  即 cv-arxiv-daily 的 `max_papers_in_readme` + 按年归档设计所要避免的失败模式的实景。

## 四、用户真正想要什么（cv-arxiv-daily 全部 20 个 issue 逐条审阅）

来源：https://api.github.com/repos/Vincentqyw/cv-arxiv-daily/issues

1. **可靠性压倒一切**（#14、#16、#19「自 2025-07/06 起无更新」）。根因：PapersWithCode API 挂了
   （SSL 握手失败），且因为它在抓取循环内被逐篇调用，其异常导致论文被跳过——管道静默停更约
   2 个月，直到 PR #18（"Remove PapersWithCode API & Backfill papers since 2025-07-17"）。
   用户能发现仅因 README 的 "Updated on" 日期冻结了。
2. **LLM 摘要**（#3）：「扫描并总结新论文，以博客形式呈现」——最有分量的功能请求；
   维护者回复指向 ChatPaper 而未实现。
3. **推送通知**（#6）：有 PR 添加 `email_alert.py`（发现 JSON 中没有的新论文时发邮件）从未被合并
   ——维护者唯一回应是「请删掉临时 json 文件解决冲突」，展示了生成文件住在默认分支上造成的合并摩擦。
4. **关键词幽灵章节**（#5、#20）：改名/删除的关键词残留在目录和正文里，因为状态按关键词为键
   且 JSON 只增；空章节照样渲染。
5. **fork 定制摩擦**（#7、#8）：用户 fork 后换领域（CV→NLP）要同时改 workflow 环境变量和
   config.yaml；上游修复后（如 #18）fork 主要求「如何把修复同步到我以前的 fork」——
   生成文件 commit 到 main 让 fork 同步非常痛苦。

## 五、教训与坑清单（对 PaperRadar 的直接输入）

### 该抄的设计决策
1. **以 arXiv ID 为键的持久状态文件去重**（版本后缀剥离，修订版原地覆盖）。简单、可 diff、
   零基础设施。**改进点**：加 `first_seen_at` 日期以支持清理旧条目（cv-arxiv-daily 的 JSON
   永远增长，只在渲染层缓解）。
2. **显式 `delay_seconds=3, num_retries`**，与 arXiv 官方指南一致；每关键词独立 try/except +
   「总共 0 篇就硬失败」的护栏，避免坏天气提交空更新。
3. **抓取与渲染分离**：JSON 存结构化行，markdown 确定性重建。（cv-arxiv-daily 把预渲染 markdown
   行存进 JSON 正是关键词改名幽灵章节 (#5) 无解的根源——PaperRadar 只存结构化字段。）
4. **正确的分类限定**：查询里 `(terms) AND (cat:A OR cat:B)`，再加 `primary_category` 后滤掉
   交叉列表；配置支持按关键词覆盖分类（其 "World Model" 例子证明必要性——该词会撞 NLP/DB 论文）。
5. **第一天就规划 512KB 渲染预算**：首页限篇数、其余按年/关键词归档（lidq92 的 1.4MB README
   证明不规划的结局）。结构化归档（按年按关键词文件+索引表）优于 yyyanbj 的按天快照倾倒。
6. **workflow 上加 `concurrency` 组**，定时与手动跑不同时写状态文件；commit 用显式文件列表 + `rebase: true`。
7. **代码链接查找按成本分层**：摘要/comment 正则（免费）→ HF papers API → 认证版 GitHub code
   search（仅每周、时间盒、限速感知、最近最少检查轮转）。尊重 code search 10 次/分钟，处理 `Retry-After`。

### 必须避开的坑
1. **抓取循环内零外部 HTTP 依赖**——PapersWithCode 宕机让整个生态的摄取静默停摆数月。
   每个可选增强都要包成「失败 → 降级为无此信息」，绝不能「失败 → 论文没保存」；
   并对「连续 N 天 0 新论文」主动告警（这些项目都没有，用户只能靠肉眼发现）。
2. **查询串构造 bug 是静默的**：`OR`/`AND` 缺空格（`"a"OR"b"`）返回「错而非空」的结果
   （yyyanbj 的 topic.yml 至今如此；上游靠 PR #9 才发现）。PaperRadar 从结构化配置程序化拼查询并校验，
   禁止用户手写原始查询串。
3. **隐式新近窗口有损**：`sortBy=submittedDate` + `max_results=10` 意味着两次运行间若爆发 >10 篇
   匹配论文会被静默截断。应使用显式 `submittedDate:[fromDate TO now]` 范围（或
   `lastUpdated:` 日期），下界取自上次成功运行时间，max_results 放宽。
4. **Cron 是 UTC 的，要明说并处理**：无项目为 cron 设真实时区。还要校验 cron 表达式——
   yyyanbj 的 `* 0/12 * * *` 实际在 0 点和 12 点期间每分钟触发（上面还压着 32 greenlet 的
   gevent 并发轰炸 arXiv）。arXiv 工作日约美东 20:00 公布新论文，所以 UTC 00:00 后确实是好时隙——
   但要刻意选择而非碰巧。
5. **关键词集合变更留幽灵**：按关键词为键的状态意味着改名后旧章节永生；状态应按论文 ID 为键、
   关键词只是字段标签，空章节自动修剪。
6. **生成文件 commit 到默认分支**引发 fork 同步灾难和 PR 冲突（issue #6/#7/#8 及 fork 同步提问）。
   若在意社区 fork，状态放孤立分支（如 `data`）或独立存储，workflow 要容忍分歧而不是
   `git push --force`（yyyanbj 的做法）。
7. **用户要的是摘要和推送，不只是表格**——最流行的项目里仅有的两个实质功能请求
   （#3 LLM 摘要、#6 邮件告警）都没被实现。这就是新追踪者的open ground。
8. **`arxiv` PyPI 包的 API 会变**（2.0 移除 `Search.results()`，必须走 `arxiv.Client`），
   且 yyyanbj 与 lidq92 至今卡在死接口上。PaperRadar 干脆不用该包，直接打原生 Atom API 并包一层薄适配。
