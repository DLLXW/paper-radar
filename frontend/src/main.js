import renderMathInElement from "katex/contrib/auto-render";
import "katex/dist/katex.min.css";

const API_BASE = "/api";

const state = {
  date: todayStr(),
  status: "",
  minScore: "",
  topic: "",
  start: 0,
  papers: [],
  total: 0,
  hasNext: false,
  nextStart: null,
  health: null,
  topics: [], // 画像 topics，用于筛选 chips 与卡片徽章的方向着色
  topicIndex: {}, // topic 名 → { group, gi }
  wasRunning: false,
  pollTimer: null,
};

// ---------- 基础工具 ----------

async function fetchJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  let body = null;
  try {
    body = await response.json();
  } catch {
    /* 非 JSON 响应 */
  }
  if (!response.ok) {
    const detail = body && body.detail ? body.detail : null;
    const error = new Error(
      typeof detail === "string" ? detail : `HTTP ${response.status}`
    );
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

function todayStr() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(
    now.getDate()
  ).padStart(2, "0")}`;
}

function shiftDate(dateStr, days) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(y, m - 1, d + days);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(
    dt.getDate()
  ).padStart(2, "0")}`;
}

function renderMath(container) {
  renderMathInElement(container, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "$", right: "$", display: false },
      { left: "\\(", right: "\\)", display: false },
    ],
    throwOnError: false,
  });
}

// ---------- 速递渲染 ----------

const paperList = document.querySelector("#paperList");
const digestSummary = document.querySelector("#digestSummary");
const loadMoreButton = document.querySelector("#loadMoreButton");
const runBanner = document.querySelector("#runBanner");

function scoreTier(score) {
  if (score === null || score === undefined) return "unscored";
  if (score >= 75) return "high";
  if (score >= 50) return "mid";
  return "low";
}

const TIER_LABEL = { high: "高相关", mid: "中相关", low: "低相关", unscored: "未打分" };

function dateBadge(publishedAt) {
  if (!publishedAt) return "";
  const pub = publishedAt.slice(0, 10);
  const today = todayStr();
  if (pub === today) return `<span class="chip today" title="${pub}">今日</span>`;
  if (pub === shiftDate(today, -1)) return `<span class="chip" title="${pub}">昨日</span>`;
  const days = Math.round((new Date(today) - new Date(pub)) / 86400000);
  return `<span class="chip" title="${pub}">${days} 天前</span>`;
}

function topicChip(paper) {
  if (!paper.topic) return "";
  const info = state.topicIndex[paper.topic];
  const gi = info ? info.gi : 3;
  const group = info ? info.group : "";
  return `<span class="chip topic g${gi}" title="${escapeHtml(group || "未分组方向")}">🧭 ${escapeHtml(paper.topic)}</span>`;
}

function renderPaperCard(paper, index) {
  const tier = scoreTier(paper.score);
  const scoreValue = paper.score === null || paper.score === undefined ? "—" : Math.round(paper.score);

  if (paper.kind === "version_update") {
    return `
      <article class="paper-card version-card" data-paper-id="${escapeHtml(paper.id)}">
        <div class="card-top">
          <span class="chip v2">📄 已更新 ${escapeHtml(paper.version)}</span>
          <h3 class="paper-title" style="flex:1"><a href="${escapeHtml(paper.arxiv_url)}" target="_blank" rel="noopener">${escapeHtml(paper.title)}</a></h3>
          ${dateBadge(paper.published_at)}
          <a class="card-link" href="${escapeHtml(paper.arxiv_url)}" target="_blank" rel="noopener">abs</a>
        </div>
      </article>`;
  }

  const authors = (paper.authors || []).slice(0, 4).join(", ") +
    ((paper.authors || []).length > 4 ? " 等" : "");
  const keywordChips = (paper.matched_keywords || [])
    .map((kw) => `<span class="chip">${escapeHtml(kw.replace("hf:daily", "HF 热门"))}</span>`)
    .join("");
  const sourceChip = paper.source === "hf_daily"
    ? '<span class="chip hf">HF Daily</span>'
    : `<span class="chip">${escapeHtml(paper.primary_category || "arXiv")}</span>`;
  const venueChip = paper.venue
    ? `<span class="chip v2" title="${escapeHtml(paper.comment || "")}">🎯 ${escapeHtml(paper.venue)}</span>`
    : "";

  const starred = paper.status === "starred";
  const dismissed = paper.status === "dismissed";
  const readerReady = Boolean(state.health?.reader_configured && paper.pdf_url);
  const readChip = paper.read_doc_name
    ? `<a class="read-chip" href="${escapeHtml(state.health?.reader_url || "#")}" target="_blank" rel="noopener">📖 已在精读服务</a>`
    : "";

  return `
    <article class="paper-card ${dismissed ? "dismissed" : ""}" data-paper-index="${index}">
      <div class="card-top">
        <div class="score-badge ${tier}">
          ${scoreValue}<small>${TIER_LABEL[tier]}</small>
        </div>
        <div class="paper-main">
          <h3 class="paper-title">
            <a href="${escapeHtml(paper.arxiv_url)}" target="_blank" rel="noopener">${escapeHtml(paper.title)}</a>
          </h3>
          <div class="paper-meta">
            ${topicChip(paper)}
            ${sourceChip}
            ${venueChip}
            ${dateBadge(paper.published_at)}
            ${keywordChips}
          </div>
          ${authors ? `<div class="paper-authors">${escapeHtml(authors)}</div>` : ""}
          ${
            paper.reason
              ? `<blockquote class="paper-reason">${escapeHtml(paper.reason)}</blockquote>`
              : ""
          }
          ${
            paper.summary
              ? `<p class="paper-summary">🧠 ${escapeHtml(paper.summary)}</p>`
              : ""
          }
          <p class="paper-abstract" data-abstract title="点击展开/收起">${escapeHtml(paper.abstract)}</p>
          <span class="abstract-toggle">展开原文摘要 ▾</span>
          <div class="card-actions">
            <button class="action-button ${starred ? "on" : ""}" data-action="star" type="button">
              ${starred ? "★ 已星标" : "☆ 星标"}
            </button>
            <button class="action-button danger" data-action="dismiss" type="button">
              ${dismissed ? "↩ 恢复" : "✕ 忽略"}
            </button>
            ${
              readerReady
                ? `<button class="action-button" data-action="read" type="button"${paper.read_doc_name ? " disabled" : ""}>${paper.read_doc_name ? "已导入精读" : "📚 去精读"}</button>`
                : ""
            }
            ${readChip}
            <div class="card-links">
              <a href="${escapeHtml(paper.arxiv_url)}" target="_blank" rel="noopener">abs</a>
              <a href="${escapeHtml(paper.pdf_url || paper.arxiv_url.replace("/abs/", "/pdf/"))}" target="_blank" rel="noopener">pdf</a>
            </div>
          </div>
        </div>
      </div>
    </article>`;
}

function renderDigest() {
  const high = state.papers.filter((p) => scoreTier(p.score) === "high").length;
  const scored = state.papers.filter((p) => p.score !== null && p.score !== undefined).length;
  digestSummary.innerHTML =
    state.total > 0
      ? `<span class="summary-chip">共 ${state.total} 篇</span>
         <span class="summary-chip">已展示 ${state.papers.length} 篇 · 其中已打分 ${scored}</span>
         <span class="summary-chip">🔥 高相关 ${high}</span>`
      : "";

  if (state.papers.length === 0) {
    paperList.innerHTML = emptyStateHtml();
    loadMoreButton.classList.add("hidden");
    return;
  }

  paperList.innerHTML = state.papers.map(renderPaperCard).join("");
  renderMath(paperList);
  loadMoreButton.classList.toggle("hidden", !state.hasNext);
}

function emptyStateHtml() {
  if (state.date === todayStr()) {
    return `
      <div class="empty-card">
        <div class="radar-icon" aria-hidden="true">📡</div>
        <h2>雷达待命中</h2>
        <p>今天还没有速递。确认「设置 → 研究画像」里的关键词与分类后，点一次抓取即可；<br>
           之后每天会按抓取时刻自动运行。</p>
        <div>
          <button class="primary-button" data-goto-fetch type="button">🛰 立即抓取</button>
          <button class="soft-button" data-goto-settings type="button">去配置画像</button>
        </div>
      </div>`;
  }
  return `
    <div class="empty-card">
      <div class="radar-icon" aria-hidden="true">🗓</div>
      <h2>${escapeHtml(state.date)} 无速递</h2>
      <p>该日期没有抓取到论文，可能当天尚未运行或无命中。</p>
    </div>`;
}

async function loadDigest({ append = false } = {}) {
  if (!append) state.start = 0;
  const params = new URLSearchParams({ date: state.date, limit: 60, start: state.start });
  if (state.status) params.set("status", state.status);
  if (state.minScore) params.set("min_score", state.minScore);
  if (state.topic) params.set("topic", state.topic);
  try {
    const data = await fetchJson(`/papers?${params}`);
    state.total = data.total;
    state.hasNext = data.has_more;
    state.nextStart = data.next_start;
    state.papers = append ? state.papers.concat(data.papers) : data.papers;
  } catch (error) {
    state.papers = [];
    state.total = 0;
    state.hasNext = false;
    showBanner(`速递加载失败：${error.message}`, "error");
  }
  renderDigest();
}

// ---------- 运行状态与轮询 ----------

const fetchButton = document.querySelector("#fetchButton");
const backendStatus = document.querySelector("#backendStatus");
const nextRun = document.querySelector("#nextRun");

function showBanner(text, tone = "info") {
  runBanner.className = `run-banner ${tone === "error" ? "error" : ""}`;
  runBanner.innerHTML =
    tone === "error" ? `⚠️ ${escapeHtml(text)}` : `<span class="spinner"></span> ${escapeHtml(text)}`;
}

function hideBanner() {
  runBanner.classList.add("hidden");
}

async function loadStatus() {
  let data;
  try {
    data = await fetchJson("/papers/status");
  } catch {
    return;
  }
  if (data.next_run_at) {
    nextRun.textContent = `下次自动抓取 ${data.next_run_at.slice(11, 16)}`;
  }
  if (data.running) {
    state.wasRunning = true;
    showBanner("雷达扫描中：抓取 → 打分 → 生成速递…");
    fetchButton.disabled = true;
    startPolling();
  } else {
    fetchButton.disabled = false;
    if (state.wasRunning) {
      state.wasRunning = false;
      stopPolling();
      hideBanner();
      const s = data.last_run_summary;
      if (s) {
        showBanner(`本轮完成：新抓 ${s.fetched} 篇 · 打分 ${s.scored} 篇`, "done");
        setTimeout(hideBanner, 6000);
      }
      loadDigest();
    }
  }
}

function startPolling() {
  if (state.pollTimer) return;
  state.pollTimer = setInterval(loadStatus, 4000);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

// ---------- 卡片操作（事件委托） ----------

paperList.addEventListener("click", async (event) => {
  const gotoFetch = event.target.closest("[data-goto-fetch]");
  if (gotoFetch) {
    triggerFetch();
    return;
  }
  const gotoSettings = event.target.closest("[data-goto-settings]");
  if (gotoSettings) {
    document.querySelector('[data-module-nav="settings"]').click();
    return;
  }
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const card = button.closest("[data-paper-index]");
  if (!card) return;
  const paper = state.papers[Number(card.dataset.paperIndex)];
  if (!paper) return;

  if (button.dataset.action === "star") {
    const next = paper.status === "starred" ? "new" : "starred";
    try {
      await fetchJson(`/papers/${encodeURIComponent(paper.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: next }),
      });
      paper.status = next;
      renderDigest();
    } catch (error) {
      showBanner(`星标失败：${error.message}`, "error");
    }
  }

  if (button.dataset.action === "dismiss") {
    const next = paper.status === "dismissed" ? "new" : "dismissed";
    try {
      await fetchJson(`/papers/${encodeURIComponent(paper.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: next }),
      });
      paper.status = next;
      renderDigest();
    } catch (error) {
      showBanner(`操作失败：${error.message}`, "error");
    }
  }

  if (button.dataset.action === "read") {
    button.disabled = true;
    button.textContent = "导入中…";
    try {
      const result = await fetchJson(`/papers/${encodeURIComponent(paper.id)}/read`, {
        method: "POST",
      });
      paper.read_doc_name = result.doc_name;
      renderDigest();
      showBanner(`已送入精读服务（${result.doc_name || "ok"}），可前往 reader 实例查看`, "done");
      setTimeout(hideBanner, 5000);
    } catch (error) {
      button.disabled = false;
      button.textContent = "📚 去精读";
      const hint = error.body?.detail?.hint || error.body?.detail?.message || error.message;
      showBanner(`精读失败：${hint}`, "error");
      setTimeout(hideBanner, 6000);
    }
  }
});

// 摘要展开/收起（委托）
paperList.addEventListener("click", (event) => {
  const toggle = event.target.closest(".abstract-toggle");
  const abstractEl = toggle
    ? toggle.previousElementSibling
    : event.target.closest(".paper-abstract");
  if (!abstractEl) return;
  abstractEl.classList.toggle("expanded");
  if (toggle) {
    toggle.textContent = abstractEl.classList.contains("expanded") ? "收起原文摘要 ▴" : "展开原文摘要 ▾";
  }
});

// ---------- 抓取触发 ----------

async function triggerFetch() {
  try {
    await fetchJson("/papers/fetch", { method: "POST" });
    state.wasRunning = true;
    showBanner("雷达扫描中：抓取 → 打分 → 生成速递…");
    startPolling();
  } catch (error) {
    if (error.status === 409) {
      state.wasRunning = true;
      showBanner("本轮抓取已在进行中，稍候…");
      startPolling();
    } else {
      showBanner(`触发失败：${error.message}`, "error");
    }
  }
}

fetchButton.addEventListener("click", triggerFetch);

// ---------- 工具栏与日期导航 ----------

const datePicker = document.querySelector("#datePicker");
const statusTabs = document.querySelector("#statusTabs");
const minScoreSelect = document.querySelector("#minScore");
const refreshButton = document.querySelector("#refreshButton");

datePicker.value = state.date;
datePicker.addEventListener("change", () => {
  state.date = datePicker.value || todayStr();
  loadDigest();
});
document.querySelector("#prevDay").addEventListener("click", () => {
  state.date = shiftDate(state.date, -1);
  datePicker.value = state.date;
  loadDigest();
});
document.querySelector("#nextDay").addEventListener("click", () => {
  state.date = shiftDate(state.date, 1);
  datePicker.value = state.date;
  loadDigest();
});
document.querySelector("#todayButton").addEventListener("click", () => {
  state.date = todayStr();
  datePicker.value = state.date;
  loadDigest();
});

statusTabs.addEventListener("click", (event) => {
  const item = event.target.closest("[data-status]");
  if (!item) return;
  for (const tab of statusTabs.querySelectorAll(".seg-item")) {
    tab.classList.toggle("active", tab === item);
  }
  state.status = item.dataset.status;
  loadDigest();
});

minScoreSelect.addEventListener("change", () => {
  state.minScore = minScoreSelect.value;
  loadDigest();
});

// ---------- topic 筛选 ----------

const topicFilter = document.querySelector("#topicFilter");

function applyTopicIndex() {
  const groups = [];
  const index = {};
  for (const t of state.topics) {
    const group = t.group || "未分组方向";
    if (!groups.includes(group)) groups.push(group);
    index[t.name] = { group, gi: groups.indexOf(group) % 4 };
  }
  state.topicIndex = index;
}

function renderTopicFilter() {
  applyTopicIndex();
  if (!state.topics.length) {
    topicFilter.classList.add("hidden");
    topicFilter.innerHTML = "";
    return;
  }
  const chips = state.topics.map(
    (t) =>
      `<button class="topic-chip${state.topic === t.name ? " active" : ""}" data-topic-name="${escapeHtml(t.name)}" type="button" title="${escapeHtml(t.group || "")}">${escapeHtml(t.name)}</button>`
  );
  topicFilter.innerHTML = `<button class="topic-chip${state.topic ? "" : " active"}" data-topic-name="" type="button">全部 topic</button>${chips.join("")}`;
  topicFilter.classList.remove("hidden");
}

topicFilter.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-topic-name]");
  if (!chip) return;
  state.topic = chip.dataset.topicName;
  renderTopicFilter();
  loadDigest();
});

refreshButton.addEventListener("click", () => loadDigest());

loadMoreButton.addEventListener("click", () => {
  state.start = state.nextStart;
  loadDigest({ append: true });
});

// ---------- 导航切换 ----------

const navList = document.querySelector("#navList");
const panels = [...document.querySelectorAll("[data-module]")];

navList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-module-nav]");
  if (!button) return;
  const target = button.dataset.moduleNav;
  for (const item of navList.querySelectorAll(".nav-item")) {
    item.classList.toggle("active", item === button);
  }
  for (const panel of panels) {
    panel.classList.toggle("active", panel.dataset.module === target);
  }
});

// ---------- 健康检查与服务状态 ----------

const serviceStatus = document.querySelector("#serviceStatus");
const refreshHealthButton = document.querySelector("#refreshHealthButton");

function renderStatusLine(ok, text) {
  const dot = backendStatus.querySelector(".status-dot");
  const label = backendStatus.querySelector(".status-text");
  dot.className = `status-dot ${ok ? "ok" : "error"}`;
  label.textContent = text;
}

function renderServiceStatus(health) {
  const rows = [
    { label: "服务版本", value: `v${health.version}`, tone: "ok" },
    { label: "数据库（SQLite）", value: health.db === "ok" ? "正常" : "异常", tone: health.db === "ok" ? "ok" : "error" },
    { label: "LLM 打分", value: health.llm_configured ? "已配置" : "未配置（跳过打分）", tone: health.llm_configured ? "ok" : "off" },
    { label: "推送渠道", value: health.push_channels.length ? health.push_channels.join(" + ") : "未配置", tone: health.push_channels.length ? "ok" : "off" },
    { label: "精读服务", value: health.reader_configured ? "已连接" : "未配置（可选）", tone: health.reader_configured ? "ok" : "off" },
  ];
  serviceStatus.innerHTML = rows
    .map(
      (row) => `
        <div class="status-row">
          <dt>${row.label}</dt>
          <dd class="value-${row.tone}">${row.value}</dd>
        </div>`
    )
    .join("");
}

async function loadHealth() {
  renderStatusLine(false, "正在连接后端…");
  try {
    const health = await fetchJson("/health");
    state.health = health;
    renderStatusLine(true, `后端在线 · v${health.version}`);
    renderServiceStatus(health);
    renderDigest(); // reader 配置可能变化，重渲染卡片
  } catch (error) {
    renderStatusLine(false, "无法连接后端");
    serviceStatus.innerHTML = `
      <div class="status-row">
        <dt>后端连接</dt>
        <dd class="value-error">失败（${escapeHtml(error.message)}）</dd>
      </div>`;
  }
}

refreshHealthButton.addEventListener("click", loadHealth);

// ---------- 推送自检 ----------

const pushTestButton = document.querySelector("#pushTestButton");
const pushTestResult = document.querySelector("#pushTestResult");

pushTestButton.addEventListener("click", async () => {
  pushTestButton.disabled = true;
  pushTestButton.textContent = "测试中…";
  pushTestResult.classList.remove("hidden");
  pushTestResult.textContent = "正在向已配置渠道发送测试消息…";
  try {
    const result = await fetchJson("/push/test", { method: "POST" });
    const lines = Object.entries(result.channels)
      .filter(([key]) => key !== "_configured")
      .map(([name, outcome]) => {
        const mark =
          outcome.status === "ok" ? "✅" : outcome.status === "skipped" ? "⚪" : "❌";
        const extra = outcome.error ? `（${escapeHtml(outcome.error)}）` : outcome.reason ? `（${escapeHtml(outcome.reason)}）` : "";
        return `<div>${mark} ${name}：${escapeHtml(outcome.status)}${extra}</div>`;
      });
    pushTestResult.innerHTML = `总体：<b class="value-${result.status === "ok" ? "ok" : "error"}">${result.status}</b><br>${lines.join("")}`;
  } catch (error) {
    pushTestResult.innerHTML = `<span class="value-error">自检失败：${escapeHtml(error.message)}</span>`;
  } finally {
    pushTestButton.disabled = false;
    pushTestButton.textContent = "推送自检";
  }
});

// ---------- 画像表单（方向 → topic → 关键词） ----------

const profileCategories = document.querySelector("#profileCategories");
const profileExcludes = document.querySelector("#profileExcludes");
const profileLimit = document.querySelector("#profileLimit");
const saveProfileButton = document.querySelector("#saveProfileButton");
const profileSaved = document.querySelector("#profileSaved");
const topicEditor = document.querySelector("#topicEditor");
const addTopicButton = document.querySelector("#addTopicButton");

const EMPTY_TOPIC = { group: "", name: "", description: "", keywords: [] };

function topicRowHtml(topic) {
  return `
    <div class="topic-row" data-topic-row>
      <div class="topic-row-head">
        <input class="t-group" type="text" placeholder="方向，如：用增权益与因果" value="${escapeHtml(topic.group || "")}" />
        <input class="t-name" type="text" placeholder="topic 名称，如：DFL/决策一体化" value="${escapeHtml(topic.name || "")}" />
        <button class="icon-button" data-topic-action="remove" type="button" title="删除该 topic">✕</button>
      </div>
      <input class="t-desc" type="text" placeholder="一句话描述（进入 LLM 打分提示，可留空）" value="${escapeHtml(topic.description || "")}" />
      <textarea class="t-keywords" rows="3" placeholder="关键词，每行一个；可加 @分类 限定，如：&#10;decision-focused learning&#10;scaling law @cs.IR">${escapeHtml((topic.keywords || []).join("\n"))}</textarea>
    </div>`;
}

function renderTopicEditor(topics) {
  topicEditor.innerHTML = topics.map(topicRowHtml).join("");
}

function readTopics() {
  return [...topicEditor.querySelectorAll("[data-topic-row]")].map((row) => ({
    group: row.querySelector(".t-group").value.trim(),
    name: row.querySelector(".t-name").value.trim(),
    description: row.querySelector(".t-desc").value.trim(),
    keywords: row
      .querySelector(".t-keywords")
      .value.split(/\n/)
      .map((s) => s.trim())
      .filter(Boolean),
  }));
}

addTopicButton.addEventListener("click", () => {
  topicEditor.insertAdjacentHTML("beforeend", topicRowHtml(EMPTY_TOPIC));
});

topicEditor.addEventListener("click", (event) => {
  const button = event.target.closest("[data-topic-action='remove']");
  if (!button) return;
  button.closest("[data-topic-row]").remove();
});

async function loadProfile() {
  try {
    const profile = await fetchJson("/profile");
    profileCategories.value = (profile.categories || []).join(", ");
    const topics = profile.topics && profile.topics.length ? profile.topics : [EMPTY_TOPIC];
    renderTopicEditor(topics);
    profileExcludes.value = (profile.exclude_keywords || []).join("\n");
    profileLimit.value = profile.daily_limit;
    state.topics = profile.topics || [];
    renderTopicFilter();
  } catch (error) {
    profileSaved.textContent = `画像加载失败：${error.message}`;
  }
}

saveProfileButton.addEventListener("click", async () => {
  const payload = {
    categories: profileCategories.value
      .split(/[,\n]/)
      .map((s) => s.trim())
      .filter(Boolean),
    topics: readTopics(),
    exclude_keywords: profileExcludes.value
      .split(/\n/)
      .map((s) => s.trim())
      .filter(Boolean),
    daily_limit: Number(profileLimit.value) || 30,
  };
  saveProfileButton.disabled = true;
  try {
    const saved = await fetchJson("/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.topics = saved.topics || [];
    renderTopicFilter();
    profileSaved.textContent = "已保存 ✓ 下次抓取生效";
    setTimeout(() => (profileSaved.textContent = ""), 4000);
  } catch (error) {
    const detail = error.body?.detail;
    profileSaved.textContent = `保存失败：${typeof detail === "string" ? detail : error.message}`;
  } finally {
    saveProfileButton.disabled = false;
  }
});

// ---------- 启动 ----------

loadHealth();
loadProfile();
loadDigest();
loadStatus();
