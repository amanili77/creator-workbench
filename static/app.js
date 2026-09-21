// ========== 全局状态 ==========
let currentMood = 3;
let currentScriptId = null;
let currentScriptAccount = "main";
let scriptSaveTimer = null;
let scriptAssistSelection = null;
let scriptRichSelection = null;
let scriptReviewState = null;
let scriptLibraryState = { scripts: [], finalOpen: false, trashOpen: false };
let currentMonthOffset = 0;
let weeklyPlanCache = [];
let calendarLoadToken = 0;
let calendarHolidayCache = {};
let calendarHolidaySource = null;
let calendarHoverTimer = null;
let calendarHoverDay = null;
let calendarHoverPinned = false;
let calendarPlansByDate = new Map();
let creatorAccounts = [];
const pendingAccountAvatars = {};
let assistantState = { briefing: null, context: null, messages: [] };
let assistantRecognition = null;
let assistantListening = false;
let assistantAutoSpeak = false;
let assistantVoiceEnabled = localStorage.getItem("xiaoli_voice_enabled") !== "0";
let assistantVoiceOptions = [];
let assistantSpeechRun = 0;
let assistantSelectedVoice = localStorage.getItem("xiaoli_voice_name") || "";
let xiaoliPetClickTimer = null;
let xiaoliPetResetTimer = null;
let xiaoliPetIdleTimer = null;
let xiaoliPetBlinkTimer = null;
let xiaoliPetClickCount = 0;
let ideaStreamState = { items: [], filter: "ima", query: "", sort: "newest", visibleLimit: 12 };
let ideaUnifiedSource = "ima";
let topicIdeaExpanded = false;
let topicLibraryState = {
    topics: [],
    status: "all",
    trashOpen: false,
    detailId: "",
};
let currentImaReaderNoteId = "";
let currentImaReaderTitle = "";
let douyinIdeaState = {
    loggedIn: false,
    ready: false,
    busy: false,
    recentCount: 0,
    stats: { total_favorites: 0, unanalyzed_count: 0, total_inspirations: 0 },
};
const CONTENT_ACCOUNTS = {
    main: {
        key: "main",
        label: "大号",
        fullLabel: "主账号",
        topicGuide: "先看它能为观众解决什么问题，再决定是否要做。",
        topicHint: "观点要清楚，并写明真实依据来自哪里。",
    },
    vlog: {
        key: "vlog",
        label: "小号 Vlog",
        fullLabel: "小号 · 创作 Vlog",
        topicGuide: "只记录真实发生的创作过程，把变化、困难和感受留下来。",
        topicHint: "不用想宏大选题，先写清今天发生了什么、哪个瞬间值得拍。",
    },
    ad: {
        key: "ad",
        label: "广告",
        fullLabel: "广告 · 商单",
        topicGuide: "商单脚本要如实介绍产品或服务，不使用绝对化用语，不做效果承诺。",
        topicHint: "写清产品、卖点和真实体验。",
    },
};
// 知识库里手写的文件：id 用 vault: 前缀区分，正文直接读写 Obsidian 目录
const VAULT_ID_PREFIX = "vault:";
function isVaultId(id) {
    return String(id || "").startsWith(VAULT_ID_PREFIX);
}
function vaultNoteId(id) {
    return String(id || "").slice(VAULT_ID_PREFIX.length);
}
function vaultPathUrl(relativePath) {
    return "obsidian://open?vault=" + encodeURIComponent("知识库") + "&file=" + encodeURIComponent(String(relativePath || "").replace(/\.md$/i, ""));
}

let currentContentAccount = localStorage.getItem("content_account_key") === "vlog" ? "vlog" : "main";
if (["main", "vlog", "ad"].includes(localStorage.getItem("script_account_key"))) {
    currentScriptAccount = localStorage.getItem("script_account_key");
} else {
    currentScriptAccount = currentContentAccount;
}
let personalMemoryState = {
    items: [], view: localStorage.getItem("personal_library_view") === "memo" ? "memo" : "about",
    scope: "all", query: "", searchTimer: null, revealed: new Map(), revealTimers: new Map(),
};

const PAGE_TITLES = {
    dashboard: "总览",
    archive: "我的",
    hotspots: "信源",
    topics: "选题库",
    inspirations: "灵感",
    scripts: "脚本库",
    weekly: "工作计划",
    journal: "日记",
    health: "身心",
};

// ========== 初始化 ==========
document.addEventListener("DOMContentLoaded", () => {
    updateDate();
    updateContentAccountUI();
    setupMoodSelector();
    initAssistantVoices();
    initXiaoliPet();
    if ("speechSynthesis" in window) window.speechSynthesis.onvoiceschanged = initAssistantVoices;
    document.addEventListener("keydown", event => {
        if (event.key !== "Escape") return;
        closeAssistantChat();
        hideCalendarHoverCard();
        closeMemoryForm();
        closeMemoryPreview();
        closeTopicDetail();
        toggleScriptTrash(false);
    });
    const requestedTab = new URLSearchParams(window.location.search).get("tab");
    if (requestedTab && PAGE_TITLES[requestedTab]) switchTab(requestedTab);
    runInitialLoad();
});

async function runInitialLoad() {
    try {
        const response = await api("/api/settings");
        loadedSettings = response.config || null;
        updateDate();
    } catch (error) {
        console.warn("公开配置读取失败：", error);
    }
    const loaders = [
        ["总览", loadDashboard],
        ["助手", loadAssistant],
        ["灵感", loadInspirations],
        ["选题", loadTopics],
        ["我的", loadPersonalMemories],
    ];
    const results = await Promise.allSettled(loaders.map(([, loader]) => loader()));
    results.forEach((result, index) => {
        if (result.status === "rejected") {
            console.error(`${loaders[index][0]}加载失败:`, result.reason);
        }
    });
}

function updateDate() {
    const now = new Date();
    const days = ["日", "一", "二", "三", "四", "五", "六"];
    document.getElementById("todayDate").textContent =
        `${now.getFullYear()}年${now.getMonth() + 1}月${now.getDate()}日 周${days[now.getDay()]}`;
    const greeting = document.getElementById("dashboardGreeting");
    if (greeting) {
        const hour = now.getHours();
        const period = hour < 6 ? "夜深了" : hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";
        greeting.textContent = `${period}，${loadedSettings?.profile?.display_name || "新用户"}`;
    }
}

// ========== Tab 切换 ==========
function switchTab(tabName) {
    if (tabName !== "weekly") hideCalendarHoverCard();
    document.querySelectorAll(".tab-content").forEach(el => el.classList.remove("active"));
    document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("active"));
    document.getElementById("tab-" + tabName).classList.add("active");
    document.querySelector(`.nav-item[data-tab="${tabName}"]`)?.classList.add("active");
    document.getElementById("pageTitle").textContent = PAGE_TITLES[tabName] || "";
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    // 切换时加载对应数据
    if (tabName === "inspirations") {
        if (ideaUnifiedSource === "douyin") loadDouyinInspirations();
        else loadInspirations();
    }
    if (tabName === "hotspots") enterHotspotRadar();
    if (tabName === "archive") loadPersonalMemories();
    if (tabName === "scripts") loadScripts();
    if (tabName === "weekly") loadWeeklyPlans();
    if (tabName === "journal") loadJournal();
    if (tabName === "health") loadHealth();
}

// ========== API 请求 ==========
async function api(url, method = "GET", body = null, timeoutMs = 30000) {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    // AbortController 超时保护
    const controller = new AbortController();
    opts.signal = controller.signal;
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const res = await fetch(url, opts);
        clearTimeout(timer);
        if (!res.ok) {
            // HTTP 状态码异常，尝试读错误信息
            let errMsg = `服务器错误 (${res.status})`;
            try { const errData = await res.json(); errMsg = errData.error || errMsg; } catch(_) {}
            throw new Error(errMsg);
        }
        const text = await res.text();
        try {
            return JSON.parse(text);
        } catch(_) {
            throw new Error("服务器返回了非JSON内容");
        }
    } catch (e) {
        clearTimeout(timer);
        if (e.name === "AbortError") throw new Error("请求超时，请检查网络连接");
        if (e.message === "Failed to fetch") throw new Error("网络连接失败，请检查服务器是否在运行");
        throw e;
    }
}

function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function safeExternalUrl(value) {
    if (!value) return "";
    try {
        const parsed = new URL(String(value), window.location.origin);
        return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : "";
    } catch (_) {
        return "";
    }
}

function localDateString(date = new Date()) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}

// ========== 仪表盘 ==========
async function loadDashboard() {
    const data = await api("/api/dashboard");
    document.getElementById("streakNum").textContent = data.streak;
    const dashboardStreak = document.getElementById("dashboardStreak");
    if (dashboardStreak) dashboardStreak.textContent = data.streak;

    // 打卡按钮状态
    if (data.today_checkin) {
        if (data.today_checkin.check_in_time) {
            const btn = document.getElementById("btnCheckIn");
            btn.classList.add("done");
            btn.textContent = "上班 " + data.today_checkin.check_in_time.slice(11, 16);
        }
        if (data.today_checkin.check_out_time) {
            const btn = document.getElementById("btnCheckOut");
            btn.classList.add("done");
            btn.textContent = "下班 " + data.today_checkin.check_out_time.slice(11, 16);
        }
    }
    const checkinSummary = document.getElementById("dashboardCheckinSummary");
    if (checkinSummary) {
        const checkin = data.today_checkin || {};
        checkinSummary.textContent = checkin.check_out_time
            ? `今天 ${checkin.check_in_time.slice(11, 16)}–${checkin.check_out_time.slice(11, 16)}`
            : checkin.check_in_time
                ? `已于 ${checkin.check_in_time.slice(11, 16)} 开始`
                : "今天还未打卡";
    }

    await Promise.all([loadDashboardCheckWall(), loadDashboardCreatorCards()]);
}

async function loadDashboardCheckWall() {
    const response = await api("/api/dashboard/check-wall");
    const container = document.getElementById("dashboardCheckWall");
    const progress = document.getElementById("dashboardCheckProgress");
    if (!container) return;
    const items = response.items || [];
    if (progress) progress.textContent = `${response.done || 0} / ${items.length}`;
    container.innerHTML = items.map(item => `
        <button class="dashboard-check-item ${item.done ? "done" : ""}" onclick="toggleDashboardActivity('${item.key}', ${item.done ? "false" : "true"})">
            <span>${item.done ? "✓" : ""}</span><strong>${escapeHtml(item.label)}</strong>
            ${item.source === "auto" && item.done ? `<small>自动</small>` : ""}
        </button>`).join("");
}

async function toggleDashboardActivity(activity, done) {
    await api("/api/dashboard/check-wall", "POST", {activity, done, source: "manual"});
    loadDashboardCheckWall();
}

async function markDashboardActivity(activity) {
    try {
        await api("/api/dashboard/check-wall", "POST", {activity, done: true, source: "auto"});
        if (document.getElementById("tab-dashboard")?.classList.contains("active")) loadDashboardCheckWall();
    } catch (_) {}
}

async function loadDashboardCreatorCards() {
    const response = await api("/api/dashboard/creator-cards");
    const ideasEl = document.getElementById("dashboardRecallIdeas");
    const newsEl = document.getElementById("dashboardDualHotspots");
    const ideas = response.ideas || [];
    if (ideasEl) ideasEl.innerHTML = ideas.length ? ideas.map(item => `
        <button onclick="switchTab('topics')"><span>点子</span><strong>${escapeHtml(item.title || "未命名")}</strong><small>${escapeHtml((item.created_at || "").slice(5, 10))}</small></button>
    `).join("") : `<div class="editorial-empty">选题库里还没有待回想的点子。</div>`;
    const labels = {overall: "全网热点", relevant: "与你相关"};
    if (newsEl) newsEl.innerHTML = (response.news || []).length ? response.news.map(item => `
        <article><span>${labels[item.kind] || "热点"}</span><h4 onclick="openDashboardHotspot('${item.event_id}')">${escapeHtml(item.title || "")}</h4><p>${escapeHtml(item.reason || "")}</p>
        ${safeExternalUrl(item.url) ? `<a href="${escapeHtml(safeExternalUrl(item.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.source || "原文")} ↗</a>` : ""}</article>
    `).join("") : `<div class="editorial-empty">暂无新闻缓存，进入“信源”刷新。</div>`;
}

async function openDashboardHotspot(eventId) {
    switchTab("hotspots");
    await loadHotspots();
    openHotspotDetail(eventId);
}

// ========== 个人账号矩阵 ==========
function formatMetric(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return "—";
    if (number >= 100000000) return `${(number / 100000000).toFixed(number >= 1000000000 ? 0 : 1).replace(/\.0$/, "")}亿`;
    if (number >= 10000) return `${(number / 10000).toFixed(number >= 1000000 ? 0 : 1).replace(/\.0$/, "")}万`;
    return number.toLocaleString("zh-CN");
}

function accountInitial(account) {
    const name = account.nickname || account.account_role || "我";
    return escapeHtml(name.slice(0, 1));
}

function renderAccountAvatar(account, className = "account-avatar") {
    return account.avatar_url
        ? `<span class="${className}"><img src="${escapeHtml(account.avatar_url)}?v=${encodeURIComponent(account.updated_at || "1")}" alt="${escapeHtml(account.nickname)}的头像"></span>`
        : `<span class="${className} account-avatar-fallback">${accountInitial(account)}</span>`;
}

function renderFollowerSparkline(trend) {
    const values = (trend || []).map(item => Number(item.followers || 0));
    if (values.length < 2) return `<div class="sparkline-empty">再更新 1 次后显示趋势</div>`;
    const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
    const points = values.map((value, index) => {
        const x = values.length === 1 ? 50 : index * (100 / (values.length - 1));
        const y = 34 - ((value - min) / range) * 27;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    return `<svg class="account-sparkline" viewBox="0 0 100 38" preserveAspectRatio="none" aria-label="粉丝趋势">
        <defs><linearGradient id="sparkFill-${Math.random().toString(36).slice(2)}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity=".22"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs>
        <polyline points="${points}" fill="none" vector-effect="non-scaling-stroke"/>
    </svg>`;
}

function renderAudience(audience) {
    const chips = Object.entries(audience || {}).filter(([key, value]) => !key.startsWith("_") && String(value || "").trim()).slice(0, 4);
    if (!chips.length) return `<span class="audience-empty">画像待补充</span>`;
    return chips.map(([, value]) => `<span>${escapeHtml(value)}</span>`).join("");
}

function accountMetricDisplay(metric, key) {
    const value = Number(metric?.[key] || 0);
    if (value > 0) return formatMetric(value);
    const publicLabel = metric?.audience?.[`_${key}_display`];
    return publicLabel ? escapeHtml(publicLabel) : "—";
}

function renderLatestWorks(works) {
    if (!works?.length) return `<div class="work-empty">录入第一条作品后，这里会显示表现对比</div>`;
    return works.map(work => {
        const engagement = Number(work.likes || 0) + Number(work.comments || 0) + Number(work.shares || 0) + Number(work.saves || 0);
        return `<div class="matrix-work-row">
            <div class="matrix-work-copy"><strong>${escapeHtml(work.title)}</strong><span>${escapeHtml((work.published_at || "").slice(5, 10) || "日期待补充")}</span></div>
            <div class="matrix-work-metrics"><span><b>${formatMetric(work.views)}</b> 播放</span><span><b>${formatMetric(engagement)}</b> 互动</span></div>
        </div>`;
    }).join("");
}

function renderAccountCard(account) {
    const metric = account.latest_metric;
    const delta = Number(metric?.followers_delta || 0);
    const hasGrowthBaseline = (account.trend || []).length > 1;
    const deltaLabel = hasGrowthBaseline ? `${delta >= 0 ? "+" : ""}${formatMetric(delta)}` : "—";
    const platformName = account.platform === "xiaohongshu" ? "小红书" : "抖音";
    const recommendation = account.recommendation || {};
    const profileLink = account.profile_url
        ? `<a class="account-profile-link" href="${escapeHtml(account.profile_url)}" target="_blank" rel="noopener noreferrer">打开主页 <span>↗</span></a>`
        : `<button class="account-profile-link is-empty" onclick="showSettings('accounts')">补充主页</button>`;
    return `<article class="account-pulse-card platform-${escapeHtml(account.platform)} ${metric ? "has-data" : "needs-data"}">
        <div class="account-card-topline"><span></span><em>${escapeHtml(account.account_role)}</em></div>
        <header class="account-card-header">
            ${renderAccountAvatar(account)}
            <div class="account-card-name"><span class="platform-pill">${platformName} · ${escapeHtml(account.account_role)}</span><h3>${escapeHtml(account.nickname || account.account_role)}</h3><p>${escapeHtml(account.positioning || "账号定位待配置")}</p></div>
            ${profileLink}
            <button class="account-more-btn" onclick="showSettings('accounts')" title="编辑账号">•••</button>
        </header>
        <div class="account-card-dashboard">
            <div class="account-card-data-panel">
                <div class="account-primary-metric">
                    <div><span>粉丝总量</span><strong>${accountMetricDisplay(metric, "followers")}</strong></div>
                    <div class="account-delta ${hasGrowthBaseline && delta > 0 ? "positive" : hasGrowthBaseline && delta < 0 ? "negative" : ""}"><span>本次增长</span><strong>${deltaLabel}</strong></div>
                    <div class="sparkline-wrap">${renderFollowerSparkline(account.trend)}</div>
                </div>
                <div class="account-secondary-metrics">
                    <span><b>${accountMetricDisplay(metric, "views_7d")}</b>近 7 天播放</span>
                    <span><b>${accountMetricDisplay(metric, "works_count")}</b>累计作品</span>
                    <span><b>${accountMetricDisplay(metric, "total_likes")}</b>获赞/收藏</span>
                </div>
                <section class="audience-snapshot"><div class="account-section-label"><span>核心受众</span><small>${metric ? `更新 ${escapeHtml(metric.snapshot_date.slice(5))}` : "待同步"}</small></div><div class="audience-chips">${renderAudience(metric?.audience)}</div></section>
            </div>
            <div class="account-card-insight-panel">
                <section class="latest-work-snapshot"><div class="account-section-label"><span>最新作品</span><small>${account.latest_works?.length || 0} 条</small></div>${renderLatestWorks(account.latest_works)}</section>
                <section class="account-next-move">
                    <div class="recommendation-kicker"><span>AI NEXT MOVE</span><button onclick="generateAccountRecommendation('${escapeHtml(account.id)}', this)">重新生成</button></div>
                    <h4>${escapeHtml(recommendation.title || "选题建议待生成")}</h4>
                    <p>${escapeHtml(recommendation.angle || recommendation.reason || "补充账号和作品数据后生成建议。")}</p>
                    <div class="recommendation-meta"><span>${escapeHtml(recommendation.content_format || "形式待定")}</span><em>${recommendation.generated ? "基于本地数据" : "策略基线"}</em></div>
                </section>
            </div>
        </div>
        <footer class="account-card-footer"><span class="status-dot ${metric ? "online" : ""}"></span>${metric ? `数据更新于 ${escapeHtml(account.last_synced_at || metric.snapshot_date)}` : "已绑定主页，等待安全同步数据"}<button onclick="showSettings('accounts')">管理账号 →</button></footer>
    </article>`;
}

async function loadAccountMatrix() {
    const grid = document.getElementById("accountCardGrid");
    if (!grid) return;
    try {
        const response = await api("/api/accounts");
        creatorAccounts = response.accounts || [];
        const summary = response.summary || {};
        document.getElementById("matrixTotalFollowers").textContent = Number(summary.total_followers || 0) > 0 ? formatMetric(summary.total_followers) : "—";
        const delta = Number(summary.followers_delta || 0);
        const hasGrowthBaseline = creatorAccounts.some(item => (item.trend || []).length > 1);
        document.getElementById("matrixFollowerDelta").textContent = hasGrowthBaseline ? `${delta >= 0 ? "+" : ""}${formatMetric(delta)}` : "—";
        document.getElementById("matrixFollowerDelta").classList.toggle("positive", hasGrowthBaseline && delta > 0);
        document.getElementById("matrixDataStatus").textContent = `${creatorAccounts.filter(item => item.latest_metric).length} / ${creatorAccounts.length || 3}`;
        const profile = response.profile || {};
        document.getElementById("accountMatrixTitle").textContent = `${profile.display_name || "我"}的内容矩阵`;
        document.getElementById("matrixPositioning").textContent = profile.content_direction || "三个账号，一个清晰的个人品牌增长视图";
        const portraitAccount = creatorAccounts.find(item => item.avatar_url) || creatorAccounts[0];
        if (portraitAccount) document.getElementById("matrixPortrait").innerHTML = portraitAccount.avatar_url
            ? `<img src="${escapeHtml(portraitAccount.avatar_url)}?v=${encodeURIComponent(portraitAccount.updated_at || "1")}" alt="个人品牌头像">`
            : `<span>${escapeHtml((profile.display_name || "本").slice(0, 1))}</span>`;
        grid.innerHTML = creatorAccounts.map(renderAccountCard).join("");
        const withData = creatorAccounts.filter(item => item.latest_metric);
        const brief = document.getElementById("accountMatrixBrief");
        const withGrowthBaseline = withData.filter(item => (item.trend || []).length > 1);
        if (!withData.length) {
            brief.innerHTML = `<span class="brief-signal"></span><p><strong>主页已经校准：</strong>等待首次安全同步后，这里会开始整理真实增长趋势。</p>`;
        } else if (!withGrowthBaseline.length) {
            brief.innerHTML = `<span class="brief-signal"></span><p><strong>首次数据基线已建立：</strong>${escapeHtml(withData.map(item => item.nickname).join("、"))} 已有公开数据；获得第二次有效数据后再计算增长，避免把未知值误报为 0。</p>`;
        } else {
            const leader = [...withGrowthBaseline].sort((a, b) => Number(b.latest_metric.followers_delta || 0) - Number(a.latest_metric.followers_delta || 0))[0];
            const missing = creatorAccounts.length - withData.length;
            brief.innerHTML = `<span class="brief-signal"></span><p><strong>今日矩阵判断：</strong>${escapeHtml(leader.nickname)} 的本次增长领先${missing ? `；还有 ${missing} 个账号需要补充数据` : "，三个账号的数据基线已经建立"}。</p>`;
        }
    } catch (error) {
        grid.innerHTML = `<div class="matrix-error"><strong>账号矩阵暂时无法加载</strong><span>${escapeHtml(error.message)}</span><button onclick="loadAccountMatrix()">重新加载</button></div>`;
    }
}

async function refreshAccountMatrix(button) {
    if (!button || button.disabled) return;
    const brief = document.getElementById("accountMatrixBrief");
    button.disabled = true;
    button.classList.add("is-syncing");
    button.setAttribute("aria-label", "正在同步账号数据");
    if (brief) brief.innerHTML = `<span class="brief-signal"></span><p><strong>正在同步三个公开主页：</strong>读取粉丝、作品数、累计获赞和公开画像，请稍候…</p>`;
    try {
        const result = await api("/api/accounts/refresh", "POST", {}, 65000);
        await loadAccountMatrix();
        if (result.partial && brief) {
            const failed = (result.results || []).filter(item => !item.ok);
            brief.innerHTML = `<span class="brief-signal warning"></span><p><strong>已更新 ${result.updated} / ${result.total} 个账号：</strong>${failed.length} 个公开主页暂时无法访问，已继续显示最近一次本地数据。</p>`;
        }
    } catch (error) {
        if (brief) brief.innerHTML = `<span class="brief-signal warning"></span><p><strong>本次同步未完成：</strong>${escapeHtml(error.message)}。本地已有数据不会丢失，可以稍后重试。</p>`;
    } finally {
        button.disabled = false;
        button.classList.remove("is-syncing");
        button.setAttribute("aria-label", "重新同步账号数据");
    }
}

async function generateAccountRecommendation(accountId, button) {
    const original = button.textContent;
    button.disabled = true; button.textContent = "生成中…";
    try {
        await api(`/api/accounts/${encodeURIComponent(accountId)}/recommend`, "POST", {}, 130000);
        await loadAccountMatrix();
    } catch (error) {
        alert("选题生成失败：" + error.message);
    } finally {
        button.disabled = false; button.textContent = original;
    }
}

// ========== 打卡 ==========
async function doCheckIn() {
    await api("/api/checkin", "POST", { action: "in" });
    const btn = document.getElementById("btnCheckIn");
    btn.classList.add("done");
    const now = new Date();
    btn.textContent = "上班 " + String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
    loadDashboard();
}

async function doCheckOut() {
    await api("/api/checkin", "POST", { action: "out" });
    const btn = document.getElementById("btnCheckOut");
    btn.classList.add("done");
    const now = new Date();
    btn.textContent = "下班 " + String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
    loadDashboard();
}

// ========== 热点 ==========
let todayRadar = {happening: [], deep_dive: [], opportunities: [], sources: [], summary: {}};
let hotspotView = "happening";
let hotspotTranslations = {};
let currentHotspotDetailId = "";

async function loadHotspots() {
    const response = await api("/api/hotspots/today");
    todayRadar = response.data || {happening: [], deep_dive: [], opportunities: [], sources: [], summary: {}};
    document.getElementById("hotspotTime").textContent = "更新于 " + (todayRadar.date || "尚未更新");
    const summary = todayRadar.summary || {};
    document.getElementById("todayOverview").innerHTML = `
        <div><strong>${summary.events || 0}</strong><span>个事件</span></div>
        <div><strong>${summary.cross_checked || 0}</strong><span>已交叉确认</span></div>
        <div><strong>${summary.healthy || 0}</strong><span>个来源在线</span></div>`;
    renderHotspotView();
    loadHotspotTranslations();
}

async function loadHotspotTranslations() {
    const titles = [];
    const events = [...(todayRadar.happening || []), ...(todayRadar.deep_dive || []), ...(todayRadar.opportunities || [])];
    // 先翻译卡片主标题，保证用户看到的每条英文新闻都有中文对照；
    // 还有余量时，再补充详情页里的来源标题。
    for (const event of events) {
        const title = event.title;
        if (title && !/[\u4e00-\u9fff]/.test(title) && !titles.includes(title)) titles.push(title);
    }
    for (const event of events) {
        for (const article of (event.articles || [])) {
            const title = article.title;
            if (title && !/[\u4e00-\u9fff]/.test(title) && !titles.includes(title)) titles.push(title);
        }
    }
    if (!titles.length) return;
    try {
        const result = await api("/api/hotspots/translations", "POST", {titles: titles.slice(0, 30)}, 130000);
        hotspotTranslations = {...hotspotTranslations, ...(result.translations || {})};
        renderHotspotView();
        if (currentHotspotDetailId) renderHotspotDetail(currentHotspotDetailId);
    } catch (_) {}
}

let hotspotEntered = false;
async function enterHotspotRadar() {
    await loadHotspots();
    markDashboardActivity("news");
    if (hotspotEntered) return;
    hotspotEntered = true;
    try {
        const settings = await api("/api/settings");
        if (settings.config?.news?.auto_refresh) refreshHotspots(true);
    } catch (_) {}
}

function setHotspotView(view) {
    hotspotView = view;
    document.querySelectorAll("[data-hotspot-view]").forEach(button => {
        const active = button.dataset.hotspotView === view;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
    });
    renderHotspotView();
}

function getHotspotEvent(eventId) {
    return [...(todayRadar.happening || []), ...(todayRadar.deep_dive || []), ...(todayRadar.opportunities || [])]
        .find(item => item.id === eventId);
}

function eventSourceLinks(event) {
    return (event.articles || []).slice(0, 4).map(article => {
        const url = safeExternalUrl(article.url);
        return url
            ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(article.source)} ↗</a>`
            : `<span>${escapeHtml(article.source)}</span>`;
    }).join("");
}

function eventEvidenceLabel(event) {
    const evidence = Number(event.evidence_count || 0);
    const trends = Number(event.trend_platform_count || 0);
    if (evidence >= 2) return `${evidence} 个独立证据源`;
    if (evidence === 1 && trends > 0) return `1 个证据源 · ${trends} 个趋势平台`;
    if (evidence === 1) return "当前仅有一个证据源";
    if (trends >= 2) return `${trends} 个平台出现 · 尚未核验`;
    return "趋势信号 · 尚未核验";
}

function renderEventCard(event, index, mode) {
    const tracked = event.feedback === "track";
    const sourceLabel = eventEvidenceLabel(event);
    const opportunity = mode === "opportunities";
    const translatedTitle = hotspotTranslations[event.title] || "";
    return `<article class="today-event-card ${opportunity ? "is-opportunity" : ""} ${translatedTitle ? "has-translation" : ""}">
        <div class="today-event-rank">${String(index + 1).padStart(2, "0")}</div>
        <div class="today-event-body">
            <div class="today-event-meta">
                <span class="importance ${escapeHtml(event.importance_label || "关注")}">${escapeHtml(event.importance_label || "关注")}</span>
                <span>${escapeHtml(event.category || "综合")}</span>
                <span>${escapeHtml(event.confidence || "单一来源")}</span>
                ${tracked ? `<span class="is-tracked">已关注</span>` : ""}
            </div>
            ${translatedTitle ? `<div class="event-translation event-title-open" onclick="openHotspotDetail('${event.id}')">${escapeHtml(translatedTitle)}</div>` : ""}
            <h3 class="event-title-open ${translatedTitle ? "is-original" : ""}" onclick="openHotspotDetail('${event.id}')" title="打开事件详情">${escapeHtml(event.title)}</h3>
            ${event.published_at ? `<time class="event-published-time">${escapeHtml(event.published_at.slice(0, 10))}</time>` : ""}
            <p>${escapeHtml(opportunity ? event.creator_reason : event.why_important)}</p>
            <div class="event-sources"><strong>${sourceLabel}</strong>${eventSourceLinks(event)}</div>
        </div>
        <div class="today-event-actions">
            ${opportunity ? `<button class="btn-primary-soft" onclick="saveHotspotIdea('${event.id}')">存为点子</button>
                <button onclick="ideaFromHotspotById('${event.id}')">进入选题管理</button>`
                : `<button class="${tracked ? "is-following" : ""}" onclick="setHotspotFeedback('${event.id}', '${tracked ? "clear" : "track"}')">${tracked ? "已关注 · 取消" : "关注进展"}</button>`}
        </div>
    </article>`;
}

function renderHotspotView() {
    const container = document.getElementById("todayContent");
    if (!container) return;
    if (hotspotView === "sources") {
        const order = ["权威基石", "深度解读", "多元视角", "趋势信号"];
        container.innerHTML = `<div class="source-method-note"><strong>三层信源 + 一层趋势信号</strong><span>关键事件至少由两个独立来源交叉确认；“参考核验”来源只用于人工复核，不会自动抓取。</span></div>` +
            order.map(layer => {
                const sources = (todayRadar.sources || []).filter(item => item.layer === layer);
                return `<section class="source-layer"><header><h3>${layer}</h3><span>${sources.length} 个来源</span></header><div class="source-cards">${sources.map(source => {
                    const state = source.mode === "reference" ? "参考核验" : source.health === "ok" ? `${source.items} 条` : source.health === "empty" ? "本次无新内容" : source.health === "error" ? "暂时不可用" : "未启用";
                    const home = safeExternalUrl(source.homepage);
                    return `<article class="source-card"><div><strong>${escapeHtml(source.name)}</strong><span>${escapeHtml(source.region || "")} · ${escapeHtml(source.category || "")}</span></div><div class="source-card-state ${escapeHtml(source.health || "")}">${state}</div>${home ? `<a href="${escapeHtml(home)}" target="_blank" rel="noopener">访问 ↗</a>` : ""}</article>`;
                }).join("")}</div></section>`;
            }).join("");
        return;
    }
    const events = todayRadar[hotspotView] || [];
    if (!events.length) {
        const message = hotspotView === "opportunities" ? "目前还没有足够适合你的内容。" : "还没有新闻缓存，点击“刷新信源”开始整理。";
        container.innerHTML = `<div class="empty-state"><h3>这里暂时是空的</h3><p>${message}</p></div>`;
        return;
    }
    const intro = hotspotView === "deep_dive"
        ? `<div class="view-explainer"><strong>从“正在发生”中选出的重点话题</strong><span>按影响范围和来源质量筛选；点开后可基于当前报道生成多源深度解读。</span></div>`
        : hotspotView === "opportunities"
            ? `<div class="view-explainer"><strong>适合转化成内容的新闻</strong><span>不会自动存入知识库，由你决定是否转成点子。</span></div>`
            : "";
    container.innerHTML = intro + `<div class="today-event-list">${events.map((event, index) => renderEventCard(event, index, hotspotView)).join("")}</div>`;
}

async function setHotspotFeedback(eventId, action) {
    const event = getHotspotEvent(eventId);
    if (!event) return;
    await api("/api/hotspots/feedback", "POST", {
        event_id: event.id, title: event.title, action,
        source_urls: (event.articles || []).map(item => item.url).filter(Boolean),
    });
    await loadHotspots();
    if (currentHotspotDetailId) renderHotspotDetail(currentHotspotDetailId);
}

function formatHotspotExplanation(text) {
    const raw = String(text || "").trim();
    if (!raw) return "";
    const sections = [];
    let current = {title: "解读", lines: []};
    raw.split(/\r?\n/).forEach(line => {
        const heading = line.match(/^#{2,3}\s+(.+)$/);
        if (heading) {
            if (current.lines.some(item => item.trim())) sections.push(current);
            current = {title: heading[1].replace(/^\d+[.、]\s*/, ""), lines: []};
        } else current.lines.push(line);
    });
    if (current.lines.some(item => item.trim())) sections.push(current);
    const inline = value => escapeHtml(value).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    return `<div class="analysis-sections">${sections.map(section => {
        const blocks = [];
        let list = [];
        const flushList = () => { if (list.length) { blocks.push(`<ul>${list.map(item => `<li>${inline(item)}</li>`).join("")}</ul>`); list = []; } };
        section.lines.forEach(line => {
            const trimmed = line.trim();
            if (/^[-*]\s+/.test(trimmed)) list.push(trimmed.replace(/^[-*]\s+/, ""));
            else if (trimmed) { flushList(); blocks.push(`<p>${inline(trimmed)}</p>`); }
        });
        flushList();
        return `<section><h4>${inline(section.title)}</h4>${blocks.join("")}</section>`;
    }).join("")}</div>`;
}

async function openHotspotDetail(eventId) {
    currentHotspotDetailId = eventId;
    document.getElementById("hotspotDetailModal").style.display = "flex";
    document.getElementById("hotspotDetailContent").innerHTML = `<div class="detail-loading">正在整理事件来源…</div>`;
    await renderHotspotDetail(eventId);
}

function closeHotspotDetail(event) {
    if (event && event.target !== event.currentTarget) return;
    document.getElementById("hotspotDetailModal").style.display = "none";
    currentHotspotDetailId = "";
}

async function renderHotspotDetail(eventId) {
    const container = document.getElementById("hotspotDetailContent");
    if (!container || !currentHotspotDetailId) return;
    try {
        const response = await api(`/api/hotspots/event/${encodeURIComponent(eventId)}`);
        const event = response.event;
        const translated = hotspotTranslations[event.title] || "";
        const tracked = event.feedback === "track";
        const articles = (event.articles || []).map(article => {
            const url = safeExternalUrl(article.url);
            const articleTranslation = hotspotTranslations[article.title] || "";
            return `<article class="detail-source-item">
                <div><span class="source-layer-tag">${escapeHtml(article.layer || "")}</span><strong>${escapeHtml(article.source || "")}</strong></div>
                <p>${escapeHtml(article.title || "")}</p>
                ${articleTranslation ? `<p class="detail-source-translation"><span>中文</span>${escapeHtml(articleTranslation)}</p>` : ""}
                ${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">打开原文 ↗</a>` : ""}
            </article>`;
        }).join("");
        const history = (response.history || []).map(item => `<li><strong>${escapeHtml(item.created_at || "")}</strong><span>${escapeHtml(item.confidence || "")} · ${item.evidence_count || 0} 个证据源 · ${item.platform_count || 0} 个平台</span></li>`).join("");
        const explanation = response.explanation?.result || "";
        container.innerHTML = `
            <div class="detail-eyebrow">EVENT BRIEF · ${escapeHtml(event.importance_label || "关注")}</div>
            ${translated ? `<div class="detail-main-translation"><strong>${escapeHtml(translated)}</strong></div>` : ""}
            <h2 class="${translated ? "detail-original-title" : ""}">${escapeHtml(event.title)}</h2>
            <div class="detail-status-grid">
                <div><strong>${escapeHtml(event.confidence || "")}</strong><span>核验状态</span></div>
                <div><strong>${event.evidence_count || 0}</strong><span>独立证据源</span></div>
                <div><strong>${event.platform_count || 0}</strong><span>出现平台</span></div>
            </div>
            <div class="detail-verification-note"><strong>当前判断</strong><span>${escapeHtml(event.why_important || "")}</span></div>
            <div class="detail-actions">
                <button class="btn-primary ${tracked ? "is-following" : ""}" onclick="setHotspotFeedback('${event.id}', '${tracked ? "clear" : "track"}')">${tracked ? "已关注进展 · 点击取消" : "关注进展"}</button>
                <button class="btn-ai" onclick="explainHotspotEvent('${event.id}')">✦ 生成深度解读</button>
                <button class="btn-ghost" onclick="saveHotspotIdea('${event.id}')">存为点子</button>
            </div>
            <section class="detail-section"><h3>消息来源</h3><div class="detail-source-list">${articles}</div></section>
            <section class="detail-section"><h3>深度解读</h3><div id="hotspotEventExplanation" class="detail-explanation">${explanation ? formatHotspotExplanation(explanation) : `<p>基于当前新闻原文与多方信源，生成内容概括、深层原因、媒体侧重点和关注价值。</p>`}</div></section>
            ${tracked ? `<section class="detail-section"><h3>关注记录</h3><p class="detail-section-hint">系统会在每次新闻刷新时重新核验；只有来源、证据数量或状态发生变化才新增记录。</p><ol class="tracking-history">${history || "<li><span>已建立关注，等待下一次刷新比较。</span></li>"}</ol></section>` : ""}`;
    } catch (error) {
        container.innerHTML = `<div class="empty-state"><h3>事件详情暂时无法打开</h3><p>${escapeHtml(error.message)}</p></div>`;
    }
}

async function explainHotspotEvent(eventId) {
    const target = document.getElementById("hotspotEventExplanation");
    if (!target) return;
    target.innerHTML = `<p>正在核对来源并生成解读…</p>`;
    try {
        const result = await api(`/api/hotspots/event/${encodeURIComponent(eventId)}/explain`, "POST", {}, 130000);
        target.innerHTML = `${formatHotspotExplanation(result.result || "")}<small>${result.cached ? "使用当前来源的已有解读" : "根据当前来源刚刚生成"} · ${escapeHtml(result.created_at || "")}</small>`;
    } catch (error) {
        target.innerHTML = `<p>暂时无法生成：${escapeHtml(error.message)}</p>`;
    }
}

async function saveHotspotIdea(eventId) {
    const event = getHotspotEvent(eventId);
    if (!event) return;
    try {
        const result = await api("/api/hotspots/save-idea", "POST", {
            event_id: event.id, title: event.title, reason: event.creator_reason,
            sources: (event.articles || []).map(item => ({source: item.source, url: item.url})),
        });
        document.getElementById("hotspotRefreshStatus").textContent = `已存入 Obsidian：${result.relative_path}`;
    } catch (error) {
        document.getElementById("hotspotRefreshStatus").textContent = `未能存入 Obsidian：${error.message}`;
    }
}

async function refreshHotspots(silent = false) {
    const btn = document.getElementById("btnHotspotRefresh");
    const status = document.getElementById("hotspotRefreshStatus");
    btn.disabled = true; status.textContent = "正在从各来源更新…";
    try {
        await api("/api/hotspots/refresh", "POST", {});
        for (let i = 0; i < 40; i++) {
            await new Promise(resolve => setTimeout(resolve, 1500));
            const state = await api("/api/hotspots/refresh-status");
            if (state.status === "done") {
                if (state.result?.success) {
                    const s = state.result.summary || {};
                    status.textContent = `更新完成：${s.healthy || 0} 个来源，${s.items || 0} 条`;
                    await loadHotspots();
                } else status.textContent = "更新失败，已保留本地缓存：" + (state.result?.message || "未知错误");
                return;
            }
        }
        status.textContent = "更新仍在后台进行，可稍后再看";
    } catch (e) {
        if (!silent) status.textContent = "更新失败，已继续使用本地缓存：" + e.message;
    } finally { btn.disabled = false; }
}

function ideaFromHotspotById(eventId) {
    const event = getHotspotEvent(eventId);
    if (!event) return;
    switchTab("topics");
    document.getElementById("topicForm").style.display = "flex";
    document.getElementById("topicTitle").value = event.title;
    document.getElementById("topicSource").value = `信源 · ${(event.articles || []).map(item => item.source).slice(0, 3).join(" / ")}`;
    document.getElementById("topicAngle").value = event.creator_reason || "";
    document.getElementById("topicAngle").focus();
}

// ========== 选题库 ==========
function updateContentAccountUI() {
    const meta = CONTENT_ACCOUNTS[currentContentAccount];
    document.querySelectorAll("[data-content-account]").forEach(button => {
        button.classList.toggle("active", button.dataset.contentAccount === currentContentAccount);
        button.setAttribute("aria-pressed", button.dataset.contentAccount === currentContentAccount ? "true" : "false");
    });
    const guide = document.getElementById("topicAccountGuide");
    if (guide) {
        guide.className = `account-lane-guide ${currentContentAccount}`;
        guide.innerHTML = `<span>${escapeHtml(meta.label)} · 选题</span><strong>${escapeHtml(meta.topicGuide)}</strong>`;
    }
    const formLabel = document.getElementById("topicFormAccountLabel");
    if (formLabel) {
        formLabel.textContent = `${meta.label} · 选题`;
        formLabel.className = `content-account-badge ${currentContentAccount}`;
    }
    const formHint = document.getElementById("topicFormHint");
    if (formHint) formHint.textContent = meta.topicHint;
    const vlogPrompt = document.getElementById("vlogTopicPrompt");
    if (vlogPrompt) vlogPrompt.style.display = currentContentAccount === "vlog" ? "flex" : "none";
    const titleInput = document.getElementById("topicTitle");
    const sourceInput = document.getElementById("topicSource");
    const angleInput = document.getElementById("topicAngle");
    if (titleInput) titleInput.placeholder = currentContentAccount === "vlog" ? "今天的创作过程，最值得记录的是什么？" : "一句话说清选题";
    if (sourceInput) sourceInput.placeholder = currentContentAccount === "vlog" ? "发生日期 / 创作环节（例如：第一次接商单）" : "来源（哪个热点/视频/帖子）";
    if (angleInput) angleInput.placeholder = currentContentAccount === "vlog" ? "真实过程：目标、困难、处理方式、结果和感受" : "独特视角：别人会怎么讲？你要怎么讲？";
    const quickButton = document.getElementById("vlogQuickButton");
    if (quickButton) quickButton.style.display = currentContentAccount === "vlog" ? "" : "none";
    updateScriptAccountUI();
}

function updateScriptAccountUI() {
    const meta = CONTENT_ACCOUNTS[currentScriptAccount] || CONTENT_ACCOUNTS.main;
    document.querySelectorAll("[data-script-account]").forEach(button => {
        const active = button.dataset.scriptAccount === currentScriptAccount;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    const listTitle = document.getElementById("scriptsListTitle");
    if (listTitle) listTitle.textContent = `${meta.label}脚本`;
    const grounding = document.getElementById("scriptGroundingNote");
    if (grounding) {
        if (currentScriptAccount === "vlog") {
            grounding.innerHTML = "<strong>Vlog 写作规则</strong><span>先写当天真实时间线和已有画面；AI 只整理镜头和旁白，不替你制造冲突。</span>";
        } else if (currentScriptAccount === "ad") {
            grounding.innerHTML = "<strong>商单写作规则</strong><span>如实介绍产品与服务，不使用绝对化用语，不做效果承诺。</span>";
        } else {
            grounding.innerHTML = "<strong>大号写作规则</strong><span>先补充真实经历和参考材料；没有依据的地方让 AI 留空，不允许替你编故事。</span>";
        }
    }
}

function switchScriptAccount(accountKey) {
    if (!CONTENT_ACCOUNTS[accountKey]) return;
    if (currentScriptAccount !== accountKey) {
        currentScriptAccount = accountKey;
        localStorage.setItem("script_account_key", accountKey);
        currentScriptId = null;
        updateScriptAccountUI();
        const editor = document.getElementById("scriptEditor");
        if (editor) {
            const placeholder = accountKey === "vlog"
                ? "从一个真实的创作瞬间开始，整理成镜头和旁白。"
                : accountKey === "ad"
                    ? "从选题库或灵感箱挑一条商单方向，开始写口播稿。"
                    : "从大号选题中选择一条，开始整理观点和依据。";
            editor.innerHTML = `<div class="editor-placeholder"><p>${placeholder}</p></div>`;
        }
    }
    loadScripts();
}

async function createVaultScript() {
    // 不起浏览器弹窗（会被拦截成“点了没反应”），直接建一个带日期的空脚本，打开后改标题即可
    const accountName = { main: "大号", vlog: "小号", ad: "广告" }[currentScriptAccount] || "大号";
    const now = new Date();
    const name = `${now.getMonth() + 1}月${now.getDate()}日${accountName}脚本`;
    try {
        const res = await api("/api/vault/document", "POST", {
            category: "scripts",
            account_key: currentScriptAccount,
            title: name,
            body: "",
        });
        if (!res.ok) throw new Error(res.error || "新建失败");
        await loadScripts();
        await loadScript(VAULT_ID_PREFIX + res.note_id);
        const titleInput = document.getElementById("editorTitle");
        if (titleInput) { titleInput.focus(); titleInput.select(); }
    } catch (error) {
        alert(error.message || "新建失败");
    }
}

function openVaultInObsidian(relativePath) {
    if (!relativePath) return;
    window.location.href = vaultPathUrl(relativePath);
}

function switchContentAccount(accountKey) {
    if (!CONTENT_ACCOUNTS[accountKey]) return;
    closeTopicDetail();
    currentContentAccount = accountKey;
    currentScriptId = null;
    currentScriptAccount = accountKey;
    localStorage.setItem("content_account_key", accountKey);
    localStorage.setItem("script_account_key", accountKey);
    updateContentAccountUI();
    updateScriptAccountUI();
    const editor = document.getElementById("scriptEditor");
    if (editor) {
        editor.innerHTML = `<div class="editor-placeholder"><p>${accountKey === "vlog" ? "从一个真实的创作瞬间开始，整理成镜头和旁白。" : "从大号选题中选择一条，开始整理观点和依据。"}</p></div>`;
    }
    loadTopics();
    loadScripts();
}

function startVlogQuickRecord() {
    currentContentAccount = "vlog";
    localStorage.setItem("content_account_key", "vlog");
    updateContentAccountUI();
    switchTab("topics");
    showTopicForm();
    document.getElementById("topicSource").value = new Date().toLocaleDateString("zh-CN") + " · 自媒体创作记录";
}

function showTopicForm() {
    updateContentAccountUI();
    document.getElementById("topicForm").style.display = "flex";
    document.getElementById("topicTitle").focus();
}

function hideTopicForm() {
    document.getElementById("topicForm").style.display = "none";
    ["topicTitle", "topicSeries", "topicSeriesOrder", "topicSource", "topicTags", "topicAngle"].forEach(id => {
        document.getElementById(id).value = "";
    });
}

async function saveTopic() {
    const title = document.getElementById("topicTitle").value.trim();
    if (!title) { alert("标题不能为空"); return; }
    const tags = document.getElementById("topicTags").value
        .split(/[,，]/).map(t => t.trim()).filter(t => t);

    await api("/api/topics", "POST", {
        title,
        source: document.getElementById("topicSource").value.trim(),
        tags,
        angle: document.getElementById("topicAngle").value.trim(),
        series_name: document.getElementById("topicSeries").value.trim(),
        series_order: Number(document.getElementById("topicSeriesOrder").value) || 0,
        status: "idea",
        account_key: currentContentAccount,
    });
    hideTopicForm();
    loadTopics();
    loadDashboard();
}

const STATUS_FLOW = ["idea", "scripting", "filming", "done"];
const STATUS_LABELS = { idea: "点子", scripting: "写稿中", filming: "定稿", done: "已发布", abandoned: "垃圾箱" };
const STATUS_COUNT_IDS = { idea: "countIdea", scripting: "countScripting", filming: "countFilming", done: "countDone" };
const IDEA_COLLAPSED_LIMIT = 9;

function getTopicSeriesName(topic) {
    const savedSeries = String(topic.series_name || "").trim();
    if (savedSeries) return savedSeries;
    const inferred = (topic.tags || []).find(tag => /系列$/u.test(String(tag).trim()));
    return inferred ? String(inferred).trim() : "";
}

function getTopicById(topicId) {
    return topicLibraryState.topics.find(topic => String(topic.id) === String(topicId));
}

function topicUpdatedValue(topic) {
    return String(topic.updated_at || topic.created_at || "");
}

function renderTopicSeriesSuggestions(topics) {
    const names = Array.from(new Set(topics.map(getTopicSeriesName).filter(Boolean))).sort((a, b) => a.localeCompare(b, "zh-CN"));
    const suggestions = document.getElementById("topicSeriesSuggestions");
    if (suggestions) suggestions.innerHTML = names.map(name => `<option value="${escapeHtml(name)}"></option>`).join("");
}

function setTopicStatusFilter(status) {
    topicLibraryState.status = STATUS_FLOW.includes(status) ? status : "all";
    document.querySelectorAll("[data-topic-status]").forEach(button => {
        button.classList.toggle("active", button.dataset.topicStatus === topicLibraryState.status);
    });
    renderTopicDirectory();
}

function topicFeaturedCardHtml(topic, index) {
    const isImportant = Number(topic.is_featured) === 1;
    const series = getTopicSeriesName(topic);
    const summary = topic.angle || topic.source || "点击查看这条选题的详细信息。";
    return `<article class="topic-focus-card${isImportant ? " is-important" : ""}" onclick="openTopicDetail('${topic.id}')">
        <header>
            <span class="topic-focus-reason">${isImportant ? "★ 重要" : index === 0 ? "最新" : "近期"}</span>
            <span class="topic-row-status status-${escapeHtml(topic.status)}">${escapeHtml(STATUS_LABELS[topic.status] || topic.status)}</span>
        </header>
        ${series ? `<small>${escapeHtml(series)}${topic.series_order ? ` · 第 ${Number(topic.series_order)} 篇` : ""}</small>` : ""}
        <h4>${escapeHtml(topic.title)}</h4>
        <p>${escapeHtml(summary)}</p>
        <footer>
            <time>${escapeHtml(String(topicUpdatedValue(topic)).slice(5, 10) || "刚刚")}</time>
            <div>
                <button title="${isImportant ? "取消重要" : "标记重要"}" onclick="event.stopPropagation();toggleTopicFeatured('${topic.id}')">${isImportant ? "★" : "☆"}</button>
                <button class="topic-discard-button" onclick="event.stopPropagation();trashTopic('${topic.id}')">丢弃</button>
            </div>
        </footer>
    </article>`;
}

function renderTopicFeatured() {
    const host = document.getElementById("topicFeaturedGrid");
    if (!host) return;
    const active = topicLibraryState.topics.filter(topic => STATUS_FLOW.includes(topic.status));
    const important = active.filter(topic => Number(topic.is_featured) === 1)
        .sort((a, b) => topicUpdatedValue(b).localeCompare(topicUpdatedValue(a)));
    const recent = active.filter(topic => Number(topic.is_featured) !== 1)
        .sort((a, b) => topicUpdatedValue(b).localeCompare(topicUpdatedValue(a)));
    const limit = Math.max(4, Math.min(6, important.length));
    const featured = [...important, ...recent].slice(0, limit);
    host.innerHTML = featured.length
        ? featured.map(topicFeaturedCardHtml).join("")
        : `<div class="topic-library-empty"><strong>还没有选题</strong><span>创建第一条选题后，最新内容会出现在这里。</span></div>`;
}

function topicCompactRowHtml(topic, inSeries = false, fallbackOrder = 0) {
    const isImportant = Number(topic.is_featured) === 1;
    const updated = String(topicUpdatedValue(topic)).slice(5, 10);
    const order = Number(topic.series_order) || Number(fallbackOrder) || 0;
    return `<div class="topic-compact-row">
        <button class="topic-important-toggle${isImportant ? " active" : ""}" title="${isImportant ? "取消重要" : "标记重要"}" onclick="toggleTopicFeatured('${topic.id}')">${isImportant ? "★" : "☆"}</button>
        <button class="topic-compact-open" onclick="openTopicDetail('${topic.id}')">
            <span class="topic-compact-title-line">${inSeries ? `<b class="topic-series-order">${String(order).padStart(2, "0")}</b>` : ""}<strong>${escapeHtml(topic.title)}</strong></span>
            <span class="topic-compact-meta"><i class="status-${escapeHtml(topic.status)}"></i>${escapeHtml(STATUS_LABELS[topic.status] || topic.status)}<time>${escapeHtml(updated)}</time></span>
        </button>
        <button class="topic-row-trash" title="放入垃圾箱" onclick="trashTopic('${topic.id}')">丢弃</button>
    </div>`;
}

function renderTopicDirectory() {
    const host = document.getElementById("topicSeriesDirectory");
    if (!host) return;
    let topics = topicLibraryState.topics.filter(topic => STATUS_FLOW.includes(topic.status));
    if (topicLibraryState.status !== "all") topics = topics.filter(topic => topic.status === topicLibraryState.status);
    topics.sort((a, b) => topicUpdatedValue(b).localeCompare(topicUpdatedValue(a)));
    const groups = new Map();
    const standalone = [];
    topics.forEach(topic => {
        const series = getTopicSeriesName(topic);
        if (!series) standalone.push(topic);
        else {
            if (!groups.has(series)) groups.set(series, []);
            groups.get(series).push(topic);
        }
    });
    const seriesHtml = Array.from(groups.entries())
        .sort(([a], [b]) => a.localeCompare(b, "zh-CN"))
        .map(([series, items]) => {
            items.sort((a, b) => {
                const orderA = Number(a.series_order) || Number.MAX_SAFE_INTEGER;
                const orderB = Number(b.series_order) || Number.MAX_SAFE_INTEGER;
                return orderA - orderB || topicUpdatedValue(b).localeCompare(topicUpdatedValue(a));
            });
            return `<details class="topic-series-group" open>
                <summary><span class="topic-series-marker">系列</span><strong>${escapeHtml(series)}</strong><b>${items.length} 篇</b><span class="topic-series-chevron">⌄</span></summary>
                <div class="topic-series-items">
                    ${items.map((topic, index) => topicCompactRowHtml(topic, true, index + 1)).join("")}
                    <button class="topic-series-add" data-series-add="${escapeHtml(series)}">＋ 添加下一篇</button>
                </div>
            </details>`;
        }).join("");
    const standaloneHtml = standalone.length ? `<section class="topic-single-group">
        <header><strong>单篇选题</strong><span>${standalone.length} 条</span></header>
        <div>${standalone.map(topic => topicCompactRowHtml(topic)).join("")}</div>
    </section>` : "";
    host.innerHTML = seriesHtml || standaloneHtml
        ? seriesHtml + standaloneHtml
        : `<div class="topic-library-empty"><strong>这里暂时没有选题</strong><span>可以切换状态筛选，或新建一条选题。</span></div>`;
    host.querySelectorAll("[data-series-add]").forEach(button => {
        button.addEventListener("click", () => addTopicToSeries(button.dataset.seriesAdd || ""));
    });
}

function renderTopicTrash() {
    const trash = topicLibraryState.topics.filter(topic => topic.status === "abandoned")
        .sort((a, b) => topicUpdatedValue(b).localeCompare(topicUpdatedValue(a)));
    const count = document.getElementById("topicTrashCount");
    if (count) count.textContent = trash.length;
    const host = document.getElementById("topicTrashList");
    if (!host) return;
    host.innerHTML = trash.length ? trash.map(topic => `<div class="topic-trash-row">
        <div><strong>${escapeHtml(topic.title)}</strong><span>${getTopicSeriesName(topic) ? escapeHtml(getTopicSeriesName(topic)) + " · " : ""}原状态：${escapeHtml(STATUS_LABELS[topic.trashed_from_status] || "点子")}</span></div>
        <button onclick="openTopicDetail('${topic.id}')">查看</button>
        <button class="topic-restore-button" onclick="restoreTopic('${topic.id}')">捡回来</button>
    </div>`).join("") : `<div class="topic-library-empty"><strong>垃圾箱是空的</strong><span>被丢弃的选题会暂存在这里。</span></div>`;
}

function renderTopicLibrary() {
    const active = topicLibraryState.topics.filter(topic => STATUS_FLOW.includes(topic.status));
    renderTopicSeriesSuggestions(active);
    renderTopicFeatured();
    renderTopicDirectory();
    renderTopicTrash();
    if (topicLibraryState.detailId) renderTopicDetail(topicLibraryState.detailId);
}

function toggleTopicTrash(forceOpen) {
    topicLibraryState.trashOpen = typeof forceOpen === "boolean" ? forceOpen : !topicLibraryState.trashOpen;
    const backdrop = document.getElementById("topicTrashBackdrop");
    const toggle = document.getElementById("topicTrashToggle");
    if (topicLibraryState.trashOpen && topicLibraryState.detailId) closeTopicDetail();
    if (backdrop) {
        if (!topicLibraryState.trashOpen && backdrop.contains(document.activeElement)) document.activeElement.blur();
        backdrop.classList.toggle("open", topicLibraryState.trashOpen);
        backdrop.setAttribute("aria-hidden", topicLibraryState.trashOpen ? "false" : "true");
    }
    if (toggle) toggle.classList.toggle("active", topicLibraryState.trashOpen);
}

async function toggleTopicFeatured(topicId) {
    if (isVaultId(topicId)) return;
    const topic = getTopicById(topicId);
    if (!topic) return;
    await api(`/api/topics/${topicId}`, "PUT", { is_featured: Number(topic.is_featured) === 1 ? 0 : 1 });
    await loadTopics();
}

async function trashTopic(topicId) {
    const topic = getTopicById(topicId);
    if (!topic || topic.status === "abandoned") return;
    if (isVaultId(topicId)) {
        if (!confirm(`把“${topic.title}”移入知识库回收站？\n文件会挪到 99-回收站，之后还能找回来。`)) return;
        await api(`/api/vault/document/${vaultNoteId(topicId)}`, "DELETE");
        closeTopicDetail();
        await loadTopics();
        return;
    }
    await api(`/api/topics/${topicId}`, "PUT", {
        status: "abandoned",
        trashed_from_status: STATUS_FLOW.includes(topic.status) ? topic.status : "idea",
    });
    closeTopicDetail();
    await Promise.all([loadTopics(), loadDashboard()]);
}

async function restoreTopic(topicId) {
    const topic = getTopicById(topicId);
    if (!topic || topic.status !== "abandoned") return;
    const restoreStatus = STATUS_FLOW.includes(topic.trashed_from_status) ? topic.trashed_from_status : "idea";
    await api(`/api/topics/${topicId}`, "PUT", { status: restoreStatus, trashed_from_status: "" });
    closeTopicDetail();
    await Promise.all([loadTopics(), loadDashboard()]);
}

function nextTopicSeriesOrder(seriesName, excludeTopicId = "") {
    const orders = topicLibraryState.topics
        .filter(topic => String(topic.id) !== String(excludeTopicId) && getTopicSeriesName(topic) === seriesName && topic.status !== "abandoned")
        .map(topic => Number(topic.series_order) || 0);
    return Math.max(0, ...orders) + 1;
}

function addTopicToSeries(seriesName) {
    showTopicForm();
    document.getElementById("topicSeries").value = seriesName;
    document.getElementById("topicSeriesOrder").value = String(nextTopicSeriesOrder(seriesName));
    document.getElementById("topicTitle").focus();
    document.getElementById("topicForm").scrollIntoView({ behavior: "smooth", block: "center" });
}

async function renameTopicSeries(topicId) {
    const currentTopic = getTopicById(topicId);
    const currentSeries = currentTopic ? getTopicSeriesName(currentTopic) : "";
    if (!currentTopic || !currentSeries) return;
    const members = topicLibraryState.topics.filter(topic => getTopicSeriesName(topic) === currentSeries);
    const entered = prompt(`重命名整个系列（共 ${members.length} 篇，包含垃圾箱中的选题）。\n系列里的所有选题会一起更新。`, currentSeries);
    if (entered === null) return;
    const normalized = entered.trim();
    if (!normalized) {
        alert("系列名称不能为空。如需移出当前选题，请使用“移动或移出此选题”。");
        return;
    }
    if (normalized === currentSeries) return;
    const destination = topicLibraryState.topics.filter(topic => getTopicSeriesName(topic) === normalized && !members.some(member => String(member.id) === String(topic.id)));
    if (destination.length && !confirm(`已经存在“${normalized}”系列。继续后会合并两个系列，并重新排列篇号。`)) return;
    await api("/api/topic-series/rename", "POST", { topic_id: topicId, new_name: normalized });
    await loadTopics();
}

async function moveTopicToSeries(topicId) {
    const currentTopic = getTopicById(topicId);
    if (!currentTopic) return;
    const currentSeries = getTopicSeriesName(currentTopic);
    const entered = prompt("输入要加入的系列名称；留空会把当前选题设为单篇。\n此操作只影响当前这一条选题。", currentSeries || "");
    if (entered === null) return;
    const normalized = entered.trim();
    if (normalized === currentSeries) return;
    await api(`/api/topics/${topicId}`, "PUT", {
        series_name: normalized,
        series_order: normalized ? nextTopicSeriesOrder(normalized, topicId) : 0,
    });
    await loadTopics();
}

async function addSiblingTopic(topicId) {
    const topic = getTopicById(topicId);
    if (!topic) return;
    let seriesName = getTopicSeriesName(topic);
    if (!seriesName) {
        const entered = prompt("先给这个系列起个名字。当前选题会自动成为第 1 篇。", topic.title || "");
        if (entered === null || !entered.trim()) return;
        seriesName = entered.trim();
        await api(`/api/topics/${topicId}`, "PUT", { series_name: seriesName, series_order: 1 });
        await loadTopics();
    }
    closeTopicDetail();
    addTopicToSeries(seriesName);
}

async function setTopicProgress(topicId, nextStatus) {
    const topic = getTopicById(topicId);
    if (!topic || !STATUS_FLOW.includes(nextStatus) || topic.status === nextStatus) return;
    await api(`/api/topics/${topicId}`, "PUT", { status: nextStatus });
    await Promise.all([loadTopics(), loadDashboard()]);
}

async function renderVaultTopicDetail(topic) {
    const content = document.getElementById("topicDetailContent");
    const status = document.getElementById("topicDetailStatus");
    const actions = document.getElementById("topicDetailActions");
    if (!content || !status || !actions) return;
    const accountKey = topic.account_key || "main";
    status.textContent = "知识库手写";
    status.className = "topic-detail-status status-idea";
    content.innerHTML = `
        <div class="topic-detail-heading">
            <small>知识库 · ${escapeHtml((CONTENT_ACCOUNTS[accountKey] || CONTENT_ACCOUNTS.main).label)}</small>
            <h2>${escapeHtml(topic.title)}</h2>
        </div>
        <section class="topic-vault-editor">
            <label class="topic-vault-field"><span>标题</span><input class="input" id="vaultTopicTitle" value="${escapeHtml(topic.title)}"></label>
            <label class="topic-vault-field"><span>角度与备注</span><textarea class="textarea" id="vaultTopicBody" rows="12" placeholder="写下这条选题的角度、来源和想法，保存后会直接写回 Obsidian 文件。"></textarea></label>
            <p class="topic-vault-hint" id="vaultTopicHint">正在读取文件…</p>
        </section>`;
    actions.innerHTML = `
        <button class="btn-primary" onclick="saveVaultTopic()">保存到知识库</button>
        <button onclick="openVaultInObsidian('${escapeHtml(topic.relative_path || "")}')">在 Obsidian 中打开</button>
        <button class="topic-detail-trash" onclick="trashTopic('${topic.id}')">放入回收站</button>`;
    try {
        const doc = await api(`/api/vault/document/${vaultNoteId(topic.id)}`);
        if (String(topicLibraryState.detailId) !== String(topic.id)) return;
        if (!doc.ok) throw new Error(doc.error || "读取失败");
        const titleEl = document.getElementById("vaultTopicTitle");
        const bodyEl = document.getElementById("vaultTopicBody");
        const hint = document.getElementById("vaultTopicHint");
        if (titleEl) titleEl.value = doc.title || topic.title;
        if (bodyEl) bodyEl.value = doc.body || "";
        if (hint) hint.textContent = doc.relative_path || "";
    } catch (error) {
        const hint = document.getElementById("vaultTopicHint");
        if (hint) hint.textContent = error.message || "读取失败";
    }
}

async function saveVaultTopic() {
    const id = String(topicLibraryState.detailId || "");
    if (!isVaultId(id)) return;
    const title = (document.getElementById("vaultTopicTitle")?.value || "").trim();
    if (!title) { alert("标题不能为空"); return; }
    const body = document.getElementById("vaultTopicBody")?.value || "";
    try {
        await api(`/api/vault/document/${vaultNoteId(id)}`, "PUT", { title, body });
        const hint = document.getElementById("vaultTopicHint");
        if (hint) hint.textContent = "已保存到知识库";
        await loadTopics();
    } catch (error) {
        alert(error.message || "保存失败");
    }
}

function renderTopicDetail(topicId) {
    const topic = getTopicById(topicId);
    if (!topic) { closeTopicDetail(); return; }
    if (isVaultId(topic.id)) { renderVaultTopicDetail(topic); return; }
    const content = document.getElementById("topicDetailContent");
    const status = document.getElementById("topicDetailStatus");
    const actions = document.getElementById("topicDetailActions");
    if (!content || !status || !actions) return;
    const series = getTopicSeriesName(topic);
    status.textContent = topic.status === "abandoned" ? "垃圾箱" : (STATUS_LABELS[topic.status] || topic.status);
    status.className = `topic-detail-status status-${escapeHtml(topic.status)}`;
    const tags = (topic.tags || []).map(tag => `<span>${escapeHtml(tag)}</span>`).join("");
    const progress = STATUS_FLOW.map((step, index) => `<button class="${topic.status === step ? "active" : ""}" onclick="setTopicProgress('${topic.id}','${step}')"><i>${index + 1}</i><span>${escapeHtml(STATUS_LABELS[step])}</span></button>`).join("");
    content.innerHTML = `
        <div class="topic-detail-heading">
            ${series ? `<small>${escapeHtml(series)}${topic.series_order ? ` · 第 ${Number(topic.series_order)} 篇` : ""}</small>` : `<small>单篇选题</small>`}
            <h2>${escapeHtml(topic.title)}</h2>
            <time>更新于 ${escapeHtml(String(topicUpdatedValue(topic)).slice(0, 16).replace("T", " "))}</time>
        </div>
        ${topic.status !== "abandoned" ? `<section class="topic-detail-progress"><h4>创作进度</h4><div class="topic-progress-selector">${progress}</div></section>` : ""}
        ${topic.status !== "abandoned" ? `<section class="topic-detail-series"><div class="topic-detail-section-heading"><h4>所属系列</h4><button onclick="${series ? "renameTopicSeries" : "moveTopicToSeries"}('${topic.id}')">${series ? "重命名整个系列" : "加入系列"}</button></div>${series ? `<div class="topic-current-series"><span>系列</span><strong>${escapeHtml(series)}</strong><small>第 ${Number(topic.series_order) || 1} 篇</small></div>` : `<p>当前是单篇选题，也可以从这里创建或加入一个系列。</p>`}<div class="topic-detail-series-actions"><button class="topic-series-sibling-add" onclick="addSiblingTopic('${topic.id}')">＋ ${series ? "添加同系列选题" : "设为系列并添加下一篇"}</button>${series ? `<button class="topic-series-move" onclick="moveTopicToSeries('${topic.id}')">移动或移出此选题</button>` : ""}</div></section>` : ""}
        <section><h4>来源</h4><p>${escapeHtml(topic.source || "没有记录来源")}</p></section>
        <section><h4>角度与备注</h4><p>${escapeHtml(topic.angle || "没有补充说明")}</p></section>
        ${tags ? `<section><h4>标签</h4><div class="topic-detail-tags">${tags}</div></section>` : ""}`;
    if (topic.status === "abandoned") {
        actions.innerHTML = `<button class="topic-restore-button" onclick="restoreTopic('${topic.id}')">从垃圾箱捡回来</button><button class="topic-detail-delete" onclick="deleteTopic('${topic.id}')">彻底删除</button>`;
        return;
    }
    const existingScript = Array.isArray(topic.scripts) ? topic.scripts.find(script => script.status !== "trashed") : null;
    const scriptAction = topic.status === "scripting" || topic.status === "filming"
        ? `<button class="btn-primary" onclick="openTopicScript('${topic.id}', '${existingScript?.id || ""}')">${existingScript ? "打开脚本" : "开始写"}</button>` : "";
    actions.innerHTML = `
        <button onclick="toggleTopicFeatured('${topic.id}')">${Number(topic.is_featured) === 1 ? "取消重要" : "★ 标记重要"}</button>
        ${scriptAction}
        <button class="topic-detail-trash" onclick="trashTopic('${topic.id}')">放入垃圾箱</button>
        <button class="topic-detail-delete" onclick="deleteTopic('${topic.id}')">彻底删除</button>`;
}

function openTopicDetail(topicId) {
    if (topicLibraryState.trashOpen) toggleTopicTrash(false);
    topicLibraryState.detailId = String(topicId);
    renderTopicDetail(topicId);
    const backdrop = document.getElementById("topicDetailBackdrop");
    if (backdrop) {
        backdrop.classList.add("open");
        backdrop.setAttribute("aria-hidden", "false");
    }
}

function closeTopicDetail() {
    topicLibraryState.detailId = "";
    const backdrop = document.getElementById("topicDetailBackdrop");
    if (backdrop) {
        if (backdrop.contains(document.activeElement)) document.activeElement.blur();
        backdrop.classList.remove("open");
        backdrop.setAttribute("aria-hidden", "true");
    }
}

function toggleIdeaPool() {
    topicIdeaExpanded = !topicIdeaExpanded;
    loadTopics();
}

function topicCardHtml(topic, status) {
    const currentIdx = STATUS_FLOW.indexOf(status);
    const previousStatus = currentIdx > 0 ? STATUS_FLOW[currentIdx - 1] : null;
    const nextStatus = currentIdx + 1 < STATUS_FLOW.length ? STATUS_FLOW[currentIdx + 1] : null;
    const existingScript = Array.isArray(topic.scripts) ? topic.scripts.find(script => script.status !== "trashed") : null;
    const accountKey = topic.account_key || "main";
    const accountMeta = CONTENT_ACCOUNTS[accountKey];
    const otherAccount = accountKey === "main" ? "vlog" : "main";
    const compactClass = status === "idea" ? " is-idea-card" : status === "done" ? " is-published-card" : "";
    const updated = String(topic.updated_at || topic.created_at || "").slice(5, 10);
    return `<article class="topic-card account-${escapeHtml(accountKey)}${compactClass}">
        <div class="topic-card-account"><span class="content-account-badge ${escapeHtml(accountKey)}">${escapeHtml(accountMeta.label)}</span>${updated ? `<time>${escapeHtml(updated)}</time>` : ""}</div>
        <div class="topic-card-title">${escapeHtml(topic.title)}</div>
        ${topic.source ? `<div class="topic-card-source">${escapeHtml(topic.source)}</div>` : ""}
        ${topic.angle && status !== "done" ? `<div class="topic-card-angle">${escapeHtml(topic.angle)}</div>` : ""}
        ${status !== "done" && (topic.tags || []).length ? `<div class="topic-card-tags">${(topic.tags || []).map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>` : ""}
        <div class="topic-card-actions">
            ${previousStatus ? `<button class="topic-step-back" onclick="moveTopic('${topic.id}', '${previousStatus}', true)">&larr; ${STATUS_LABELS[previousStatus]}</button>` : ""}
            ${nextStatus ? `<button class="topic-step-forward" onclick="moveTopic('${topic.id}', '${nextStatus}')">&rarr; ${STATUS_LABELS[nextStatus]}</button>` : ""}
            ${status === "scripting" || status === "filming" ? `<button onclick="openTopicScript('${topic.id}', '${existingScript?.id || ""}')">${existingScript ? "打开脚本" : "开始写"}</button>` : ""}
            ${status !== "done" ? `<button onclick="moveTopicAccount('${topic.id}', '${otherAccount}')">移到${escapeHtml(CONTENT_ACCOUNTS[otherAccount].label)}</button>` : ""}
            <button onclick="deleteTopic('${topic.id}')" class="btn-delete">删除</button>
        </div>
    </article>`;
}

async function loadTopics() {
    const topics = await api("/api/topics");
    // 知识库里手写的选题（还没有进工作台数据库的文件）
    let vaultTopics = [];
    try {
        const res = await api("/api/vault/library?category=topics");
        vaultTopics = (res.items || []).map(item => ({
            id: VAULT_ID_PREFIX + item.note_id,
            title: item.title,
            source: "知识库手写",
            angle: "",
            tags: [],
            status: item.status === "abandoned" ? "idea" : (STATUS_FLOW.includes(item.status) ? item.status : "idea"),
            account_key: item.account_key || "main",
            is_featured: 0,
            created_at: item.updated_at,
            updated_at: item.updated_at,
            scripts: [],
            from_vault: true,
            relative_path: item.relative_path,
        }));
    } catch (error) {
        vaultTopics = [];
    }
    const merged = topics.concat(vaultTopics);
    document.getElementById("topicCountMain").textContent = merged.filter(t => (t.account_key || "main") === "main" && t.status !== "abandoned").length;
    document.getElementById("topicCountVlog").textContent = merged.filter(t => t.account_key === "vlog" && t.status !== "abandoned").length;
    const accountTopics = merged
        .filter(t => (t.account_key || "main") === currentContentAccount)
        .map(t => t.status === "confirmed" ? { ...t, status: "scripting" } : t);
    topicLibraryState.topics = accountTopics;
    renderTopicLibrary();
}

async function moveTopicAccount(id, targetAccount) {
    const target = CONTENT_ACCOUNTS[targetAccount];
    if (!target || !confirm(`把这条选题以及关联脚本移到${target.fullLabel}？`)) return;
    await api(`/api/topics/${id}`, "PUT", { account_key: targetAccount });
    if (currentScriptId) {
        currentScriptId = null;
        const editor = document.getElementById("scriptEditor");
        if (editor) editor.innerHTML = `<div class="editor-placeholder"><p>内容已移动到${escapeHtml(target.fullLabel)}。</p></div>`;
    }
    await Promise.all([loadTopics(), loadScripts(), loadDashboard()]);
}

async function moveTopic(id, newStatus, isBackward = false) {
    if (isBackward && !confirm(`把这条选题退回到“${STATUS_LABELS[newStatus]}”？已有脚本会完整保留。`)) return;
    await api(`/api/topics/${id}`, "PUT", { status: newStatus });
    loadTopics();
    loadDashboard();
}

async function deleteTopic(id) {
    const topic = getTopicById(id);
    const title = topic?.title ? `“${topic.title}”` : "这个选题";
    if (isVaultId(id)) {
        if (!confirm(`确定彻底删除${title}？\n知识库里的文件会被直接删掉，无法恢复。`)) return;
        await api(`/api/vault/document/${vaultNoteId(id)}?permanent=1`, "DELETE");
        if (String(topicLibraryState.detailId) === String(id)) closeTopicDetail();
        await loadTopics();
        return;
    }
    if (!confirm(`确定彻底删除${title}？\n关联脚本也会一起删除，而且无法从垃圾箱恢复。`)) return;
    await api(`/api/topics/${id}`, "DELETE");
    if (String(topicLibraryState.detailId) === String(id)) closeTopicDetail();
    await Promise.all([loadTopics(), loadDashboard()]);
}

// ========== 脚本编辑器 ==========
function scriptItemHtml(script, archived = false) {
    const title = script.title || script.topicTitle || "未命名";
    const date = script.updated_at?.slice(5, 10) || "";
    const isActive = String(script.id) === String(currentScriptId);
    const isVlog = script.account_key === "vlog";
    const moveAction = isActive && script.topic_id
        ? `<button class="script-item-move" onclick="event.stopPropagation();moveTopicAccount('${escapeHtml(script.topic_id)}', '${isVlog ? "main" : "vlog"}')">移${isVlog ? "大号" : "小号"}</button>`
        : "";
    return `<div class="script-item ${archived ? "is-final" : ""} ${isActive ? "active" : ""}" onclick="loadScript('${script.id}')">
        <div class="script-item-main">
            <div class="script-item-title">${escapeHtml(title)}</div>
            <div class="script-item-meta">
                <span class="script-status-badge ${script.status}">${archived ? "定稿" : "草稿"}</span>
                ${script.from_vault ? `<span class="script-item-vault" title="这份文件直接在知识库里，两边都能改">知识库</span>` : ""}
                <span>${escapeHtml(date)}</span>
            </div>
        </div>
        <div class="script-item-actions">
            ${moveAction}
            <button class="script-item-delete" onclick="event.stopPropagation();trashScript('${script.id}')" aria-label="删除${escapeHtml(title)}">删除</button>
        </div>
    </div>`;
}

function renderScriptTrash(trash) {
    const host = document.getElementById("scriptTrashList");
    if (!host) return;
    host.innerHTML = trash.length ? trash.map(script => {
        const restoreLabel = script.trashed_from_status === "final" ? "定稿" : "草稿";
        return `<div class="script-trash-row">
            <div><strong>${escapeHtml(script.title || script.topicTitle || "未命名")}</strong><span>原位置：${restoreLabel} · ${escapeHtml(script.updated_at?.slice(5, 10) || "")}</span></div>
            <button class="script-restore-button" onclick="restoreScript('${script.id}')">恢复</button>
            <button class="script-delete-forever" onclick="deleteScriptPermanently('${script.id}')">彻底删除</button>
        </div>`;
    }).join("") : `<div class="script-trash-empty"><strong>垃圾箱是空的</strong><span>删除的脚本会先放在这里。</span></div>`;
}

async function loadScripts() {
    const topics = await api("/api/topics");
    let allScripts = [];
    for (const t of topics) {
        if (t.scripts && t.scripts.length > 0) {
            for (const s of t.scripts) {
                allScripts.push({
                    ...s,
                    topic_id: s.topic_id || t.id,
                    account_key: s.account_key || t.account_key || "main",
                    topicTitle: t.title,
                });
            }
        }
    }
    // 知识库里手写的脚本（还没有进工作台数据库的文件）
    let vaultScripts = [];
    try {
        const res = await api("/api/vault/library?category=scripts");
        vaultScripts = (res.items || []).map(item => ({
            id: VAULT_ID_PREFIX + item.note_id,
            title: item.title,
            status: item.status === "final" ? "final" : "draft",
            account_key: item.account_key || "main",
            topic_id: "",
            topicTitle: "",
            updated_at: item.updated_at,
            from_vault: true,
            relative_path: item.relative_path,
        }));
    } catch (error) {
        vaultScripts = [];
    }
    const mergedScripts = allScripts.concat(vaultScripts);
    document.getElementById("scriptCountMain").textContent = mergedScripts.filter(s => s.account_key === "main" && s.status !== "trashed").length;
    document.getElementById("scriptCountVlog").textContent = mergedScripts.filter(s => s.account_key === "vlog" && s.status !== "trashed").length;
    const scriptCountAd = document.getElementById("scriptCountAd");
    if (scriptCountAd) scriptCountAd.textContent = mergedScripts.filter(s => s.account_key === "ad" && s.status !== "trashed").length;
    allScripts = mergedScripts.filter(s => s.account_key === currentScriptAccount);
    allScripts.sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
    scriptLibraryState.scripts = allScripts;

    const listEl = document.getElementById("scriptsList");
    const finalList = document.getElementById("scriptsFinalList");
    const drafts = allScripts.filter(script => script.status === "draft");
    const finals = allScripts.filter(script => script.status === "final");
    const trash = allScripts.filter(script => script.status === "trashed");
    listEl.innerHTML = drafts.length
        ? drafts.map(script => scriptItemHtml(script)).join("")
        : `<div class="script-list-empty"><strong>没有进行中的脚本</strong><span>${finals.length ? "定稿都收在下方，可以随时展开。" : currentScriptAccount === "vlog" ? "先记录今天真实发生的一件事。" : currentScriptAccount === "ad" ? "还没有商单脚本。" : "从选题库开始写一篇。"}</span></div>`;
    if (finalList) finalList.innerHTML = finals.length
        ? finals.map(script => scriptItemHtml(script, true)).join("")
        : `<div class="script-list-empty compact"><span>还没有定稿</span></div>`;
    document.getElementById("scriptDraftCount").textContent = drafts.length;
    document.getElementById("scriptFinalCount").textContent = finals.length;
    document.getElementById("scriptTrashCount").textContent = trash.length;
    const archive = document.getElementById("scriptFinalArchive");
    if (archive) archive.open = scriptLibraryState.finalOpen;
    renderScriptTrash(trash);
}

function handleScriptFinalToggle(details) {
    scriptLibraryState.finalOpen = Boolean(details?.open);
}

function toggleScriptTrash(force) {
    const next = typeof force === "boolean" ? force : !scriptLibraryState.trashOpen;
    scriptLibraryState.trashOpen = next;
    const backdrop = document.getElementById("scriptTrashBackdrop");
    const toggle = document.getElementById("scriptTrashToggle");
    if (backdrop) {
        backdrop.classList.toggle("open", next);
        backdrop.setAttribute("aria-hidden", next ? "false" : "true");
    }
    if (toggle) toggle.classList.toggle("active", next);
}

function getScriptFromLibrary(scriptId) {
    return scriptLibraryState.scripts.find(script => String(script.id) === String(scriptId));
}

async function trashScript(scriptId) {
    const script = getScriptFromLibrary(scriptId);
    if (!script || script.status === "trashed") return;
    const title = script.title || script.topicTitle || "这个脚本";
    if (isVaultId(scriptId)) {
        if (!confirm(`把“${title}”移入知识库回收站？\n文件会挪到 99-回收站，之后还能找回来。`)) return;
        await api(`/api/vault/document/${vaultNoteId(scriptId)}`, "DELETE");
        if (String(currentScriptId) === String(scriptId)) {
            currentScriptId = null;
            document.getElementById("scriptEditor").innerHTML = `<div class="editor-placeholder"><p>脚本已移入知识库回收站。</p></div>`;
        }
        await loadScripts();
        return;
    }
    if (!confirm(`删除“${title}”？\n脚本会先进入垃圾箱，以后还能恢复。`)) return;
    await api(`/api/scripts/${scriptId}`, "PUT", {
        status: "trashed",
        trashed_from_status: script.status === "final" ? "final" : "draft",
    });
    if (String(currentScriptId) === String(scriptId)) {
        currentScriptId = null;
        document.getElementById("scriptEditor").innerHTML = `<div class="editor-placeholder"><p>脚本已移入垃圾箱。</p></div>`;
    }
    await Promise.all([loadScripts(), loadTopics(), loadDashboard()]);
}

async function restoreScript(scriptId) {
    const script = getScriptFromLibrary(scriptId);
    if (!script || script.status !== "trashed") return;
    const status = script.trashed_from_status === "final" ? "final" : "draft";
    await api(`/api/scripts/${scriptId}`, "PUT", { status, trashed_from_status: "" });
    await Promise.all([loadScripts(), loadTopics(), loadDashboard()]);
}

async function deleteScriptPermanently(scriptId) {
    const script = getScriptFromLibrary(scriptId);
    const title = script?.title || script?.topicTitle || "这个脚本";
    if (isVaultId(scriptId)) {
        if (!confirm(`确定彻底删除“${title}”？\n知识库里的文件会被直接删掉，无法恢复。`)) return;
        await api(`/api/vault/document/${vaultNoteId(scriptId)}?permanent=1`, "DELETE");
        if (String(currentScriptId) === String(scriptId)) {
            currentScriptId = null;
            document.getElementById("scriptEditor").innerHTML = `<div class="editor-placeholder"><p>文件已删除。</p></div>`;
        }
        await loadScripts();
        return;
    }
    if (!confirm(`确定彻底删除“${title}”？\n删除后无法恢复。`)) return;
    await api(`/api/scripts/${scriptId}`, "DELETE");
    if (String(currentScriptId) === String(scriptId)) {
        currentScriptId = null;
        document.getElementById("scriptEditor").innerHTML = `<div class="editor-placeholder"><p>脚本已彻底删除。</p></div>`;
    }
    await Promise.all([loadScripts(), loadTopics(), loadDashboard()]);
}

async function openTopicScript(topicId, scriptId = "") {
    if (scriptId) {
        switchTab("scripts");
        await loadScript(scriptId);
        return;
    }
    await openScriptEditor(topicId);
}

async function openScriptEditor(topicId) {
    const result = await api("/api/scripts", "POST", {
        topic_id: topicId,
        title: "",
        content: ""
    });
    if (result.ok) {
        await api(`/api/topics/${topicId}`, "PUT", { status: "scripting" });
        switchTab("scripts");
        loadScript(result.script.id);
        loadTopics();
        loadScripts();
    }
}

async function loadScript(scriptId) {
    currentScriptId = scriptId;
    scriptReviewState = null;
    let script;
    if (isVaultId(scriptId)) {
        // 知识库里手写的文件：走 vault 接口读原始 markdown
        const doc = await api(`/api/vault/document/${vaultNoteId(scriptId)}`);
        script = {
            id: scriptId,
            title: doc.title || "",
            content: doc.body || "",
            learning_materials: doc.materials || "",
            status: doc.status === "final" ? "final" : "draft",
            account_key: doc.account_key || "main",
            from_vault: true,
            relative_path: doc.relative_path || "",
        };
    } else {
        script = await api(`/api/scripts/${scriptId}`);
    }
    if (!script.id) {
        document.getElementById("scriptEditor").innerHTML = `<div class="editor-placeholder"><p>脚本不存在</p></div>`;
        return;
    }
    const scriptAccount = CONTENT_ACCOUNTS[script.account_key] ? script.account_key : "main";
    if (currentScriptAccount !== scriptAccount) {
        currentScriptAccount = scriptAccount;
        localStorage.setItem("script_account_key", currentScriptAccount);
    }
    if (scriptAccount !== "ad" && currentContentAccount !== scriptAccount) {
        currentContentAccount = scriptAccount;
        localStorage.setItem("content_account_key", currentContentAccount);
    }
    updateContentAccountUI();

    const hasLearning = script.learning_materials && script.learning_materials.trim();
    const isVlog = currentScriptAccount === "vlog";
    const editorHtml = scriptContentToEditorHtml(script.content || "");

    document.getElementById("scriptEditor").innerHTML = `
        <div class="script-editor-toolbar" role="toolbar" aria-label="脚本标题、文本格式与 AI 辅助">
            <div class="script-format-tools" aria-label="文本格式">
                <button class="script-format-bold" type="button" title="加粗" aria-label="加粗" onmousedown="event.preventDefault();rememberScriptSelection()" onclick="formatScriptText('bold')"><strong>B</strong></button>
                <span class="script-format-divider" aria-hidden="true"></span>
                <button type="button" title="小字号" onmousedown="event.preventDefault();rememberScriptSelection()" onclick="formatScriptText('fontSize','2')">小</button>
                <button type="button" title="中字号" onmousedown="event.preventDefault();rememberScriptSelection()" onclick="formatScriptText('fontSize','3')">中</button>
                <button type="button" title="大字号" onmousedown="event.preventDefault();rememberScriptSelection()" onclick="formatScriptText('fontSize','5')">大</button>
                <label class="script-color-tool" title="字体颜色" onmousedown="rememberScriptSelection()">
                    <span>A</span>
                    <input type="color" value="#e6edf3" aria-label="字体颜色" oninput="formatScriptText('foreColor',this.value)">
                </label>
            </div>
            <input type="text" class="editor-title" id="editorTitle" aria-label="脚本标题" placeholder="脚本标题" value="${escapeHtml(script.title || '')}">
            <div class="script-ai-actions">
                <div class="script-review-targets" id="scriptReviewTargets" role="group" aria-label="预审目标平台">
                    <label title="按小红书规则检查"><input type="checkbox" value="xhs" checked><span>小红书</span></label>
                    <label title="按抖音规则检查"><input type="checkbox" value="douyin" checked><span>抖音</span></label>
                    <label title="按微信视频号规则检查"><input type="checkbox" value="wechat" checked><span>视频号</span></label>
                </div>
                <button class="script-review-button" type="button" onclick="runScriptReview(this)"><span></span>AI 预审</button>
                <details class="script-ai-menu" id="scriptAiMenu">
                    <summary onmousedown="rememberScriptSelection()">AI 辅助</summary>
                    <div class="script-ai-options">
                        <button onmousedown="event.preventDefault();rememberScriptSelection()" onclick="runScriptAssist('outline', this)">${isVlog ? "整理拍摄清单" : "生成提纲"}</button>
                        <button onmousedown="event.preventDefault();rememberScriptSelection()" onclick="runScriptAssist('continue', this)">${isVlog ? "继续下个场景" : "续写一段"}</button>
                        <button onmousedown="event.preventDefault();rememberScriptSelection()" onclick="runScriptAssist('polish', this)">润色选中</button>
                    </div>
                </details>
            </div>
        </div>
        <div class="editor-content" id="editorContent" contenteditable="true" role="textbox" aria-multiline="true" aria-label="脚本正文" spellcheck="true" data-placeholder="${isVlog ? "按真实时间线记录：开场镜头、过程、转折、结果和当下感受。" : "从真实经历和明确观点开始写。"}">${editorHtml}</div>
        <div class="editor-learning-section">
            <div class="learning-header" onclick="toggleLearning()">
                <span class="learning-title">${isVlog ? "当天素材与时间线" : "写作依据"}</span>
                <span class="learning-status">${hasLearning ? '已填写' : `<span class="learning-empty">${isVlog ? "先记下当天发生的事、已有画面和真实感受" : "先放入真实经历、链接摘要或参考材料"}</span>`}</span>
                <span class="toggle-icon" id="learningToggleIcon">▶</span>
            </div>
            <div class="learning-body" id="learningBody" style="display:none">
                    <textarea class="learning-materials-input" id="editorMaterials" placeholder="${isVlog ? "按时间写下：今天的目标、发生的事情、遇到的问题、拍到的画面、结果和真实感受。" : "粘贴真实经历、ima 笔记、视频观察或需要核验的资料。"}">${escapeHtml(script.learning_materials || '')}</textarea>
            </div>
        </div>
        <div class="script-assist-result" id="scriptAssistResult" style="display:none">
            <div class="script-assist-result-head"><strong>AI 建议</strong><button onclick="closeScriptAssist()">关闭</button></div>
            <textarea id="scriptAssistText"></textarea>
            <div><button class="btn-primary" onclick="applyScriptAssist('insert')">插入光标处</button><button class="btn-ghost" onclick="applyScriptAssist('replace')">替换选中内容</button></div>
        </div>
        <section class="script-review-panel" id="scriptReviewPanel" style="display:none" aria-live="polite">
            <div class="script-review-head"><div><span>发布前检查</span><strong>AI 预审</strong></div><button onclick="closeScriptReview()" aria-label="关闭预审结果">×</button></div>
            <div id="scriptReviewBody"></div>
        </section>
        <div class="editor-footer">
            <button class="btn-primary" onclick="saveScript()">保存</button>
            ${script.from_vault
                ? `<button class="btn-secondary" data-vault-path="${escapeHtml(script.relative_path || "")}" onclick="openVaultInObsidian(this.dataset.vaultPath)">在 Obsidian 中打开</button>`
                : `<button class="btn-secondary" onclick="markScriptFinal()">标记定稿</button>`}
            <span class="editor-save-status" id="editorSaveStatus"></span>
        </div>
    `;

    const content = document.getElementById("editorContent");
    const title = document.getElementById("editorTitle");
    const materials = document.getElementById("editorMaterials");
    content.addEventListener("input", () => {
        clearTimeout(scriptSaveTimer);
        markScriptReviewStale();
        document.getElementById("editorSaveStatus").textContent = "编辑中...";
        scriptSaveTimer = setTimeout(() => saveScript(true), 2000);
    });
    content.addEventListener("mouseup", rememberScriptSelection);
    content.addEventListener("keyup", rememberScriptSelection);
    content.addEventListener("focus", rememberScriptSelection);
    content.addEventListener("paste", event => {
        event.preventDefault();
        const text = event.clipboardData?.getData("text/plain") || "";
        document.execCommand("insertText", false, text);
    });
    content.addEventListener("keydown", event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "b") {
            event.preventDefault();
            formatScriptText("bold");
        }
    });
    title.addEventListener("input", () => {
        clearTimeout(scriptSaveTimer);
        markScriptReviewStale();
        scriptSaveTimer = setTimeout(() => saveScript(true), 2000);
    });
    materials.addEventListener("input", () => {
        clearTimeout(scriptSaveTimer);
        markScriptReviewStale();
        document.getElementById("editorSaveStatus").textContent = "写作依据编辑中...";
        scriptSaveTimer = setTimeout(() => saveScript(true), 2000);
    });

    loadScripts();
    loadLatestScriptReview(scriptId);
}

function markdownLiteToEditorHtml(content) {
    // 只还原工作台自己会写出去的语法，其余保留字面，保证往返不丢内容
    return escapeHtml(String(content || ""))
        .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\r\n|\r|\n/g, "<br>");
}

function scriptContentToEditorHtml(content) {
    const value = String(content || "");
    if (value.includes('data-script-rich-text="true"') || value.includes("data-script-rich-text='true'")) {
        const holder = document.createElement("div");
        holder.innerHTML = value;
        const rich = holder.querySelector('[data-script-rich-text="true"]');
        return sanitizeScriptRichHtml(rich ? rich.innerHTML : "");
    }
    return escapeHtml(value).replace(/\r\n|\r|\n/g, "<br>");
}

function sanitizeScriptRichHtml(rawHtml) {
    const template = document.createElement("template");
    template.innerHTML = String(rawHtml || "");
    const allowedTags = new Set(["BR", "DIV", "P", "STRONG", "B", "SPAN"]);
    const blockedTags = new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "LINK", "META"]);
    const allowedSizes = new Set(["small", "medium", "large", "x-large", "xx-large"]);

    function clean(node) {
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        [...node.childNodes].forEach(clean);
        if (blockedTags.has(node.tagName)) {
            node.remove();
            return;
        }
        if (!allowedTags.has(node.tagName)) {
            node.replaceWith(...node.childNodes);
            return;
        }
        const styles = [];
        if (node.tagName === "SPAN") {
            const size = String(node.style.fontSize || "").toLowerCase();
            const color = String(node.style.color || "");
            const weight = String(node.style.fontWeight || "").toLowerCase();
            if (allowedSizes.has(size)) styles.push(`font-size:${size}`);
            if (/^(#[0-9a-f]{3,8}|rgba?\([\d\s,.%]+\))$/i.test(color)) styles.push(`color:${color}`);
            if (weight === "bold" || Number.parseInt(weight, 10) >= 600) styles.push("font-weight:700");
        }
        [...node.attributes].forEach(attribute => node.removeAttribute(attribute.name));
        if (styles.length) node.setAttribute("style", styles.join(";"));
    }

    [...template.content.childNodes].forEach(clean);
    return template.innerHTML;
}

function getScriptStoredContent(editor) {
    if (!editor) return "";
    const clean = sanitizeScriptRichHtml(editor.innerHTML);
    const probe = document.createElement("div");
    probe.innerHTML = clean;
    if (!String(probe.textContent || "").trim() && !probe.querySelector("br")) return "";
    return `<div data-script-rich-text="true">${clean}</div>`;
}

function rememberScriptSelection() {
    const editor = document.getElementById("editorContent");
    const selection = window.getSelection();
    if (!editor || !selection || !selection.rangeCount) return scriptRichSelection;
    const range = selection.getRangeAt(0);
    if (!editor.contains(range.commonAncestorContainer)) return scriptRichSelection;
    scriptRichSelection = range.cloneRange();
    return scriptRichSelection;
}

function restoreScriptSelection() {
    const editor = document.getElementById("editorContent");
    if (!editor) return null;
    let range = scriptRichSelection ? scriptRichSelection.cloneRange() : null;
    if (!range || !editor.contains(range.commonAncestorContainer)) {
        range = document.createRange();
        range.selectNodeContents(editor);
        range.collapse(false);
    }
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    return range;
}

function formatScriptText(command, value = "") {
    const editor = document.getElementById("editorContent");
    if (!editor) return;
    editor.focus();
    restoreScriptSelection();
    document.execCommand("styleWithCSS", false, true);
    document.execCommand(command, false, value || null);
    rememberScriptSelection();
    editor.dispatchEvent(new Event("input", { bubbles: true }));
}

function toggleLearning() {
    const body = document.getElementById("learningBody");
    const icon = document.getElementById("learningToggleIcon");
    if (!body) return;
    if (body.style.display === "none") {
        body.style.display = "block";
        icon.textContent = "▼";
    } else {
        body.style.display = "none";
        icon.textContent = "▶";
    }
}

async function saveScript(auto = false) {
    if (!currentScriptId) return;
    const title = document.getElementById("editorTitle").value;
    const content = getScriptStoredContent(document.getElementById("editorContent"));
    const learning_materials = document.getElementById("editorMaterials")?.value || "";
    if (isVaultId(currentScriptId)) {
        const saved = await api(`/api/vault/document/${vaultNoteId(currentScriptId)}`, "PUT", {
            title,
            body: content,
            materials: learning_materials,
        });
        // 标题改了，知识库里的文件名会跟着变，标识要同步更新
        if (saved && saved.note_id) currentScriptId = VAULT_ID_PREFIX + saved.note_id;
    } else {
        await api(`/api/scripts/${currentScriptId}`, "PUT", { title, content, learning_materials });
    }
    const status = document.getElementById("editorSaveStatus");
    if (status) {
        status.textContent = auto ? "已自动保存" : "已保存";
        setTimeout(() => { if (status) status.textContent = ""; }, 2000);
    }
    if (!auto) loadScripts();
}

async function markScriptFinal() {
    if (!currentScriptId) return;
    if (isVaultId(currentScriptId)) {
        document.getElementById("editorSaveStatus").textContent = "知识库文件的定稿状态请在 Obsidian 里维护";
        return;
    }
    await saveScript(true);
    await api(`/api/scripts/${currentScriptId}`, "PUT", { status: "final" });
    document.getElementById("editorSaveStatus").textContent = "已标记为定稿";
    await Promise.all([loadScripts(), loadTopics(), loadDashboard()]);
}

async function runScriptAssist(action, button) {
    if (!currentScriptId) return;
    const editor = document.getElementById("editorContent");
    const materials = document.getElementById("editorMaterials")?.value || "";
    const selectedRange = rememberScriptSelection();
    scriptAssistSelection = selectedRange ? selectedRange.cloneRange() : null;
    const selection = scriptAssistSelection ? scriptAssistSelection.toString() : "";
    if (action === "polish" && !selection.trim()) {
        document.getElementById("editorSaveStatus").textContent = "请先选中需要润色的文字";
        editor.focus();
        return;
    }
    const original = button.textContent;
    const aiMenu = document.getElementById("scriptAiMenu");
    if (aiMenu) aiMenu.open = false;
    button.disabled = true;
    button.textContent = "整理中...";
    try {
        await saveScript(true);
        const assistPayload = { action, selection, materials };
        if (isVaultId(currentScriptId)) {
            // 知识库文件不在数据库里，正文和账号直接随请求传过去
            assistPayload.content = document.getElementById("editorContent").innerText || "";
            assistPayload.account_key = currentScriptAccount;
        }
        const result = await api(`/api/scripts/${currentScriptId}/assist`, "POST", assistPayload, 135000);
        if (!result.ok) throw new Error(result.error || "AI 辅助失败");
        document.getElementById("scriptAssistText").value = result.text || "";
        document.getElementById("scriptAssistResult").style.display = "block";
    } catch (error) {
        document.getElementById("editorSaveStatus").textContent = "AI 辅助失败：" + error.message;
    } finally {
        button.disabled = false;
        button.textContent = original;
    }
}

function closeScriptAssist() {
    const panel = document.getElementById("scriptAssistResult");
    if (panel) panel.style.display = "none";
}

function applyScriptAssist(mode) {
    const editor = document.getElementById("editorContent");
    const suggestion = document.getElementById("scriptAssistText")?.value || "";
    if (!editor || !suggestion.trim()) return;
    editor.focus();
    let range = scriptAssistSelection ? scriptAssistSelection.cloneRange() : restoreScriptSelection();
    if (!range || !editor.contains(range.commonAncestorContainer)) {
        range = document.createRange();
        range.selectNodeContents(editor);
        range.collapse(false);
    }
    if (mode === "replace") range.deleteContents();
    else range.collapse(false);
    const inserted = document.createTextNode(suggestion);
    range.insertNode(inserted);
    range.setStartAfter(inserted);
    range.collapse(true);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    scriptRichSelection = range.cloneRange();
    editor.dispatchEvent(new Event("input", { bubbles: true }));
    closeScriptAssist();
    saveScript(true);
}

function scriptRiskLabel(risk) {
    return risk === "high" ? "高风险" : risk === "medium" ? "中风险" : "低风险";
}

function scriptReviewSourceHtml(source) {
    const title = escapeHtml(source?.title || "本地资料");
    const path = String(source?.path || "");
    if (path) {
        return `<button class="script-review-source" data-vault-path="${escapeHtml(path)}" onclick="openVaultInObsidian(this.dataset.vaultPath)">${title}</button>`;
    }
    return `<span class="script-review-source">${title}</span>`;
}

function getScriptReviewPlatforms() {
    const inputs = Array.from(document.querySelectorAll("#scriptReviewTargets input"));
    const selected = inputs.filter(input => input.checked)
        .map(input => input.value)
        .filter(value => ["xhs", "douyin", "wechat"].includes(value));
    if (selected.length) return selected;
    inputs.forEach(input => { input.checked = true; });
    return ["xhs", "douyin", "wechat"];
}

function scriptPlatformLabel(key) {
    return key === "xhs" ? "小红书" : key === "douyin" ? "抖音" : key === "wechat" ? "视频号" : key;
}

function scriptPolicySourceHtml(source) {
    const title = escapeHtml(source?.title || "平台规则");
    const platform = escapeHtml(source?.platform_label || scriptPlatformLabel(source?.platform || ""));
    const checkedAt = escapeHtml(source?.checked_at || "");
    const rawUrl = String(source?.url || "").trim();
    const safeUrl = /^https:\/\//i.test(rawUrl) ? escapeHtml(rawUrl) : "";
    const meta = [platform, checkedAt].filter(Boolean).join(" · ");
    if (!safeUrl) return `<span class="script-policy-source"><strong>${title}</strong><small>${meta}</small></span>`;
    return `<a class="script-policy-source" href="${safeUrl}" target="_blank" rel="noopener noreferrer"><strong>${title}</strong><small>${meta}</small></a>`;
}

function scriptReviewIssueMeta(item) {
    const platforms = Array.isArray(item?.platforms) ? item.platforms.map(scriptPlatformLabel).join(" / ") : "";
    const confidence = item?.confidence === "high" ? "高把握" : item?.confidence === "low" ? "低把握" : "需复核";
    const hitCount = Number(item?.match_count || 0) > 1 ? `同类命中 ${Number(item.match_count)} 处` : "";
    const matchedWords = Array.isArray(item?.matches) && item.matches.length ? `关键词：${item.matches.slice(0, 6).join("、")}` : "";
    return [platforms, confidence, hitCount, matchedWords].filter(Boolean).join(" · ");
}

function renderScriptReview(review) {
    const panel = document.getElementById("scriptReviewPanel");
    const body = document.getElementById("scriptReviewBody");
    if (!panel || !body || !review) return;
    const risk = ["low", "medium", "high"].includes(review.overall_risk) ? review.overall_risk : "low";
    const issues = Array.isArray(review.issues) ? review.issues : [];
    const dimensions = Array.isArray(review.dimensions) ? review.dimensions : [];
    const strengths = Array.isArray(review.strengths) ? review.strengths : [];
    const rewrites = Array.isArray(review.rewrite_examples) ? review.rewrite_examples : [];
    const sources = Array.isArray(review.sources) ? review.sources : [];
    const ruleSources = Array.isArray(review.rule_sources) ? review.rule_sources : [];
    const platformVerdicts = review.platform_verdicts && typeof review.platform_verdicts === "object"
        ? Object.values(review.platform_verdicts) : [];
    const likelyCauses = Array.isArray(review.likely_causes) ? review.likely_causes : [];
    const coverage = review.coverage && typeof review.coverage === "object" ? review.coverage : {};
    const unfinishedChecks = Array.isArray(coverage.unverified_checks) ? coverage.unverified_checks : [];
    const blockingReasons = Array.isArray(review.blocking_reasons) ? review.blocking_reasons : [];
    const stale = Boolean(review.stale);
    const staleText = Array.isArray(review.stale_reasons) && review.stale_reasons.length
        ? review.stale_reasons.join("、") : "脚本或平台规则已经变化";
    body.innerHTML = `
        ${stale ? `<div class="script-review-stale">${escapeHtml(staleText)}，这份结果已失效，请重新预审。</div>` : ""}
        ${review.notice ? `<div class="script-review-notice">${escapeHtml(review.notice)}</div>` : ""}
        <div class="script-review-summary ${risk}">
            <div class="script-review-score"><strong>${Number(review.safety_score ?? 0)}</strong><span>/ 100</span></div>
            <div><span class="script-risk-pill ${risk}">${scriptRiskLabel(risk)}</span><h3>${escapeHtml(review.publish_gate || "查看建议")}</h3><p>${escapeHtml(review.summary || "预审完成")}</p></div>
            <button class="script-review-again" onclick="runScriptReview(this)">重新预审</button>
        </div>
        ${review.hard_block ? `<section class="script-review-blocker"><header><span>必须处理</span><strong>命中硬拦截，修改并人工复核前不要发布</strong></header>${blockingReasons.length ? `<ul>${blockingReasons.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}</section>` : ""}
        <div class="script-review-disclaimer">${escapeHtml(review.policy_note || "这是发布前风险预审，不是平台官方审核，也不能保证推荐量或避免限流。")}</div>
        ${platformVerdicts.length ? `<section class="script-review-section script-platform-section"><header><strong>三平台分别判断</strong><span>各平台独立计分</span></header><div class="script-platform-verdicts">${platformVerdicts.map(item => {
            const itemRisk = ["low", "medium", "high"].includes(item.risk) ? item.risk : "low";
            const reasons = Array.isArray(item.top_reasons) ? item.top_reasons.slice(0, 3) : [];
            return `<article class="${itemRisk}"><header><div><strong>${escapeHtml(item.label || scriptPlatformLabel(item.key))}</strong><span class="script-risk-pill ${itemRisk}">${scriptRiskLabel(itemRisk)}</span></div><b>${Number(item.safety_score ?? 0)}</b></header><h4>${escapeHtml(item.publish_gate || "查看建议")}</h4><p>${item.hard_block ? "存在硬拦截" : `命中 ${Number(item.issue_count || 0)} 个风险点`}</p>${reasons.length ? `<ul>${reasons.map(reason => `<li>${escapeHtml(reason.reason || reason.category || "需复核")}</li>`).join("")}</ul>` : ""}</article>`;
        }).join("")}</div></section>` : ""}
        ${likelyCauses.length ? `<section class="script-review-section script-likely-causes"><header><strong>最可能的触发点</strong><span>根据脚本与规则匹配推断</span></header><div>${likelyCauses.slice(0, 5).map((item, index) => `<article class="${escapeHtml(item.severity || "medium")}"><b>${index + 1}</b><div><header><strong>${escapeHtml(item.category || "风险点")}</strong>${item.hard_block ? `<span>硬拦截</span>` : ""}</header>${item.quote ? `<blockquote>${escapeHtml(item.quote)}</blockquote>` : ""}<p>${escapeHtml(item.reason || "")}</p><small>${escapeHtml(scriptReviewIssueMeta(item))}</small></div></article>`).join("")}</div></section>` : ""}
        ${coverage.finished_video === "not_checked" ? `<section class="script-review-coverage"><header><span>检查缺口</span><strong>${escapeHtml(coverage.finished_video_label || "成片尚未检查")}</strong></header><p>当前分数只覆盖脚本文字。以下任何一项出现在成片里，都可能改变结论：</p>${unfinishedChecks.length ? `<ul>${unfinishedChecks.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}</section>` : ""}
        <div class="script-review-dimensions">${dimensions.map(item => `
            <article class="${escapeHtml(item.risk || "low")}"><header><strong>${escapeHtml(item.key || "检查项")}</strong><span>${scriptRiskLabel(item.risk)}</span></header><p>${escapeHtml(item.finding || "")}</p><small>${escapeHtml(item.suggestion || "")}</small></article>
        `).join("")}</div>
        <details class="script-review-section script-review-details" ${issues.length <= 5 ? "open" : ""}>
            <summary><strong>全部问题详情</strong><span>${issues.length} 处 · 点击展开</span></summary>
            ${issues.length ? `<div class="script-review-issues">${issues.map((item, index) => `
                <article class="${escapeHtml(item.severity || "low")}">
                    <div class="script-review-issue-top"><span>${item.hard_block ? "硬拦截" : scriptRiskLabel(item.severity)}</span><strong>${escapeHtml(item.category || "其他")}</strong><em>${escapeHtml(item.source || "预审")}</em></div>
                    ${item.quote ? `<blockquote>${escapeHtml(item.quote)}</blockquote>` : ""}
                    <p>${escapeHtml(item.reason || "")}</p>
                    <small class="script-review-issue-meta">${escapeHtml(scriptReviewIssueMeta(item))}</small>
                    <div class="script-review-suggestion"><span>怎么改</span><p>${escapeHtml(item.suggestion || "")}</p><button data-suggestion="${escapeHtml(item.suggestion || "")}" onclick="copyScriptReviewSuggestion(this)">复制建议</button></div>
                </article>
            `).join("")}</div>` : `<div class="script-review-empty">没有命中明显高风险表达。发布前仍要人工核对事实、版权和平台最新规则。</div>`}
        </details>
        ${strengths.length ? `<div class="script-review-section"><header><strong>保留的优点</strong></header><ul>${strengths.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
        ${rewrites.length ? `<div class="script-review-section"><header><strong>改写示例</strong></header><div class="script-review-rewrites">${rewrites.map(item => `<p>${escapeHtml(item)}</p>`).join("")}</div></div>` : ""}
        <div class="script-review-section script-policy-evidence"><header><strong>平台规则依据</strong><span>${ruleSources.length} 个官方页面</span></header><div>${ruleSources.length ? ruleSources.map(scriptPolicySourceHtml).join("") : "规则来源暂不可用，本次结果不可作为发布依据"}</div></div>
        <div class="script-review-section script-review-evidence"><header><strong>本次参考资料</strong><span>${sources.length} 条</span></header><div>${sources.length ? sources.map(scriptReviewSourceHtml).join("") : "仅使用本地规则初筛"}</div></div>
        <footer>规则包 ${escapeHtml(review.rule_version || "本地")} · ${escapeHtml(review.reviewed_at || review.created_at || "刚刚")} · ${review.engine === "ai+rules" ? "AI + 硬规则" : "硬规则"} · 分数是安全度，不是通过率</footer>
    `;
    panel.style.display = "block";
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function loadLatestScriptReview(scriptId) {
    try {
        const result = await api(`/api/scripts/${encodeURIComponent(scriptId)}/reviews`, "GET", null, 20000);
        if (String(currentScriptId) !== String(scriptId) || !result.reviews?.length) return;
        scriptReviewState = result.reviews[0];
        renderScriptReview(scriptReviewState);
    } catch (_) {
        // 历史预审不影响脚本编辑。
    }
}

function markScriptReviewStale() {
    if (!scriptReviewState || scriptReviewState.stale) return;
    scriptReviewState.stale = true;
    const panel = document.getElementById("scriptReviewPanel");
    if (panel && panel.style.display !== "none") renderScriptReview(scriptReviewState);
}

async function runScriptReview(button) {
    if (!currentScriptId) return;
    const original = button?.innerHTML;
    const status = document.getElementById("editorSaveStatus");
    if (button) { button.disabled = true; button.textContent = "预审中…"; }
    if (status) status.textContent = "正在保存并核对工作台、维基和平台风险点…";
    try {
        await saveScript(true);
        const payload = {
            title: document.getElementById("editorTitle")?.value || "",
            content: document.getElementById("editorContent")?.innerText || "",
            materials: document.getElementById("editorMaterials")?.value || "",
            account_key: currentScriptAccount,
            platforms: getScriptReviewPlatforms(),
        };
        const result = await api(`/api/scripts/${encodeURIComponent(currentScriptId)}/review`, "POST", payload, 150000);
        if (!result.ok) throw new Error(result.error || "预审失败");
        scriptReviewState = result.review || null;
        renderScriptReview(scriptReviewState);
        if (status) status.textContent = `预审完成：${scriptRiskLabel(scriptReviewState?.overall_risk)}`;
    } catch (error) {
        if (status) status.textContent = "AI 预审失败：" + error.message;
    } finally {
        if (button) { button.disabled = false; button.innerHTML = original || "AI 预审"; }
    }
}

function closeScriptReview() {
    const panel = document.getElementById("scriptReviewPanel");
    if (panel) panel.style.display = "none";
}

async function copyScriptReviewSuggestion(button) {
    const text = button?.dataset?.suggestion || "";
    if (!text) return;
    try {
        await navigator.clipboard.writeText(text);
        const original = button.textContent;
        button.textContent = "已复制";
        setTimeout(() => { button.textContent = original; }, 1200);
    } catch (_) {
        button.textContent = "复制失败";
    }
}

// ========== 周计划 ==========
function getDisplayedMonth() {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth() + currentMonthOffset, 1);
}

function getMonthGridRange(monthDate) {
    const firstDay = new Date(monthDate.getFullYear(), monthDate.getMonth(), 1);
    const gridStart = new Date(firstDay);
    gridStart.setDate(firstDay.getDate() - ((firstDay.getDay() + 6) % 7));
    const gridEnd = new Date(gridStart);
    gridEnd.setDate(gridStart.getDate() + 41);
    return { gridStart, gridEnd };
}

function weekStartForDate(value) {
    const date = value instanceof Date ? new Date(value) : new Date(`${value}T00:00:00`);
    const day = date.getDay();
    date.setDate(date.getDate() + (day === 0 ? -6 : 1 - day));
    return localDateString(date);
}

function getDefaultPlanDate() {
    const month = getDisplayedMonth();
    const today = new Date();
    const isCurrentMonth = month.getFullYear() === today.getFullYear() && month.getMonth() === today.getMonth();
    return isCurrentMonth ? localDateString(today) : localDateString(month);
}

function calendarTimeMinutes(value) {
    if (!value || !/^\d{2}:\d{2}$/.test(value)) return null;
    const parts = value.split(":").map(Number);
    return parts[0] * 60 + parts[1];
}

function calendarPlanTime(plan) {
    const start = plan.start_time || "";
    const end = plan.end_time || "";
    if (start && end) return start + "–" + end;
    if (start) return start + " 起";
    if (end) return "截止 " + end;
    return "待排";
}

function calendarCompactPlanTime(plan) {
    const start = plan.start_time || "";
    const end = plan.end_time || "";
    if (start) return start;
    if (end) return "截止" + end;
    return "待排";
}

function calendarSortedPlans(plans) {
    return plans.slice().sort((a, b) => {
        const aTime = calendarTimeMinutes(a.start_time);
        const bTime = calendarTimeMinutes(b.start_time);
        if (aTime === null && bTime !== null) return 1;
        if (aTime !== null && bTime === null) return -1;
        if (aTime !== null && bTime !== null && aTime !== bTime) return aTime - bTime;
        return String(a.created_at || "").localeCompare(String(b.created_at || ""));
    });
}

function calendarDayGaps(plans) {
    const timed = calendarSortedPlans(plans)
        .map(plan => ({ plan, start: calendarTimeMinutes(plan.start_time), end: calendarTimeMinutes(plan.end_time) }))
        .filter(item => item.start !== null);
    const unscheduled = plans.filter(plan => calendarTimeMinutes(plan.start_time) === null && calendarTimeMinutes(plan.end_time) === null);
    if (!timed.length) return { timed, gaps: [], unscheduled };
    const gaps = [];
    let cursor = 0;
    timed.forEach(item => {
        const end = item.end === null ? Math.min(1440, item.start + 60) : Math.max(item.start, item.end);
        if (item.start > cursor) gaps.push({ start: cursor, end: item.start });
        cursor = Math.max(cursor, end);
    });
    if (cursor < 1440) gaps.push({ start: cursor, end: 1440 });
    return {
        timed,
        gaps,
        unscheduled,
    };
}

function calendarMinutesLabel(value) {
    if (value === 1440) return "24:00";
    const hour = Math.floor(value / 60);
    const minute = value % 60;
    return String(hour).padStart(2, "0") + ":" + String(minute).padStart(2, "0");
}

function calendarGapLabel(gap) {
    return calendarMinutesLabel(gap.start) + "–" + calendarMinutesLabel(gap.end);
}

function calendarDayMeta(dateKey) {
    const date = new Date(dateKey + "T00:00:00");
    const fallback = date.getDay() === 0 || date.getDay() === 6
        ? { kind: "rest-day", label: "周末休息", official: false }
        : { kind: "workday", label: "工作日", official: false };
    return Object.assign({}, fallback, calendarHolidayCache[dateKey] || {});
}

function renderCalendarTask(plan, expanded = false) {
    const safeId = String(plan.id).replace(/'/g, "\\'");
    const status = ["todo", "doing", "done"].includes(plan.status) ? plan.status : "todo";
    const hasTime = calendarTimeMinutes(plan.start_time) !== null || calendarTimeMinutes(plan.end_time) !== null;
    const timeLabel = expanded ? calendarPlanTime(plan) : calendarCompactPlanTime(plan);
    return '<article class="calendar-task status-' + status + (status === "done" ? " done" : "") + (expanded ? " is-expanded" : "") + '"' +
        (hasTime ? '' : ' data-unscheduled="true"') +
        ' role="button" tabindex="0" onclick="editPlan(\'' + safeId + '\')" ' +
        'onkeydown="if(event.target===this&&(event.key===\'Enter\'||event.key===\' \')){event.preventDefault();editPlan(\'' + safeId + '\')}" ' +
        'aria-label="' + escapeHtml(calendarPlanTime(plan) + " " + (plan.title || "未命名任务")) + '">' +
        '<span class="calendar-task-time" title="' + escapeHtml(calendarPlanTime(plan)) + '">' + escapeHtml(timeLabel) + '</span>' +
        '<button class="calendar-task-check" aria-label="' + (status === "done" ? "标记为待办" : "标记为完成") + '" onclick="event.stopPropagation();togglePlanDone(\'' + safeId + '\',\'' + status + '\')">' + (status === "done" ? "✓" : "") + '</button>' +
        '<div class="calendar-task-copy"><strong>' + escapeHtml(plan.title) + '</strong>' + (plan.description ? '<p>' + escapeHtml(plan.description) + '</p>' : "") + '</div>' +
        '<button class="calendar-task-delete" aria-label="删除任务" onclick="event.stopPropagation();deletePlan(\'' + safeId + '\')">×</button>' +
        '</article>';
}

function renderCalendarHoverCard(dateKey, plans) {
    const meta = calendarDayMeta(dateKey);
    const date = new Date(dateKey + "T00:00:00");
    const fullDate = date.getFullYear() + "年" + (date.getMonth() + 1) + "月" + date.getDate() + "日";
    const timeline = calendarDayGaps(plans);
    const unscheduledNote = timeline.unscheduled.length ? " · " + timeline.unscheduled.length + " 项待排" : "";
    return '<div class="calendar-hover-head">' +
        '<div><strong>' + fullDate + '</strong><span class="calendar-hover-kind kind-' + meta.kind + '">' + escapeHtml(meta.label) + '</span></div>' +
        '<button type="button" class="calendar-hover-close" aria-label="关闭" onclick="hideCalendarHoverCard()">×</button></div>' +
        '<div class="calendar-hover-subtitle">当天计划 · ' + plans.length + ' 项' + unscheduledNote + '</div>' +
        '<div class="calendar-hover-timeline">' +
            (plans.length ? calendarSortedPlans(plans).map(plan => renderCalendarTask(plan, true)).join("") : '<div class="calendar-hover-empty">全天没有安排</div>') +
        '</div>' +
        '<div class="calendar-hover-note">未标时间的计划会显示为“待排”，其余时间保持折叠。</div>' +
        '<button type="button" class="calendar-hover-add" onclick="showPlanForm(\'' + dateKey + '\');hideCalendarHoverCard()">+ 添加当天计划</button>';
}

function positionCalendarHoverCard(day) {
    const card = document.getElementById("calendarHoverCard");
    if (!card || !day) return;
    const rect = day.getBoundingClientRect();
    const viewport = window.visualViewport;
    const viewportWidth = viewport ? viewport.width : window.innerWidth;
    const viewportHeight = viewport ? viewport.height : window.innerHeight;
    const margin = 14;
    const gap = 12;
    const width = Math.min(390, viewportWidth - margin * 2);
    card.style.width = width + "px";
    const cardRect = card.getBoundingClientRect();
    let left = rect.right + gap;
    if (left + cardRect.width > viewportWidth - margin) left = rect.left - cardRect.width - gap;
    if (left < margin) left = Math.min(Math.max(margin, rect.left + (rect.width - cardRect.width) / 2), viewportWidth - cardRect.width - margin);
    let top = rect.top;
    if (top + cardRect.height > viewportHeight - margin) top = viewportHeight - cardRect.height - margin;
    if (top < margin) top = margin;
    card.style.left = Math.round(left) + "px";
    card.style.top = Math.round(top) + "px";
}

function cancelCalendarHoverHide() {
    if (calendarHoverTimer) clearTimeout(calendarHoverTimer);
    calendarHoverTimer = null;
}

function openCalendarHoverCard(day) {
    cancelCalendarHoverHide();
    const dateKey = day && day.dataset ? day.dataset.date : "";
    if (!dateKey) return;
    const card = document.getElementById("calendarHoverCard");
    if (!card) return;
    calendarHoverDay = day;
    card.innerHTML = renderCalendarHoverCard(dateKey, calendarPlansByDate.get(dateKey) || []);
    card.hidden = false;
    card.classList.add("is-open");
    requestAnimationFrame(() => positionCalendarHoverCard(day));
}

function openCalendarDetails(dateKey) {
    const day = document.querySelector('#weeklyPlans .calendar-day[data-date="' + dateKey + '"]');
    if (!day) return;
    calendarHoverPinned = true;
    openCalendarHoverCard(day);
}

function queueHideCalendarHover() {
    cancelCalendarHoverHide();
    calendarHoverTimer = setTimeout(() => {
        const card = document.getElementById("calendarHoverCard");
        const active = document.activeElement;
        const cardHasFocus = card && active && card.contains(active);
        const dayHasFocus = calendarHoverDay && active && calendarHoverDay.contains(active);
        if (!calendarHoverPinned && card && !card.matches(":hover") && !(calendarHoverDay && calendarHoverDay.matches(":hover")) && !cardHasFocus && !dayHasFocus) hideCalendarHoverCard();
    }, 140);
}

function hideCalendarHoverCard() {
    cancelCalendarHoverHide();
    const card = document.getElementById("calendarHoverCard");
    if (!card) return;
    calendarHoverPinned = false;
    card.classList.remove("is-open");
    card.hidden = true;
    calendarHoverDay = null;
}

function bindCalendarHoverCards() {
    const card = document.getElementById("calendarHoverCard");
    if (card && card.parentElement !== document.body) document.body.appendChild(card);
    document.querySelectorAll("#weeklyPlans .calendar-day").forEach(day => {
        const dateKey = day.dataset.date;
        const hasPlans = (calendarPlansByDate.get(dateKey) || []).length > 0;
        day.addEventListener("mouseenter", () => { if (hasPlans) openCalendarHoverCard(day); });
        day.addEventListener("mouseleave", queueHideCalendarHover);
        day.addEventListener("focusin", () => { if (hasPlans) openCalendarHoverCard(day); });
        day.addEventListener("focusout", queueHideCalendarHover);
        day.addEventListener("click", event => {
            if (event.target.closest("button, article")) return;
            if (card && card.hidden) {
                calendarHoverPinned = true;
                openCalendarHoverCard(day);
            } else if (calendarHoverPinned) hideCalendarHoverCard();
        });
        day.querySelector(".calendar-more")?.addEventListener("click", event => {
            event.stopPropagation();
            calendarHoverPinned = true;
            openCalendarHoverCard(day);
        });
    });
    if (card && !card.dataset.bound) {
        card.dataset.bound = "1";
        card.addEventListener("mouseenter", cancelCalendarHoverHide);
        card.addEventListener("mouseleave", () => { if (!calendarHoverPinned) queueHideCalendarHover(); });
        window.addEventListener("resize", () => {
            if (!card.hidden && calendarHoverDay) positionCalendarHoverCard(calendarHoverDay);
        });
    }
}

async function loadCalendarHolidaySchedule(year) {
    try {
        const payload = await api("/api/calendar/holidays?year=" + year);
        calendarHolidayCache = payload.days || {};
        calendarHolidaySource = payload;
        const source = document.getElementById("calendarHolidaySource");
        if (source) source.textContent = payload.official_schedule_available ? "国务院 " + payload.updated_at + " 安排" : "周末规则 · 法定安排待更新";
    } catch (error) {
        calendarHolidayCache = {};
        calendarHolidaySource = null;
        const source = document.getElementById("calendarHolidaySource");
        if (source) source.textContent = "周末规则 · 法定安排暂不可用";
        console.warn("日历节假日数据加载失败", error);
    }
}

function renderCalendarDay(date, month, dayPlans, todayKey) {
    const dateKey = localDateString(date);
    const meta = calendarDayMeta(dateKey);
    const isOutside = date.getMonth() !== month.getMonth();
    const isToday = dateKey === todayKey;
    const dateLabel = isOutside ? (date.getMonth() + 1) + "/" + date.getDate() : String(date.getDate());
    const fullDateLabel = date.getFullYear() + "年" + (date.getMonth() + 1) + "月" + date.getDate() + "日";
    const sortedPlans = calendarSortedPlans(dayPlans);
    const previewLimit = sortedPlans.length > 2 ? 1 : 2;
    const previewPlans = sortedPlans.slice(0, previewLimit);
    const hiddenCount = Math.max(0, sortedPlans.length - previewPlans.length);
    const hasPlans = dayPlans.length > 0;
    const hasUnscheduled = dayPlans.some(plan => calendarTimeMinutes(plan.start_time) === null && calendarTimeMinutes(plan.end_time) === null);
    const visibleKind = meta.kind === "workday" ? "" : meta.kind === "rest-day" ? "休息" : meta.label;
    const planCount = hasPlans ? '<span class="calendar-day-plan-count">' + dayPlans.length + '项</span>' : "";
    const empty = '<div class="calendar-day-empty" aria-hidden="true"></div>';
    return '<section class="calendar-day day-' + meta.kind + (hasPlans ? " has-plans" : "") + (hasUnscheduled ? " has-unscheduled" : "") + (isOutside ? " is-outside" : "") + (isToday ? " is-today" : "") + '" data-date="' + dateKey + '" tabindex="0" aria-label="' + fullDateLabel + "，" + escapeHtml(meta.label) + "，" + dayPlans.length + '项安排">' +
        '<header class="calendar-day-header">' +
            '<button class="calendar-date-button" onclick="openCalendarDetails(\'' + dateKey + '\')" aria-label="' + fullDateLabel + '，查看当天计划"><span>' + dateLabel + '</span>' + (isToday ? "<small>今天</small>" : "") + '</button>' +
            '<div class="calendar-day-meta">' + planCount + '<span class="calendar-day-kind">' + escapeHtml(visibleKind) + '</span><button class="calendar-add-button" onclick="showPlanForm(\'' + dateKey + '\')" aria-label="为' + fullDateLabel + '添加任务">＋</button></div>' +
        '</header>' +
        '<div class="calendar-day-tasks">' + (dayPlans.length ? previewPlans.map(plan => renderCalendarTask(plan)).join("") : empty) + (hiddenCount ? '<button class="calendar-more" type="button">+' + hiddenCount + ' 项</button>' : "") + '</div>' +
        '</section>';
}

async function loadWeeklyPlans() {
    const loadToken = ++calendarLoadToken;
    hideCalendarHoverCard();
    const month = getDisplayedMonth();
    const range = getMonthGridRange(month);
    document.getElementById("weekLabel").textContent = month.getFullYear() + "年" + (month.getMonth() + 1) + "月";

    const plans = await api("/api/weekly-plans?start=" + localDateString(range.gridStart) + "&end=" + localDateString(range.gridEnd));
    await loadCalendarHolidaySchedule(month.getFullYear());
    if (loadToken !== calendarLoadToken) return;
    weeklyPlanCache = plans;

    calendarPlansByDate = new Map();
    plans.forEach(plan => {
        const dateKey = plan.task_date || plan.week_start;
        if (!calendarPlansByDate.has(dateKey)) calendarPlansByDate.set(dateKey, []);
        calendarPlansByDate.get(dateKey).push(plan);
    });

    const todayKey = localDateString();
    const cells = [];
    for (let index = 0; index < 42; index += 1) {
        const date = new Date(range.gridStart);
        date.setDate(range.gridStart.getDate() + index);
        const dateKey = localDateString(date);
        cells.push(renderCalendarDay(date, month, calendarPlansByDate.get(dateKey) || [], todayKey));
    }
    document.getElementById("weeklyPlans").innerHTML = cells.join("");
    bindCalendarHoverCards();

    const monthPrefix = month.getFullYear() + "-" + String(month.getMonth() + 1).padStart(2, "0") + "-";
    const monthPlans = plans.filter(plan => String(plan.task_date || plan.week_start).startsWith(monthPrefix));
    const stats = { todo: 0, doing: 0, done: 0 };
    monthPlans.forEach(plan => { stats[plan.status] = (stats[plan.status] || 0) + 1; });
    document.getElementById("weekSummary").style.display = "flex";
    document.getElementById("weekTodoCount").textContent = stats.todo;
    document.getElementById("weekDoingCount").textContent = stats.doing;
    document.getElementById("weekDoneCount").textContent = stats.done;
}

function showPlanForm(date = "") {
    document.getElementById("planEditId").value = "";
    document.getElementById("planDate").value = date || getDefaultPlanDate();
    document.getElementById("planTitle").value = "";
    document.getElementById("planDesc").value = "";
    document.getElementById("planStart").value = "";
    document.getElementById("planEnd").value = "";
    document.getElementById("planForm").style.display = "grid";
    document.getElementById("planTitle").focus();
}

function editPlan(id) {
    const plan = weeklyPlanCache.find(item => item.id === id);
    if (!plan) return;
    document.getElementById("planEditId").value = plan.id;
    document.getElementById("planDate").value = plan.task_date || plan.week_start;
    document.getElementById("planTitle").value = plan.title || "";
    document.getElementById("planDesc").value = plan.description || "";
    document.getElementById("planStart").value = plan.start_time || "";
    document.getElementById("planEnd").value = plan.end_time || "";
    document.getElementById("planForm").style.display = "grid";
    document.getElementById("planTitle").focus();
}

function hidePlanForm() {
    document.getElementById("planForm").style.display = "none";
    document.getElementById("planTitle").value = "";
    document.getElementById("planDesc").value = "";
    document.getElementById("planStart").value = "";
    document.getElementById("planEnd").value = "";
    document.getElementById("planEditId").value = "";
}

async function savePlan() {
    const title = document.getElementById("planTitle").value.trim();
    if (!title) { alert("任务标题不能为空"); return; }
    const desc = document.getElementById("planDesc").value.trim();
    const startTime = document.getElementById("planStart").value || "";
    const endTime = document.getElementById("planEnd").value || "";
    if (startTime && endTime && startTime >= endTime) { alert("结束时间需要晚于开始时间"); return; }
    const taskDate = document.getElementById("planDate").value || getDefaultPlanDate();
    const week = weekStartForDate(taskDate);
    const editId = document.getElementById("planEditId").value;
    const payload = { title, description: desc, start_time: startTime, end_time: endTime, week_start: week, task_date: taskDate };
    if (editId) await api(`/api/weekly-plans/${editId}`, "PUT", payload);
    else await api("/api/weekly-plans", "POST", payload);
    hidePlanForm();
    loadWeeklyPlans();
    loadDashboard();
}

async function togglePlanDone(id, currentStatus) {
    const newStatus = currentStatus === "done" ? "todo" : "done";
    await api(`/api/weekly-plans/${id}`, "PUT", { status: newStatus });
    loadWeeklyPlans();
    loadDashboard();
}

async function updatePlanStatus(id, status) {
    await api(`/api/weekly-plans/${id}`, "PUT", { status });
    loadWeeklyPlans();
    loadDashboard();
}

async function deletePlan(id) {
    if (!confirm("确认删除这个任务？")) return;
    await api(`/api/weekly-plans/${id}`, "DELETE");
    loadWeeklyPlans();
    loadDashboard();
}

function changeWeek(delta) {
    currentMonthOffset += delta;
    loadWeeklyPlans();
}

function goToCurrentMonth() {
    currentMonthOffset = 0;
    loadWeeklyPlans();
}

// ========== 日记 ==========
function setupMoodSelector() {
    document.querySelectorAll(".mood-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".mood-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            currentMood = parseInt(btn.dataset.mood);
        });
    });
}

async function loadJournal() {
    const data = await api("/api/journal");
    if (data.today) {
        document.getElementById("journalDid").value = data.today.did || "";
        document.getElementById("journalFeeling").value = data.today.feeling || "";
        document.getElementById("journalPlan").value = data.today.plan || "";
        currentMood = data.today.mood || 3;
        document.querySelectorAll(".mood-btn").forEach(b => {
            b.classList.toggle("active", parseInt(b.dataset.mood) === currentMood);
        });
        if (data.today.ima_synced) {
        document.getElementById("syncStatus").textContent = "已同步到 Obsidian";
            document.getElementById("syncStatus").className = "sync-status success";
        }
    }

    const historyEl = document.getElementById("journalHistory");
    if (!data.recent || data.recent.length === 0) {
        historyEl.innerHTML = `<p style="color:var(--text-muted);font-size:14px">还没有历史日记</p>`;
        return;
    }

    const moodEmoji = ["", "[低落]", "[一般]", "[平静]", "[不错]", "[很好]"];
    historyEl.innerHTML = data.recent.slice().reverse().map(entry => `
        <div class="journal-entry">
            <div class="journal-entry-date">${entry.date} ${moodEmoji[entry.mood || 3] || "[平静]"} ${entry.ima_synced ? "[已同步]" : ""}</div>
            ${entry.did ? `<div class="journal-entry-row"><strong>做了：</strong>${escapeHtml(entry.did)}</div>` : ""}
            ${entry.feeling ? `<div class="journal-entry-row"><strong>感受：</strong>${escapeHtml(entry.feeling)}</div>` : ""}
            ${entry.plan ? `<div class="journal-entry-row"><strong>计划：</strong>${escapeHtml(entry.plan)}</div>` : ""}
        </div>
    `).join("");
}

async function saveJournal() {
    const did = document.getElementById("journalDid").value.trim();
    const feeling = document.getElementById("journalFeeling").value.trim();
    const plan = document.getElementById("journalPlan").value.trim();
    if (!did && !feeling && !plan) { alert("至少写一行"); return; }
    await api("/api/journal", "POST", { did, feeling, plan, mood: currentMood });
    loadJournal();
    loadDashboard();
}

async function syncIma() {
    const btn = document.getElementById("btnSyncImaJournal");
    const status = document.getElementById("syncStatus");
    btn.disabled = true;
    status.textContent = "同步中...";
    status.className = "sync-status";
    try {
        const result = await api("/api/journal/sync-obsidian", "POST");
        if (result.ok) {
            status.textContent = "已同步到 Obsidian 笔记";
            status.className = "sync-status success";
        } else {
            status.textContent = "同步失败: " + (result.error || "未知错误");
            status.className = "sync-status error";
        }
    } catch (e) {
        status.textContent = "同步失败: " + e.message;
        status.className = "sync-status error";
    }
    btn.disabled = false;
}

// ========== 健康 ==========
async function loadHealth() {
    const data = await api("/api/health");
    document.getElementById("weekExerciseDays").textContent = data.week_days;
    if (data.today) {
        document.getElementById("exerciseDone").checked = data.today.exercise_done;
        document.getElementById("exerciseType").value = data.today.exercise_type || "";
        document.getElementById("exerciseMinutes").value = data.today.exercise_minutes || 0;
        document.getElementById("exerciseNote").value = data.today.note || "";
        updateExerciseLabel();
    }
    document.getElementById("exerciseDone").onchange = updateExerciseLabel;
}

function updateExerciseLabel() {
    document.getElementById("exerciseLabel").textContent =
        document.getElementById("exerciseDone").checked ? "已运动 [OK]" : "还没动";
}

async function saveHealth() {
    const data = {
        exercise_done: document.getElementById("exerciseDone").checked,
        exercise_type: document.getElementById("exerciseType").value.trim(),
        exercise_minutes: parseInt(document.getElementById("exerciseMinutes").value) || 0,
        note: document.getElementById("exerciseNote").value.trim()
    };
    await api("/api/health", "POST", data);
    loadHealth();
    loadDashboard();
}

// ========== 灵感流（本地合并 Obsidian 与抖音） ==========
async function loadInspirations() {
    const container = document.getElementById("inspirationGrid");
    if (!container) return;
    try {
        const data = await api("/api/idea-stream");
        ideaStreamState.items = data.items || [];
        ideaStreamState.visibleLimit = 12;
        const counts = data.counts || {};
        const allCount = document.getElementById("ideaCountAll");
        const imaCountFilter = document.getElementById("ideaCountIma");
        const douyinCountFilter = document.getElementById("ideaCountDouyin");
        const imaTabCount = document.getElementById("ideaTabImaCount");
        if (allCount) allCount.textContent = counts.all || 0;
        if (imaCountFilter) imaCountFilter.textContent = counts.ima || 0;
        if (douyinCountFilter) douyinCountFilter.textContent = counts.douyin || 0;
        if (imaTabCount) imaTabCount.textContent = counts.ima || 0;
        douyinIdeaState.recentCount = Number(counts.douyin || 0);
        const imaCount = document.getElementById("imaSourceCount");
        const imaStatus = document.getElementById("imaSourceStatus");
        if (imaCount) imaCount.textContent = Number(counts.ima || 0);
        if (imaStatus) {
            imaStatus.textContent = "缓存可用";
            imaStatus.className = "idea-status-badge ok";
        }
        document.getElementById("ideaStreamNotice").textContent = data.notice || "";
        renderIdeaHighlights(ideaStreamState.items);
        renderIdeaStream();
        renderDashboardIdeas(ideaStreamState.items);
        await loadIdeaSourceStatus();
    } catch (e) {
        container.innerHTML = `<div class="empty-hint">本地灵感加载失败：${escapeHtml(e.message)}</div>`;
        await loadIdeaSourceStatus();
    }
}

function switchIdeaSource(source) {
    ideaUnifiedSource = source === "douyin" ? "douyin" : "ima";
    const isDouyin = ideaUnifiedSource === "douyin";
    const imaTab = document.getElementById("ideaTabIma");
    const douyinTab = document.getElementById("ideaTabDouyin");
    const imaPanel = document.getElementById("ideaPanelIma");
    const douyinPanel = document.getElementById("ideaPanelDouyin");
    if (imaTab) {
        imaTab.classList.toggle("active", !isDouyin);
        imaTab.setAttribute("aria-selected", String(!isDouyin));
    }
    if (douyinTab) {
        douyinTab.classList.toggle("active", isDouyin);
        douyinTab.setAttribute("aria-selected", String(isDouyin));
    }
    if (imaPanel) {
        imaPanel.classList.toggle("active", !isDouyin);
        imaPanel.hidden = isDouyin;
    }
    if (douyinPanel) {
        douyinPanel.classList.toggle("active", isDouyin);
        douyinPanel.hidden = !isDouyin;
    }
    if (isDouyin) {
        loadDouyinInspirations();
    } else {
        ideaStreamState.filter = "ima";
        loadInspirations();
    }
}

async function loadIdeaSourceStatus() {
    const statusBadge = document.getElementById("douyinSourceStatus");
    if (!statusBadge) return;
    const results = await Promise.allSettled([
        api("/api/douyin/check-login"),
        api("/api/douyin/status"),
        api("/api/douyin/stats"),
        api("/api/douyin/sync-status"),
        api("/api/douyin/analysis-status"),
    ]);
    const [loginResult, moduleResult, statsResult, syncResult, analysisResult] = results;
    douyinIdeaState.loggedIn = loginResult.status === "fulfilled" && Boolean(loginResult.value?.logged_in);
    douyinIdeaState.ready = moduleResult.status === "fulfilled" && moduleResult.value?.status === "ready";
    if (statsResult.status === "fulfilled" && statsResult.value?.ok) {
        douyinIdeaState.stats = {
            ...douyinIdeaState.stats,
            ...(statsResult.value.data || {}),
        };
    }
    const syncRunning = syncResult.status === "fulfilled" && syncResult.value?.status === "running";
    const analysisRunning = analysisResult.status === "fulfilled" && analysisResult.value?.status === "running";
    douyinIdeaState.busy = syncRunning || analysisRunning;
    renderIdeaSourceStatus(syncRunning, analysisRunning);
}

function renderIdeaSourceStatus(syncRunning = false, analysisRunning = false) {
    const stats = douyinIdeaState.stats || {};
    const favoriteCount = document.getElementById("douyinFavoriteCount");
    const pendingCount = document.getElementById("douyinPendingCount");
    const ideaTotal = document.getElementById("douyinIdeaTotal");
    const statusBadge = document.getElementById("douyinSourceStatus");
    const analyzeButton = document.getElementById("btnAnalyzeLocalDouyin");
    const syncButton = document.getElementById("btnSyncDouyinIdeas");
    if (favoriteCount) favoriteCount.textContent = Number(stats.total_favorites || 0);
    if (pendingCount) pendingCount.textContent = Number(stats.unanalyzed_count || 0);
    if (ideaTotal) ideaTotal.textContent = Number(stats.total_inspirations || 0);
    if (statusBadge) {
        if (!douyinIdeaState.ready) {
            statusBadge.textContent = "模块异常";
            statusBadge.className = "idea-status-badge error";
        } else if (douyinIdeaState.loggedIn) {
            statusBadge.textContent = "已登录";
            statusBadge.className = "idea-status-badge ok";
        } else {
            statusBadge.textContent = "需要登录";
            statusBadge.className = "idea-status-badge warn";
        }
    }
    if (analyzeButton) {
        const pending = Number(stats.unanalyzed_count || 0);
        analyzeButton.textContent = pending > 0 ? `分析本地新收藏（${pending}）` : "本地收藏已分析";
        analyzeButton.disabled = douyinIdeaState.busy || !douyinIdeaState.ready || pending <= 0;
    }
    if (syncButton) {
        syncButton.textContent = syncRunning ? "正在同步…" : "同步最新收藏";
        syncButton.disabled = douyinIdeaState.busy || !douyinIdeaState.ready;
    }
    if (analysisRunning) setIdeaProgress("正在分析本地收藏，完成后会自动刷新灵感流…", "running");
    if (syncRunning) setIdeaProgress("正在同步最新收藏，请保持工作台开启…", "running");
}

function setIdeaProgress(message = "", tone = "") {
    const progress = document.getElementById("douyinIdeaProgress");
    if (!progress) return;
    progress.textContent = message;
    progress.className = `idea-operation-progress${tone ? ` ${tone}` : ""}${message ? " show" : ""}`;
}

function waitForIdeaTask(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function refreshIdeaWorkspace(button) {
    const original = button?.innerHTML;
    if (button) {
        button.disabled = true;
        button.classList.add("is-loading");
    }
    try {
        await loadInspirations();
        setIdeaProgress("本地状态已刷新。", "success");
    } catch (e) {
        setIdeaProgress(`刷新失败：${e.message}`, "error");
    } finally {
        if (button) {
            button.disabled = false;
            button.classList.remove("is-loading");
            button.innerHTML = original;
        }
    }
}

async function analyzeLocalDouyinIdeas(button) {
    const pending = Number(douyinIdeaState.stats?.unanalyzed_count || 0);
    if (!douyinIdeaState.ready) {
        setIdeaProgress("抖音本地分析模块未就绪，请打开“登录与设置”检查环境。", "error");
        return;
    }
    if (pending <= 0) {
        setIdeaProgress("本地收藏已经分析完成，没有待处理内容。", "success");
        return;
    }
    douyinIdeaState.busy = true;
    renderIdeaSourceStatus(false, true);
    setIdeaProgress(`正在启动本地分析，本次最多处理 ${Math.min(20, pending)} 条；不会访问抖音账号。`, "running");
    try {
        const started = await api("/api/douyin/analyze", "POST", { max_videos: Math.min(20, pending) });
        if (!started?.ok) throw new Error(started?.error || "分析任务未能启动");
        for (let attempt = 0; attempt < 72; attempt += 1) {
            await waitForIdeaTask(2500);
            const state = await api("/api/douyin/analysis-status");
            if (state.status === "success") {
                douyinIdeaState.busy = false;
                setIdeaProgress(state.message || "分析完成，灵感流已更新。", "success");
                await loadInspirations();
                setIdeaStreamFilter("douyin");
                return;
            }
            if (state.status === "error") throw new Error(state.message || "本地分析失败");
            setIdeaProgress(`正在分析本地收藏… ${Math.min(99, Math.round(((attempt + 1) / 72) * 100))}%`, "running");
        }
        throw new Error("分析时间较长，任务可能仍在后台运行，请稍后点击“刷新本地状态”");
    } catch (e) {
        douyinIdeaState.busy = false;
        setIdeaProgress(`分析失败：${e.message}`, "error");
        await loadIdeaSourceStatus();
    }
}

async function syncDouyinIdeas(button) {
    if (!douyinIdeaState.ready) {
        setIdeaProgress("抖音同步模块未就绪，请先检查登录与设置。", "error");
        return;
    }
    if (!douyinIdeaState.loggedIn) {
        setIdeaProgress("登录状态已失效。请先点“登录与设置”，重新扫码后再同步。", "error");
        showSettings("douyin");
        return;
    }
    douyinIdeaState.busy = true;
    renderIdeaSourceStatus(true, false);
    setIdeaProgress("正在同步最新收藏。只有这次主动操作会访问抖音，请保持工作台开启。", "running");
    try {
        const started = await api("/api/douyin/sync", "POST", { max_count: 30 }, 15000);
        if (!started?.ok && started?.status !== "already_running") throw new Error(started?.error || "同步任务未能启动");
        for (let attempt = 0; attempt < 60; attempt += 1) {
            await waitForIdeaTask(3500);
            const state = await api("/api/douyin/sync-status", "GET", null, 10000);
            if (state.status === "success") {
                douyinIdeaState.busy = false;
                const count = Number(state.data?.count || 0);
                await loadInspirations();
                setIdeaProgress(`同步完成，本地已读取 ${count} 条收藏。新内容不会自动分析，请按需点击“分析本地新收藏”。`, "success");
                return;
            }
            if (state.status === "error") throw new Error(state.message || "同步失败");
            setIdeaProgress(`正在同步最新收藏… ${Math.min(99, Math.round(((attempt + 1) / 60) * 100))}%`, "running");
        }
        throw new Error("同步时间较长，任务可能仍在后台运行，请稍后点击“刷新本地状态”");
    } catch (e) {
        douyinIdeaState.busy = false;
        setIdeaProgress(`同步失败：${e.message}`, "error");
        await loadIdeaSourceStatus();
    }
}

function setIdeaStreamFilter(source) {
    ideaStreamState.filter = source;
    ideaStreamState.visibleLimit = 12;
    document.querySelectorAll(".idea-filter").forEach(button => button.classList.toggle("active", button.dataset.source === source));
    renderIdeaStream();
}

function setIdeaSearch(value) {
    ideaStreamState.query = String(value || "").trim().toLowerCase();
    ideaStreamState.visibleLimit = 12;
    renderIdeaStream();
}

function setIdeaSort(value) {
    ideaStreamState.sort = value || "newest";
    ideaStreamState.visibleLimit = 12;
    renderIdeaStream();
}

function loadMoreIdeas() {
    ideaStreamState.visibleLimit += 12;
    renderIdeaStream();
}

function ideaSummary(value, maxLength = 100) {
    return compactAssistantText(String(value || "").replace(/\s+/g, " "), maxLength);
}

function filteredIdeaItems() {
    const query = ideaStreamState.query;
    const items = ideaStreamState.items.filter(item => {
        if (ideaStreamState.filter !== "all" && item.source !== ideaStreamState.filter) return false;
        if (!query) return true;
        return `${item.title || ""} ${item.summary || ""} ${item.source_label || ""}`.toLowerCase().includes(query);
    });
    return items.sort((a, b) => {
        if (ideaStreamState.sort === "oldest") return String(a.time || "").localeCompare(String(b.time || ""));
        if (ideaStreamState.sort === "source") {
            return String(a.source || "").localeCompare(String(b.source || "")) || String(b.time || "").localeCompare(String(a.time || ""));
        }
        return String(b.time || "").localeCompare(String(a.time || ""));
    });
}

function ideaDateGroup(value) {
    const raw = String(value || "").slice(0, 10);
    if (!raw) return "更早";
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    if (raw === localDateString(today)) return "今天";
    if (raw === localDateString(yesterday)) return "昨天";
    const date = new Date(`${raw}T00:00:00`);
    const diff = Math.floor((new Date(localDateString(today)) - date) / 86400000);
    if (diff >= 0 && diff < 7) return "本周";
    return "更早";
}

function renderIdeaHighlights(items) {
    const container = document.getElementById("ideaHighlights");
    if (!container) return;
    const highlights = (items || []).slice(0, 3);
    container.innerHTML = highlights.length ? highlights.map((item, index) => `
        <button onclick="applyIdeaHighlight('${encodeURIComponent(item.title || "")}')">
            <span>0${index + 1}</span>
            <strong>${escapeHtml(compactAssistantText(item.title || "未命名灵感", 34))}</strong>
            <em>${escapeHtml(item.source_label || "")}</em>
        </button>
    `).join("") : `<p>还没有可整理的灵感。</p>`;
}

function applyIdeaHighlight(encodedTitle) {
    const title = decodeURIComponent(encodedTitle || "");
    const input = document.getElementById("ideaSearch");
    if (input) input.value = title;
    setIdeaStreamFilter("all");
    setIdeaSearch(title);
    document.getElementById("inspirationGrid")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderIdeaStream() {
    const container = document.getElementById("inspirationGrid");
    if (!container) return;
    const allItems = filteredIdeaItems();
    const items = allItems.slice(0, ideaStreamState.visibleLimit);
    const resultCount = document.getElementById("ideaResultCount");
    if (resultCount) resultCount.textContent = `${allItems.length} 条结果`;
    const loadMore = document.getElementById("ideaLoadMore");
    if (loadMore) {
        loadMore.style.display = allItems.length > items.length ? "block" : "none";
        loadMore.textContent = `再看 ${Math.min(12, allItems.length - items.length)} 条`;
    }
    if (!items.length) {
        const pending = Number(douyinIdeaState.stats?.unanalyzed_count || 0);
        const favorites = Number(douyinIdeaState.stats?.total_favorites || 0);
        if (ideaStreamState.filter === "douyin" && !ideaStreamState.query) {
            container.innerHTML = `
                <div class="idea-empty-state">
                    <span class="idea-empty-icon">抖</span>
                    <h3>近两天还没有新的抖音灵感</h3>
                    <p>本地已有 ${favorites} 条收藏，${pending} 条待分析。先分析本地内容，不会访问抖音账号。</p>
                    ${pending > 0 ? `<button class="idea-action-primary douyin" onclick="analyzeLocalDouyinIdeas(this)">分析本地新收藏（${pending}）</button>` : `<button class="btn-ghost" onclick="syncDouyinIdeas(this)">同步最新收藏</button>`}
                </div>`;
        } else if (ideaStreamState.query) {
            container.innerHTML = `
                <div class="idea-empty-state">
                    <h3>没有找到匹配内容</h3>
                    <p>可以清空关键词，或者切换到其他来源继续看。</p>
                    <button class="btn-ghost" onclick="clearIdeaSearch()">清空搜索</button>
                </div>`;
        } else {
            container.innerHTML = `
                <div class="idea-empty-state">
                    <h3>灵感流还是空的</h3>
                <p>先从上方同步 ima，或者同步并分析抖音收藏。</p>
                </div>`;
        }
        return;
    }
    const groups = new Map();
    items.forEach(item => {
        const label = ideaDateGroup(item.time);
        if (!groups.has(label)) groups.set(label, []);
        groups.get(label).push(item);
    });
    container.innerHTML = [...groups.entries()].map(([label, groupItems]) => `
        <section class="idea-date-group">
            <header><h3>${label}</h3><span>${groupItems.length} 条</span></header>
            <div class="idea-date-items">${groupItems.map(item => `
        <article class="idea-stream-item" data-source="${escapeHtml(item.source)}">
            <div class="idea-source ${escapeHtml(item.source)}">${escapeHtml(item.source_label)}</div>
                    <div class="idea-stream-copy" ${item.source === "ima" ? `onclick="viewNoteContent('${escapeHtml(item.original_id)}')" role="button" tabindex="0"` : ""}>
                <h3>${escapeHtml(item.title || "未命名灵感")}</h3>
                <p>${escapeHtml(ideaSummary(item.summary, 140) || "暂无摘要")}</p>
                <small>${escapeHtml((item.time || "").slice(0, 16))}</small>
            </div>
            <div class="idea-stream-actions">
                    ${item.source === "ima" && !item.imported ? `<button class="primary" onclick="importTopicFromIma('${escapeHtml(item.original_id)}')">转选题</button>` : ""}
                    ${item.source === "ima" && item.imported ? `<span>已进选题库</span>` : ""}
                ${item.source === "douyin" ? `<span>近两天分析</span>` : ""}
            </div>
        </article>
            `).join("")}</div>
        </section>
    `).join("");
}

function clearIdeaSearch() {
    const input = document.getElementById("ideaSearch");
    if (input) input.value = "";
    setIdeaSearch("");
}

function renderDashboardIdeas(items) {
    const container = document.getElementById("dashboardIdeaList");
    if (!container) return;
    const latest = (items || []).slice(0, 5);
    container.innerHTML = latest.length ? latest.map(item => `
        <button class="editorial-feed-item" onclick="switchTab('inspirations')">
            <span class="feed-source ${escapeHtml(item.source)}">${escapeHtml(item.source_label)}</span>
            <strong>${escapeHtml(compactAssistantText(item.title, 46))}</strong>
            <small>${escapeHtml((item.time || "").slice(5, 10))}</small>
        </button>
        `).join("") : `<div class="editorial-empty">还没有新灵感，先同步 ima 或抖音收藏。</div>`;
}

async function syncImaNotes() {
    const btn = document.getElementById("btnSyncIma");
    const status = document.getElementById("imaSyncStatus");
    btn.disabled = true;
    status.textContent = "正在同步 ima…";
    status.className = "idea-action-status running";
    try {
        const result = await api("/api/ima/notes", "GET", null, 130000);
        if (result.ok) {
            const count = (result.notes || []).length;
            status.textContent = `同步完成，共 ${count} 条`;
            status.className = "idea-action-status success";
            await loadInspirations();
        } else {
            status.textContent = "同步失败: " + (result.error || "未知错误");
            status.className = "idea-action-status error";
        }
    } catch (e) {
        status.textContent = "同步失败: " + e.message;
        status.className = "idea-action-status error";
    } finally {
        btn.disabled = false;
    }
}

async function viewNoteContent(noteId) {
    const modal = document.getElementById("noteModal");
    const titleEl = document.getElementById("noteModalTitle");
    const contentEl = document.getElementById("noteModalContent");
    const metaEl = document.getElementById("noteModalMeta");
    const searchBox = document.getElementById("noteFileSearch");
    const searchInput = document.getElementById("noteFileSearchInput");
    const searchResult = document.getElementById("noteFileSearchResult");

    currentImaReaderNoteId = noteId;
    currentImaReaderTitle = "";
    titleEl.textContent = "加载中...";
    contentEl.textContent = "";
    metaEl.textContent = "";
    searchBox.style.display = "none";
    searchInput.value = "";
    searchResult.textContent = "";
    document.getElementById("noteImaOpenStatus").textContent = "";
    modal.style.display = "flex";

    try {
        const result = await api(`/api/ima/note/${noteId}`, "GET", null, 60000);
        if (result.ok) {
            titleEl.textContent = result.title || "ima 内容";
            currentImaReaderTitle = result.title || "";
            const typeLabels = { 1: "PDF", 2: "网页", 3: "Word", 4: "PPT", 5: "表格", 6: "微信文章", 7: "Markdown", 9: "图片", 11: "ima 笔记", 13: "文本" };
            metaEl.textContent = [result.knowledge_base, typeLabels[result.media_type] || "ima 内容"].filter(Boolean).join(" · ");
            if (result.content_mode === "full") {
                contentEl.textContent = result.content || "(空笔记)";
            } else {
                contentEl.textContent = result.message || "这是一份 ima 文件，可按关键词查看其中的匹配段落。";
                document.getElementById("noteFileSearchHint").textContent = "输入你记得的词；如果 ima 返回了匹配段落，会直接显示在这里。";
                searchBox.style.display = "block";
                setTimeout(() => searchInput.focus(), 60);
            }
        } else {
            titleEl.textContent = "加载失败";
            contentEl.textContent = result.error || "未知错误";
        }
    } catch (e) {
        titleEl.textContent = "加载失败";
        contentEl.textContent = e.message;
    }
}

async function searchImaFileContent() {
    const input = document.getElementById("noteFileSearchInput");
    const resultEl = document.getElementById("noteFileSearchResult");
    const query = input.value.trim();
    if (!currentImaReaderNoteId || !query) {
        resultEl.textContent = "先输入一个关键词。";
        return;
    }
    resultEl.textContent = "正在搜索这份文件…";
    try {
        const result = await api(`/api/ima/note/${currentImaReaderNoteId}/search?q=${encodeURIComponent(query)}`, "GET", null, 60000);
        resultEl.textContent = result.excerpt || result.message || "没有找到包含这个词的段落。";
        resultEl.classList.toggle("is-empty", !result.excerpt);
    } catch (error) {
        resultEl.textContent = error.message;
        resultEl.classList.add("is-empty");
    }
}

async function openImaOriginal() {
    const status = document.getElementById("noteImaOpenStatus");
    try {
        if (currentImaReaderTitle && navigator.clipboard) {
            await navigator.clipboard.writeText(currentImaReaderTitle).catch(() => {});
        }
        const result = await api("/api/local/open-ima", "POST", {});
        status.textContent = result.ok
            ? (currentImaReaderTitle ? "已打开 ima，文件名已复制，可直接搜索。" : "已打开 ima。")
            : (result.error || "打开失败");
    } catch (error) {
        status.textContent = error.message;
    }
}

function closeNoteModal() {
    document.getElementById("noteModal").style.display = "none";
    currentImaReaderNoteId = "";
    currentImaReaderTitle = "";
}

async function importTopicFromIma(noteId) {
    const accountName = CONTENT_ACCOUNTS[currentContentAccount].fullLabel;
    if (!confirm(`把这条 ima 灵感加入${accountName}的选题库？`)) return;
    const result = await api("/api/ima/import-topic", "POST", { note_id: noteId, account_key: currentContentAccount });
    if (result.ok) {
        loadInspirations();
        loadTopics();
        loadDashboard();
    } else {
        alert("导入失败: " + (result.error || "未知错误"));
    }
}

// ========== 个人档案 ==========
const MEMORY_CATEGORY_LABELS = {
    identity: "基础信息",
    experience: "过往经历",
    value: "观点与价值观",
    preference: "喜好与表达偏好",
    goal: "目标与当前阶段",
    boundary: "边界与禁区",
    memo: "备忘录",
};
const MEMORY_SCOPE_LABELS = { all: "所有内容", main: "仅大号", vlog: "仅小号 Vlog" };

async function loadPersonalMemories() {
    const params = new URLSearchParams();
    params.set("type", personalMemoryState.view);
    if (personalMemoryState.scope !== "all") params.set("scope", personalMemoryState.scope);
    if (personalMemoryState.query) params.set("q", personalMemoryState.query);
    const grid = document.getElementById("memoryGrid");
    if (!grid) return;
    try {
        const result = await api(`/api/personal-memories?${params.toString()}`);
        personalMemoryState.items = result.items || [];
        document.getElementById("memoryStatAbout").textContent = result.stats?.about_count || 0;
        document.getElementById("memoryStatMemo").textContent = result.stats?.memo_count || 0;
        document.getElementById("memoryStatPrivate").textContent = result.stats?.private_count || 0;
        updateLibraryViewUI();
        renderPersonalMemories();
    } catch (error) {
        grid.innerHTML = `<div class="memory-empty error">资料读取失败：${escapeHtml(error.message)}</div>`;
    }
}

function renderPersonalMemories() {
    const grid = document.getElementById("memoryGrid");
    if (!grid) return;
    if (!personalMemoryState.items.length) {
        const label = personalMemoryState.view === "about" ? "关于我" : "备忘录";
        grid.innerHTML = `<div class="memory-empty"><span>暂时没有符合条件的${label}</span><button onclick="openMemoryForm('', '${personalMemoryState.view}')">+ 新建第一条</button></div>`;
        return;
    }
    grid.innerHTML = personalMemoryState.items.map(item => {
        const tags = (item.tags || []).map(tag => `<span>#${escapeHtml(tag)}</span>`).join("");
        const migrated = item.source === "profile_migration" ? `<em>来自原个人资料</em>` : "";
        const revealed = personalMemoryState.revealed.get(item.id);
        const displayContent = item.is_private ? (revealed || item.content_preview) : item.content;
        const icon = item.is_private ? "锁" : (item.item_type === "memo" ? "记" : (item.category_label || "资").slice(0, 1));
        const kind = item.item_type === "memo" ? (item.is_private ? "私密备忘" : "备忘录") : (item.category_label || "关于我");
        const aiControl = item.is_private
            ? `<span class="memory-ai-pill private">禁止 AI</span>`
            : `<button class="memory-ai-pill ${item.ai_enabled ? "enabled" : "local"}" onclick="toggleMemoryAI('${item.id}', ${item.ai_enabled ? "false" : "true"})">${item.ai_enabled ? "AI 可用" : "仅本地"}</button>`;
        const memoActions = item.item_type === "memo"
            ? `${item.is_private ? `<button class="memory-edit-button" onclick="togglePrivateMemory('${item.id}')">${revealed ? "隐藏" : "查看"}</button>` : ""}<button class="memory-edit-button" onclick="copyMemory('${item.id}', this)">复制</button>`
            : "";
        return `<article class="memory-card category-${escapeHtml(item.category)} ${item.item_type === "memo" ? "memo-card" : "about-card"} ${item.is_private ? "private-card" : ""} ${item.pinned ? "pinned" : ""}">
            <header>
                <div><span class="memory-category-mark">${escapeHtml(icon)}</span><div><small>${escapeHtml(kind)}</small><h3>${escapeHtml(item.title)}</h3></div></div>
                ${item.pinned ? `<b title="重要资料">★</b>` : ""}
            </header>
            <p class="${item.is_private && !revealed ? "private-mask" : ""}">${escapeHtml(displayContent)}</p>
            <div class="memory-card-tags">${tags}</div>
            <footer>
                <div><span class="memory-scope-pill ${escapeHtml(item.account_scope)}">${escapeHtml(MEMORY_SCOPE_LABELS[item.account_scope] || "所有内容")}</span>${item.memory_date ? `<time>${escapeHtml(item.memory_date)}</time>` : ""}${migrated}</div>
                <div>${aiControl}${memoActions}<button class="memory-edit-button" onclick="openMemoryForm('${item.id}')">编辑</button></div>
            </footer>
        </article>`;
    }).join("");
}

function setLibraryView(view) {
    personalMemoryState.view = view === "memo" ? "memo" : "about";
    personalMemoryState.scope = "all";
    localStorage.setItem("personal_library_view", personalMemoryState.view);
    const scope = document.getElementById("memoryScopeFilter");
    if (scope) scope.value = "all";
    loadPersonalMemories();
}

function updateLibraryViewUI() {
    document.querySelectorAll("[data-library-view]").forEach(button => button.classList.toggle("active", button.dataset.libraryView === personalMemoryState.view));
    const search = document.getElementById("memorySearch");
    const scope = document.getElementById("memoryScopeFilter");
    if (personalMemoryState.view === "about") {
        search.placeholder = "搜索背景、偏好或目标";
        scope.style.display = "";
    } else {
        search.placeholder = "搜索备忘录标题、内容或标签";
        scope.style.display = "none";
    }
}

function setMemoryScope(scope) {
    personalMemoryState.scope = scope;
    loadPersonalMemories();
}

function setMemorySearch(value) {
    personalMemoryState.query = String(value || "").trim();
    clearTimeout(personalMemoryState.searchTimer);
    personalMemoryState.searchTimer = setTimeout(loadPersonalMemories, 260);
}

async function openMemoryForm(memoryId = "", requestedType = "") {
    const item = personalMemoryState.items.find(memory => memory.id === memoryId);
    const itemType = item?.item_type || (requestedType === "memo" ? "memo" : requestedType === "about" ? "about" : personalMemoryState.view);
    document.getElementById("memoryEditId").value = item?.id || "";
    document.getElementById("memoryTitle").value = item?.title || "";
    document.getElementById("memoryContent").value = item?.is_private ? "" : (item?.content || "");
    document.getElementById("memoryCategory").value = item?.category === "memo" ? "experience" : (item?.category || "experience");
    document.getElementById("memoryAccountScope").value = item?.account_scope || "all";
    document.getElementById("memoryDate").value = item?.memory_date || "";
    document.getElementById("memoryTags").value = (item?.tags || []).join("，");
    document.getElementById("memoryAIEnabled").checked = item ? !!item.ai_enabled : itemType === "about";
    document.getElementById("memoryPrivate").checked = !!item?.is_private;
    document.getElementById("memoryPinned").checked = !!item?.pinned;
    document.getElementById("memoryModalTitle").textContent = item ? (itemType === "about" ? "编辑关于我" : "编辑备忘录") : (itemType === "about" ? "补充关于我" : "新建备忘录");
    document.getElementById("memoryDeleteButton").style.display = item ? "" : "none";
    document.getElementById("memoryFormStatus").textContent = "";
    document.getElementById("memoryModal").style.display = "flex";
    document.querySelectorAll("[data-memory-form-type]").forEach(button => { button.disabled = !!item; });
    setMemoryFormType(itemType, true);
    if (item?.is_private) {
        const status = document.getElementById("memoryFormStatus");
        status.textContent = "正在用当前 Windows 账户解锁…";
        try {
            const result = await api(`/api/personal-memories/${item.id}/private-content`, "POST");
            document.getElementById("memoryContent").value = result.content || "";
            status.textContent = "已在本次编辑中临时解锁，关闭窗口后会再次隐藏。";
        } catch (error) {
            status.textContent = "解锁失败：" + error.message;
        }
    }
    setTimeout(() => document.getElementById(item ? "memoryContent" : "memoryTitle")?.focus(), 40);
}

function setMemoryFormType(type, preserve = false) {
    type = type === "memo" ? "memo" : "about";
    document.getElementById("memoryItemType").value = type;
    document.querySelectorAll("[data-memory-form-type]").forEach(button => button.classList.toggle("active", button.dataset.memoryFormType === type));
    document.getElementById("memoryAboutFields").style.display = type === "about" ? "grid" : "none";
    document.getElementById("memoryPrivateOption").style.display = type === "memo" ? "flex" : "none";
    document.getElementById("memoryModalSubtitle").textContent = type === "about"
        ? "记录背景、喜好、偏好与目标，让 AI 回答时更贴合你。"
        : "保存常用内容或临时记录；私密备忘始终不会提供给 AI。";
    if (!preserve) {
        document.getElementById("memoryPrivate").checked = false;
        document.getElementById("memoryAIEnabled").checked = type === "about";
    }
    syncMemoryPrivacyState();
}

function syncMemoryPrivacyState() {
    const isMemo = document.getElementById("memoryItemType").value === "memo";
    const isPrivate = isMemo && document.getElementById("memoryPrivate").checked;
    const ai = document.getElementById("memoryAIEnabled");
    if (isPrivate) ai.checked = false;
    ai.disabled = isPrivate;
    ai.closest("label")?.classList.toggle("disabled", isPrivate);
}

function closeMemoryForm() {
    const modal = document.getElementById("memoryModal");
    if (modal) modal.style.display = "none";
}

async function saveMemory() {
    const id = document.getElementById("memoryEditId").value;
    const status = document.getElementById("memoryFormStatus");
    const button = document.getElementById("memorySaveButton");
    const payload = {
        item_type: document.getElementById("memoryItemType").value,
        title: document.getElementById("memoryTitle").value.trim(),
        content: document.getElementById("memoryContent").value.trim(),
        category: document.getElementById("memoryCategory").value,
        account_scope: document.getElementById("memoryItemType").value === "about" ? document.getElementById("memoryAccountScope").value : "all",
        memory_date: document.getElementById("memoryItemType").value === "about" ? document.getElementById("memoryDate").value : "",
        tags: document.getElementById("memoryTags").value,
        ai_enabled: document.getElementById("memoryAIEnabled").checked,
        is_private: document.getElementById("memoryPrivate").checked,
        pinned: document.getElementById("memoryPinned").checked,
    };
    if (!payload.content) { status.textContent = "请先写下要记住的内容。"; return; }
    button.disabled = true;
    status.textContent = "正在保存到本机…";
    try {
        const result = await api(id ? `/api/personal-memories/${id}` : "/api/personal-memories", id ? "PUT" : "POST", payload);
        if (!result.ok) throw new Error(result.error || "保存失败");
        closeMemoryForm();
        personalMemoryState.view = payload.item_type;
        localStorage.setItem("personal_library_view", personalMemoryState.view);
        await loadPersonalMemories();
    } catch (error) {
        status.textContent = "保存失败：" + error.message;
    } finally {
        button.disabled = false;
    }
}

async function deleteMemoryFromForm() {
    const id = document.getElementById("memoryEditId").value;
    if (!id || !confirm("确认删除这条资料？删除后无法恢复。")) return;
    await api(`/api/personal-memories/${id}`, "DELETE");
    closeMemoryForm();
    await loadPersonalMemories();
}

async function toggleMemoryAI(id, enabled) {
    await api(`/api/personal-memories/${id}`, "PUT", { ai_enabled: enabled });
    await loadPersonalMemories();
}

async function getMemoryPlainContent(item) {
    if (!item?.is_private) return item?.content || "";
    const result = await api(`/api/personal-memories/${item.id}/private-content`, "POST");
    return result.content || "";
}

async function togglePrivateMemory(id) {
    if (personalMemoryState.revealed.has(id)) {
        personalMemoryState.revealed.delete(id);
        clearTimeout(personalMemoryState.revealTimers.get(id));
        personalMemoryState.revealTimers.delete(id);
        renderPersonalMemories();
        return;
    }
    const item = personalMemoryState.items.find(memory => memory.id === id);
    try {
        const content = await getMemoryPlainContent(item);
        personalMemoryState.revealed.set(id, content);
        clearTimeout(personalMemoryState.revealTimers.get(id));
        personalMemoryState.revealTimers.set(id, setTimeout(() => {
            personalMemoryState.revealed.delete(id);
            personalMemoryState.revealTimers.delete(id);
            renderPersonalMemories();
        }, 30000));
        renderPersonalMemories();
    } catch (error) {
        alert("无法查看私密备忘：" + error.message);
    }
}

async function copyMemory(id, button) {
    const item = personalMemoryState.items.find(memory => memory.id === id);
    try {
        const content = await getMemoryPlainContent(item);
        await navigator.clipboard.writeText(content);
        const oldText = button.textContent;
        button.textContent = "已复制";
        setTimeout(() => { button.textContent = oldText; }, 1600);
        if (item?.is_private) {
            setTimeout(async () => {
                try {
                    const current = await navigator.clipboard.readText();
                    if (current === content) await navigator.clipboard.writeText("");
                } catch (_) {}
            }, 60000);
        }
    } catch (error) {
        alert("复制失败：" + error.message);
    }
}

function previewMemoryContext() {
    document.getElementById("memoryPreviewQuery").value = "";
    document.getElementById("memoryPreviewScope").value = "all";
    document.getElementById("memoryPreviewResult").innerHTML = `<div class="memory-preview-empty">输入一个选题或写作任务，看看 AI 会找到哪些资料。</div>`;
    document.getElementById("memoryPreviewModal").style.display = "flex";
}

function closeMemoryPreview() {
    const modal = document.getElementById("memoryPreviewModal");
    if (modal) modal.style.display = "none";
}

async function runMemoryContextPreview() {
    const query = document.getElementById("memoryPreviewQuery").value.trim();
    const scope = document.getElementById("memoryPreviewScope").value;
    const resultEl = document.getElementById("memoryPreviewResult");
    resultEl.innerHTML = `<div class="memory-preview-empty">正在匹配资料…</div>`;
    try {
        const result = await api(`/api/personal-memories/context-preview?q=${encodeURIComponent(query)}&account=${encodeURIComponent(scope)}`);
        if (!result.items?.length) {
            resultEl.innerHTML = `<div class="memory-preview-empty">没有找到相关且允许 AI 使用的资料。</div>`;
            return;
        }
        resultEl.innerHTML = `<strong>本次会优先读取 ${result.items.length} 条</strong>` + result.items.map(item => `<article><span>${item.item_type === "memo" ? "备忘录" : escapeHtml(item.category_label)}</span><h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.content)}</p></article>`).join("");
    } catch (error) {
        resultEl.innerHTML = `<div class="memory-preview-empty error">匹配失败：${escapeHtml(error.message)}</div>`;
    }
}

// ========== 设置 ==========
const NEWS_SOURCES = ["新华网","中国政府网","澎湃新闻","新浪财经","36氪","量子位","BBC World","UN News","The Guardian","Nature News","MIT News AI","TechCrunch","The Verge","少数派","IT之家","百度热搜","今日头条","B站热搜","知乎热榜","GitHub Trending","Product Hunt"];
let loadedSettings = null;

async function showSettings(initialTab = "profile") {
    const modal = document.getElementById("settingsModal");
    const status = document.getElementById("settingsStatus");
    modal.style.display = "flex";
    status.textContent = "";
    try {
        const response = await api("/api/settings"); loadedSettings = response.config;
        const c = loadedSettings, p = c.profile || {};
        const fields = {cfgAppName:c.branding?.app_name,cfgAssistantName:c.branding?.assistant_name,cfgDisplayName:p.display_name,cfgOccupation:p.occupation,cfgBackground:p.background,cfgDirection:p.content_direction,cfgAudience:p.target_audience,cfgStrengths:p.strengths,cfgTone:p.tone,cfgInterests:p.interests,cfgAvoid:p.avoid_topics,cfgAiUrl:c.ai?.base_url,cfgAiModel:c.ai?.model,cfgImaClient:c.ima?.client_id,cfgObsidianVault:c.obsidian?.vault_path,cfgObsidianRoots:(c.obsidian?.allowed_roots||[]).join(","),cfgDouyinHour:c.douyin?.sync_hour,cfgDigestCount:c.news?.digest_count};
        Object.entries(fields).forEach(([id,value]) => { const el=document.getElementById(id); if(el) el.value=value ?? ""; });
        document.getElementById("cfgImaEnabled").checked=!!c.ima?.enabled;
        document.getElementById("cfgObsidianEnabled").checked=!!c.obsidian?.enabled;
        document.getElementById("cfgKnowledgeEnabled").checked=c.knowledge?.enabled !== false;
        document.getElementById("cfgKnowledgeWiki").checked=c.knowledge?.include_wiki !== false;
        document.getElementById("cfgKnowledgeCreative").checked=c.knowledge?.include_creative !== false;
        document.getElementById("cfgKnowledgeRaw").checked=c.knowledge?.include_raw !== false;
        document.getElementById("cfgDouyinAuto").checked=!!c.douyin?.auto_sync;
        document.getElementById("cfgNewsAuto").checked=!!c.news?.auto_refresh;
        document.getElementById("deepseekApiKeyInput").placeholder=c.ai?.has_api_key ? "已配置，留空则保留" : "请输入 API Key";
        document.getElementById("cfgImaApiKey").placeholder=c.ima?.has_api_key ? "已配置，留空则保留" : "请输入 ima API Key";
        document.getElementById("localDataPath").textContent=c.paths?.root || "";
        const enabled=new Set(c.news?.enabled_sources || []);
        let availableSources = NEWS_SOURCES;
        try {
            const sourceResponse = await api("/api/hotspots/sources");
            availableSources = (sourceResponse.sources || []).filter(source => source.mode === "auto").map(source => source.name);
        } catch (_) {}
        document.getElementById("newsSourceChecks").innerHTML=availableSources.map(x=>`<label><input type="checkbox" value="${escapeHtml(x)}" ${enabled.has(x)?"checked":""}>${escapeHtml(x)}</label>`).join("");
        await loadAccountSettingsEditor();
        checkDouyinConfigState();
        switchSettingsTab(initialTab);
    } catch(e) { status.textContent="设置读取失败："+e.message; }
}

function switchSettingsTab(name) {
    document.querySelectorAll(".settings-tab").forEach(x=>x.classList.toggle("active",x.dataset.settings===name));
    document.querySelectorAll(".settings-panel").forEach(x=>x.classList.toggle("active",x.dataset.panel===name));
}

function closeSettings() {
    document.getElementById("settingsModal").style.display = "none";
}

function accountFieldId(accountId, field) { return `account-${accountId}-${field}`; }

function renderAccountSettingsEditor(account) {
    const metric = account.latest_metric || {};
    const audience = metric.audience || {};
    const work = account.latest_works?.[0] || {};
    const platformName = account.platform === "xiaohongshu" ? "小红书" : "抖音";
    const sourceOptions = [
        ["manual", "手动快速更新（稳定）"],
        ["manual_csv", "CSV / 后台数据导入"],
        ["official_api", "官方 API（取得权限后）"],
    ];
    const f = (field) => accountFieldId(account.id, field);
    return `<details class="account-config-card" ${account.id === "douyin_main" ? "open" : ""}>
        <summary>
            <div class="config-account-identity">
                ${renderAccountAvatar(account, "config-avatar")}
                <div><span>${platformName} · ${escapeHtml(account.account_role)}</span><strong>${escapeHtml(account.nickname || "待配置账号")}</strong><small>${account.setup_complete ? "基础资料已完成" : "还需填写昵称和账号 ID"}</small></div>
            </div>
            <div class="config-account-state ${metric.snapshot_date ? "ready" : ""}">${metric.snapshot_date ? `数据 ${escapeHtml(metric.snapshot_date.slice(5))}` : "待录入"}</div>
        </summary>
        <div class="account-config-body">
            <div class="account-config-section account-profile-section">
                <div class="avatar-picker-wrap">
                    <label class="avatar-picker" for="${f("avatar")}">${renderAccountAvatar(account, "avatar-picker-preview")}<span>更换头像</span></label>
                    <input id="${f("avatar")}" type="file" accept="image/jpeg,image/png,image/webp" hidden onchange="changeAccountAvatar('${account.id}', this)">
                    <small>JPG / PNG / WebP，最大 3MB</small>
                </div>
                <div class="account-form-grid">
                    <label>账号昵称<input id="${f("nickname")}" class="input" value="${escapeHtml(account.nickname || "")}" placeholder="平台显示的昵称"></label>
                    <label>账号 ID / 小红书号<input id="${f("handle")}" class="input" value="${escapeHtml(account.handle || "")}" placeholder="用于区分账号"></label>
                    <label class="full">主页地址<input id="${f("profile_url")}" class="input" value="${escapeHtml(account.profile_url || "")}" placeholder="https://..."></label>
                    <label>账号职责<input id="${f("positioning")}" class="input" value="${escapeHtml(account.positioning || "")}" placeholder="如：品牌主阵地"></label>
                    <label>目标受众<input id="${f("target_audience")}" class="input" value="${escapeHtml(account.target_audience || "")}" placeholder="这个账号主要说给谁听"></label>
                    <label class="full">内容目标<textarea id="${f("content_goal")}" class="textarea compact" placeholder="这个账号今年最重要的目标">${escapeHtml(account.content_goal || "")}</textarea></label>
                    <label class="full">数据来源<select id="${f("data_source")}" class="input">${sourceOptions.map(([value,label])=>`<option value="${value}" ${account.data_source===value?"selected":""}>${label}</option>`).join("")}</select></label>
                </div>
            </div>
            <div class="account-config-section">
                <div class="config-section-title"><div><span>01</span><h5>今日账号数据</h5></div><small>同一天重复保存会覆盖当天快照</small></div>
                <div class="metric-input-grid">
                    <label>数据日期<input id="${f("snapshot_date")}" type="date" class="input" value="${escapeHtml(metric.snapshot_date || localDateString())}"></label>
                    <label>粉丝量<input id="${f("followers")}" type="number" min="0" class="input" value="${metric.followers ?? ""}" placeholder="必填"></label>
                    <label>较上次变化<input id="${f("followers_delta")}" type="number" class="input" value="${metric.followers_delta ?? ""}" placeholder="如 128 或 -12"></label>
                    <label>获赞 / 收藏总量<input id="${f("total_likes")}" type="number" min="0" class="input" value="${metric.total_likes ?? ""}"></label>
                    <label>累计作品<input id="${f("works_count")}" type="number" min="0" class="input" value="${metric.works_count ?? ""}"></label>
                    <label>近 7 天播放<input id="${f("views_7d")}" type="number" min="0" class="input" value="${metric.views_7d ?? ""}"></label>
                </div>
                <div class="audience-input-grid">
                    <label>年龄重点<input id="${f("audience_age")}" class="input" value="${escapeHtml(audience.age || "")}" placeholder="如：25–34 岁 46%"></label>
                    <label>性别重点<input id="${f("audience_gender")}" class="input" value="${escapeHtml(audience.gender || "")}" placeholder="如：女性 62%"></label>
                    <label>核心地区<input id="${f("audience_region")}" class="input" value="${escapeHtml(audience.region || "")}" placeholder="如：广东、上海、北京"></label>
                    <label>兴趣关键词<input id="${f("audience_interests")}" class="input" value="${escapeHtml(audience.interests || "")}" placeholder="如：AI、效率、职场"></label>
                </div>
            </div>
            <div class="account-config-section">
                <div class="config-section-title"><div><span>02</span><h5>最新作品</h5></div><small>用于计算该账号的选题方向</small></div>
                <div class="work-input-grid">
                    <label class="work-title-input">作品标题<input id="${f("work_title")}" class="input" value="${escapeHtml(work.title || "")}" placeholder="最新发布的作品"></label>
                    <label>发布日期<input id="${f("work_date")}" type="date" class="input" value="${escapeHtml((work.published_at || "").slice(0,10) || localDateString())}"></label>
                    <label>播放 / 阅读<input id="${f("work_views")}" type="number" min="0" class="input" value="${work.views ?? ""}"></label>
                    <label>点赞<input id="${f("work_likes")}" type="number" min="0" class="input" value="${work.likes ?? ""}"></label>
                    <label>评论<input id="${f("work_comments")}" type="number" min="0" class="input" value="${work.comments ?? ""}"></label>
                    <label>分享<input id="${f("work_shares")}" type="number" min="0" class="input" value="${work.shares ?? ""}"></label>
                    <label>收藏<input id="${f("work_saves")}" type="number" min="0" class="input" value="${work.saves ?? ""}"></label>
                </div>
            </div>
            <div class="account-config-actions"><span id="${f("status")}" class="settings-status"></span><button class="btn-primary" onclick="saveSingleAccount('${account.id}')">保存这个账号</button></div>
        </div>
    </details>`;
}

async function loadAccountSettingsEditor() {
    const container = document.getElementById("accountSettingsList");
    if (!container) return;
    try {
        const response = await api("/api/accounts");
        creatorAccounts = response.accounts || [];
        container.innerHTML = creatorAccounts.map(renderAccountSettingsEditor).join("");
    } catch (error) {
        container.innerHTML = `<div class="matrix-error">账号配置读取失败：${escapeHtml(error.message)}</div>`;
    }
}

function changeAccountAvatar(accountId, input) {
    const file = input.files?.[0];
    if (!file) return;
    if (file.size > 3 * 1024 * 1024) { alert("头像不能超过 3MB"); input.value = ""; return; }
    const reader = new FileReader();
    reader.onload = () => {
        pendingAccountAvatars[accountId] = reader.result;
        const preview = input.closest(".avatar-picker-wrap")?.querySelector(".avatar-picker-preview");
        if (preview) preview.innerHTML = `<img src="${reader.result}" alt="头像预览">`;
    };
    reader.readAsDataURL(file);
}

function accountPayloadFromForm(accountId) {
    const get = field => document.getElementById(accountFieldId(accountId, field));
    const value = field => get(field)?.value?.trim() || "";
    const payload = {
        nickname: value("nickname"), handle: value("handle"), profile_url: value("profile_url"),
        positioning: value("positioning"), target_audience: value("target_audience"),
        content_goal: value("content_goal"), data_source: value("data_source"),
    };
    if (pendingAccountAvatars[accountId]) payload.avatar_data = pendingAccountAvatars[accountId];
    if (value("followers") !== "") payload.metric = {
        snapshot_date: value("snapshot_date"), followers: value("followers"), followers_delta: value("followers_delta"),
        total_likes: value("total_likes"), works_count: value("works_count"), views_7d: value("views_7d"),
        audience: {age:value("audience_age"),gender:value("audience_gender"),region:value("audience_region"),interests:value("audience_interests")},
    };
    if (value("work_title")) payload.latest_work = {
        title:value("work_title"), published_at:value("work_date"), views:value("work_views"), likes:value("work_likes"),
        comments:value("work_comments"), shares:value("work_shares"), saves:value("work_saves"),
    };
    return payload;
}

async function saveSingleAccount(accountId, silent = false) {
    const status = document.getElementById(accountFieldId(accountId, "status"));
    if (status) status.textContent = "正在保存…";
    try {
        await api(`/api/accounts/${encodeURIComponent(accountId)}`, "PUT", accountPayloadFromForm(accountId));
        delete pendingAccountAvatars[accountId];
        if (status) status.textContent = "已保存到本机";
        if (!silent) { await loadAccountMatrix(); setTimeout(()=>{ if(status) status.textContent=""; }, 1800); }
        return true;
    } catch (error) {
        if (status) status.textContent = "保存失败：" + error.message;
        if (!silent) alert("账号保存失败：" + error.message);
        return false;
    }
}

async function saveAllAccountSettings() {
    if (!document.getElementById("accountSettingsList")) return true;
    for (const account of creatorAccounts) {
        if (!await saveSingleAccount(account.id, true)) return false;
    }
    await loadAccountMatrix();
    return true;
}

async function saveSettings() {
    const key = document.getElementById("deepseekApiKeyInput").value.trim();
    const status = document.getElementById("settingsStatus");
    const payload={branding:{app_name:v("cfgAppName")||"创作者工作台",assistant_name:v("cfgAssistantName")||"小助手"},profile:{display_name:v("cfgDisplayName"),occupation:v("cfgOccupation"),background:v("cfgBackground"),content_direction:v("cfgDirection"),target_audience:v("cfgAudience"),strengths:v("cfgStrengths"),tone:v("cfgTone"),interests:v("cfgInterests"),avoid_topics:v("cfgAvoid")},ai:{base_url:v("cfgAiUrl"),model:v("cfgAiModel")},ima:{enabled:document.getElementById("cfgImaEnabled").checked,client_id:v("cfgImaClient")},obsidian:{enabled:document.getElementById("cfgObsidianEnabled").checked,vault_path:v("cfgObsidianVault"),allowed_roots:v("cfgObsidianRoots").split(/[,，]/).map(x=>x.trim()).filter(Boolean)},knowledge:{enabled:document.getElementById("cfgKnowledgeEnabled").checked,include_wiki:document.getElementById("cfgKnowledgeWiki").checked,include_creative:document.getElementById("cfgKnowledgeCreative").checked,include_raw:document.getElementById("cfgKnowledgeRaw").checked},douyin:{auto_sync:document.getElementById("cfgDouyinAuto").checked,sync_hour:Number(v("cfgDouyinHour")||9)},news:{auto_refresh:document.getElementById("cfgNewsAuto").checked,digest_count:Number(v("cfgDigestCount")||20),enabled_sources:[...document.querySelectorAll("#newsSourceChecks input:checked")].map(x=>x.value)},ai_api_key:key,ima_api_key:v("cfgImaApiKey")};
    status.textContent="正在保存…";
    try { if(!await saveAllAccountSettings()) throw new Error("有账号资料未能保存"); const result=await api("/api/settings","PUT",payload); if(!result.ok) throw new Error(result.error||"保存失败"); status.textContent="保存成功，正在刷新界面"; loadedSettings=result.config; setTimeout(()=>window.location.reload(),500); }
    catch(e){ status.textContent="保存失败："+e.message; }
}

function v(id){ return document.getElementById(id)?.value?.trim() || ""; }
async function testAIConnection(){ const s=document.getElementById("aiTestStatus");s.textContent="测试中…";try{const r=await api("/api/settings/test-ai","POST",{ai:{base_url:v("cfgAiUrl"),model:v("cfgAiModel")},ai_api_key:v("deepseekApiKeyInput")},130000);s.textContent=r.ok?"连接成功":"失败："+r.error;}catch(e){s.textContent="失败："+e.message;} }
async function testImaConnection(){ const s=document.getElementById("imaTestStatus");s.textContent="测试中…";try{const r=await api("/api/settings/test-ima","POST",{client_id:v("cfgImaClient"),api_key:v("cfgImaApiKey")},40000);s.textContent=r.ok?r.message:"失败："+r.error;}catch(e){s.textContent="失败："+e.message;} }
async function testObsidianConnection(){ const s=document.getElementById("obsidianTestStatus");s.textContent="检查中…";try{const r=await api("/api/settings/test-obsidian","POST",{vault_path:v("cfgObsidianVault")},40000);s.textContent=r.ok?r.message:"失败："+r.error;}catch(e){s.textContent="失败："+e.message;} }
async function checkDouyinConfigState(){const e=document.getElementById("douyinConfigState");try{const r=await api("/api/douyin/check-login");e.textContent=r.logged_in?"已登录，可直接同步收藏":"未登录或登录已过期，请点击下方按钮扫码";e.className="config-state "+(r.logged_in?"ok":"warn");}catch(x){e.textContent="检查失败："+x.message;}}
async function startDouyinLogin(){const e=document.getElementById("douyinConfigState");try{const r=await api("/api/douyin/login","POST",{});e.textContent=r.message||r.error;}catch(x){e.textContent="启动失败："+x.message;}}
async function openLocalFolder(kind){try{await api("/api/local/open/"+kind,"POST",{});}catch(e){alert(e.message);}}
async function backupLocalData(){const s=document.getElementById("settingsStatus");s.textContent="正在备份…";try{const r=await api("/api/local/backup","POST",{});s.textContent=r.ok?"备份完成："+r.path:"备份失败："+r.error;}catch(e){s.textContent=e.message;}}
async function shutdownWorkbench(){if(!confirm("退出工作台？本地服务会同时关闭。"))return;await api("/api/local/shutdown","POST",{});document.body.innerHTML='<main class="shutdown-page"><h1>工作台已退出</h1><p>可以安全关闭这个页面。</p></main>';}

// ========== AI 脚本生成 ==========
async function generateScript(topicId) {
    // 检查 API Key
    const settings = await api("/api/settings");
    if (!settings.has_api_key) {
        showSettings();
        return;
    }

    // 显示加载中
    switchTab("scripts");
    document.getElementById("scriptEditor").innerHTML = `
        <div class="editor-loading">
            <div class="loading-spinner"></div>
            <p>AI 正在按${escapeHtml((CONTENT_ACCOUNTS[currentScriptAccount] || CONTENT_ACCOUNTS[currentContentAccount]).fullLabel)}的结构整理脚本...</p>
            <p class="loading-hint">大约需要 30-60 秒，稍等一下</p>
        </div>
    `;

    try {
        const result = await api("/api/script/generate", "POST", { topic_id: topicId });
        if (result.ok) {
            await loadScript(result.script.id);
            loadTopics();
            loadScripts();
        } else {
            document.getElementById("scriptEditor").innerHTML = `
                <div class="editor-placeholder">
                    <p style="color: var(--red)">生成失败：${escapeHtml(result.error || '未知错误')}</p>
                </div>
            `;
        }
    } catch (e) {
        document.getElementById("scriptEditor").innerHTML = `
            <div class="editor-placeholder">
                <p style="color: var(--red)">生成失败：${escapeHtml(e.message)}</p>
            </div>
        `;
    }
}

// ===== AI 分析功能 =====
async function runHotspotAnalysis() {
    const btn = document.getElementById('btnHotspotAnalyze');
    const resultDiv = document.getElementById('aiAnalysisResult');
    const contentDiv = document.getElementById('aiAnalysisContent');
    btn.disabled = true;
    btn.textContent = '✦ 正在解读...';
    resultDiv.style.display = 'block';
    contentDiv.innerHTML = '<p>正在区分公共重要性、交叉验证状态和创作价值，请稍候…</p>';

    try {
        // 1. 触发分析（热点分析脚本最多跑 3 分钟）
        const resp = await api('/api/analyze-hotspots', 'POST', {}, 180000);
        if (!resp.ok) {
            contentDiv.innerHTML = `<p>[失败] 分析失败：${escapeHtml(resp.error || "未知错误")}</p>`;
            return;
        }

        // 2. 加载结果
        const data = await api('/ai', 'GET', null, 10000);
        if (data.ok && data.result) {
            let html = data.result
                .replace(/\n\n/g, '</p><p>')
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/\n/g, '<br>');
            contentDiv.innerHTML = '<div class="hotspot-analysis-text">' + html + '</div>';
            contentDiv.innerHTML += `<p class="analysis-time">分析时间：${data.created_at || ''}</p>`;
        } else {
            contentDiv.innerHTML = '<p>[提示] 分析完成但未获取到结果，请刷新重试。</p>';
        }
    } catch (e) {
        contentDiv.innerHTML = `<p>[失败] 分析出错：${escapeHtml(e.message)}</p>`;
    } finally {
        btn.disabled = false;
        btn.textContent = '✦ AI 解读';
    }
}

function hideAIAnalysis() {
    document.getElementById('aiAnalysisResult').style.display = 'none';
}

// ========== 抖音灵感 ==========

function loadDouyinInspirations() {
    checkDouyinLogin();
    loadDouyinStats();
    loadDouyinFavorites();
    loadDouyinAnalysis();
}

async function checkDouyinLogin() {
    const prompt = document.getElementById("douyinLoginPrompt");
    const stats = document.getElementById("douyinStats");
    const videoSection = document.querySelector("#ideaPanelDouyin .douyin-video-section");
    const analysisSection = document.getElementById("douyinAnalysisSection");
    try {
        const data = await api("/api/douyin/check-login");
        if (data.ok && !data.logged_in) {
            prompt.style.display = "flex";
            stats.style.opacity = "0.4";
            if (videoSection) videoSection.style.opacity = "0.4";
            if (analysisSection) analysisSection.style.display = "none";
        } else {
            prompt.style.display = "none";
            stats.style.opacity = "1";
            if (videoSection) videoSection.style.opacity = "1";
        }
    } catch (e) {
        console.log("登录状态检查失败:", e);
    }
}

let _douyinLoginPolling = false;

async function douyinLogin() {
    const btn = document.getElementById("btnDouyinLogin");
    const status = document.getElementById("douyinStatus");
    btn.disabled = true;
    btn.textContent = "正在打开浏览器...";
    status.textContent = "请在弹出的浏览器窗口中扫码登录抖音";
    status.className = "douyin-status";
    try {
        const data = await api("/api/douyin/login", "POST");
        if (data.ok) {
            status.textContent = "浏览器已打开，请扫码登录。登录成功后这里会自动检测到。";
            status.className = "douyin-status success";
            btn.textContent = "等待登录中...";
            // 开始轮询登录状态
            _pollDouyinLogin();
        } else {
            status.textContent = (data.error || "登录失败");
            status.className = "douyin-status error";
            btn.disabled = false;
            btn.textContent = "打开扫码窗口";
        }
    } catch (e) {
        status.textContent = "请求失败: " + e.message;
        status.className = "douyin-status error";
        btn.disabled = false;
        btn.textContent = "打开扫码窗口";
    }
}

async function _pollDouyinLogin() {
    if (_douyinLoginPolling) return;
    _douyinLoginPolling = true;

    const btn = document.getElementById("btnDouyinLogin");
    const status = document.getElementById("douyinStatus");
    const prompt = document.getElementById("douyinLoginPrompt");
    let attempts = 0;
    const maxAttempts = 60; // 60次 x 5秒 = 5分钟

    const poll = async () => {
        attempts++;
        try {
            const data = await api("/api/douyin/check-login");
            if (data.ok && data.logged_in) {
                // 登录成功！
                status.textContent = "登录成功！可以点击「同步收藏」拉取视频了";
                status.className = "douyin-status success";
                btn.disabled = false;
                btn.textContent = "打开扫码窗口";
                prompt.style.display = "none";
                _douyinLoginPolling = false;
                // 刷新页面状态
                checkDouyinLogin();
                loadDouyinStats();
                return;
            }
        } catch (e) { /* 忽略，继续轮询 */ }

        if (attempts >= maxAttempts) {
            status.textContent = "等待超时（5分钟）。如果已经扫码，请点击「检查登录状态」";
            status.className = "douyin-status error";
            btn.disabled = false;
            btn.textContent = "重新扫码";
            _douyinLoginPolling = false;
            return;
        }

        // 更新倒计时
        const remaining = Math.ceil((maxAttempts - attempts) * 5 / 60);
        status.textContent = `等待扫码登录...（剩余约 ${remaining} 分钟）`;
        setTimeout(poll, 5000);
    };

    setTimeout(poll, 5000);
}

async function loadDouyinStats() {
    try {
        const data = await api("/api/douyin/stats");
        if (data.ok && data.data) {
            document.getElementById("douyinStatTotal").textContent = data.data.total_favorites || 0;
            document.getElementById("douyinStatNew").textContent = data.data.unanalyzed_count || 0;
            document.getElementById("douyinStatInsp").textContent = data.data.total_inspirations || 0;
        }
    } catch (e) {
        console.log("抖音统计加载失败:", e);
    }
}

// ====== 抖音灵感：选中视频ID管理 ======
let _selectedDouyinVideoIds = new Set();

function getSelectedDouyinVideoIds() {
    return Array.from(_selectedDouyinVideoIds);
}

function updateDouyinSelectedUI() {
    const countEl = document.getElementById("douyinSelectedCount");
    const btnEl = document.getElementById("btnAnalyzeSelected");
    const selectAllEl = document.getElementById("douyinSelectAll");
    const count = _selectedDouyinVideoIds.size;
    if (countEl) countEl.textContent = `已选 ${count} 个`;
    if (btnEl) btnEl.disabled = count === 0;

    // 更新全选状态：只有当所有checkbox都被选中时才勾选
    if (selectAllEl) {
        const totalCheckboxes = document.querySelectorAll(".douyin-video-checkbox");
        const checkedCount = document.querySelectorAll(".douyin-video-checkbox:checked").length;
        selectAllEl.checked = totalCheckboxes > 0 && checkedCount === totalCheckboxes;
    }
}

function toggleDouyinSelectAll(checked) {
    document.querySelectorAll(".douyin-video-checkbox").forEach(cb => {
        cb.checked = checked;
        const vid = cb.dataset.videoId;
        if (checked) {
            _selectedDouyinVideoIds.add(vid);
        } else {
            _selectedDouyinVideoIds.delete(vid);
        }
    });
    updateDouyinSelectedUI();
}

function onDouyinVideoCheckboxChange(cb) {
    const vid = cb.dataset.videoId;
    if (cb.checked) {
        _selectedDouyinVideoIds.add(vid);
    } else {
        _selectedDouyinVideoIds.delete(vid);
    }
    updateDouyinSelectedUI();
}

// 从视频标题/描述中提取日期显示用
function getDisplayDate(v) {
    // 后端已统一为“明确收藏时间 / 本地首次发现时间”，不再使用视频发布时间
    if (v.favorite_time && v.favorite_time.length >= 10) {
        return v.favorite_time.substring(0, 10);
    }
    return "未知日期";
}

// 判断是否是"今天"/"昨天"
function getDateLabel(dateStr) {
    const today = localDateString();
    const yesterday = localDateString(new Date(Date.now() - 86400000));
    if (dateStr === today) return "今天";
    if (dateStr === yesterday) return "昨天";
    // 转为"月日"格式
    const parts = dateStr.split("-");
    if (parts.length === 3) {
        return `${parseInt(parts[1])}月${parseInt(parts[2])}日`;
    }
    return dateStr;
}

async function loadDouyinFavorites() {
    const container = document.getElementById("douyinVideoGrid");
    try {
        const data = await api("/api/douyin/favorites?limit=200");
        if (!data.ok) {
            container.innerHTML = `<div class="empty-hint">${escapeHtml(data.error || "无法加载收藏视频")}</div>`;
            return;
        }

        const videos = data.data || [];
        const tabCount = document.getElementById("ideaTabDouyinCount");
        if (tabCount) tabCount.textContent = videos.length;
        // 重置选中状态
        _selectedDouyinVideoIds.clear();
        updateDouyinSelectedUI();

        if (videos.length === 0) {
            container.innerHTML = `<div class="empty-hint">还没有收藏视频，点击「同步收藏」开始获取</div>`;
            return;
        }

        // 后端只返回最近三天，并按收藏时间倒序；这里按收藏日期分组
        const groups = {};
        const groupOrder = [];
        for (const v of videos) {
            const dateKey = getDisplayDate(v);
            if (!groups[dateKey]) {
                groups[dateKey] = [];
                groupOrder.push(dateKey);
            }
            groups[dateKey].push(v);
        }

        // 按出现顺序渲染（groupOrder 已是倒序，因为 videos 是倒序的）
        let html = "";
        for (const dateKey of groupOrder) {
            const groupVideos = groups[dateKey];
            const dateLabel = getDateLabel(dateKey);
            html += `<div class="douyin-date-group">
                <div class="douyin-date-header">
                    <span class="douyin-date-label">${dateLabel}</span>
                    <span class="douyin-date-full">${dateKey}</span>
                    <span class="douyin-date-count">${groupVideos.length} 个视频</span>
                </div>
                <div class="douyin-date-videos">`;

            for (const v of groupVideos) {
                const hashtags = Array.isArray(v.hashtags) ? v.hashtags : [];
                const tagsHtml = hashtags.slice(0, 4).map(t => `<span class="douyin-tag">#${escapeHtml(t)}</span>`).join("");
                const analyzed = v.is_analyzed ? '<span class="douyin-badge-ok">已分析</span>' : '';
                const coverUrl = safeExternalUrl(v.cover_url);
                const videoUrl = safeExternalUrl(v.video_url);
                html += `
                    <div class="douyin-video-card">
                        <div class="douyin-video-select">
                            <input type="checkbox" class="douyin-video-checkbox"
                                   data-video-id="${escapeHtml(v.video_id)}"
                                   onchange="onDouyinVideoCheckboxChange(this)">
                        </div>
                        <div class="douyin-video-cover">
                            <div class="douyin-video-placeholder">▶</div>
                            ${coverUrl ? `<img src="${escapeHtml(coverUrl)}" loading="lazy" alt="" referrerpolicy="no-referrer" onerror="this.style.display='none'">` : ''}
                        </div>
                        <div class="douyin-video-info">
                            <div class="douyin-video-title">${escapeHtml(v.title || '无标题')}</div>
                            <div class="douyin-video-meta">
                                <span class="douyin-video-author">@${escapeHtml(v.author_name || '未知')}</span>
                                <span class="douyin-video-likes">❤ ${v.like_count || 0}</span>
                                ${analyzed}
                            </div>
                            <div class="douyin-video-tags">${tagsHtml}</div>
                            ${videoUrl ? `<a class="douyin-video-link" href="${escapeHtml(videoUrl)}" target="_blank" rel="noopener noreferrer">查看视频 →</a>` : ''}
                        </div>
                    </div>`;
            }

            html += `</div></div>`;
        }

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="empty-hint">加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

async function loadDouyinAnalysis() {
    const section = document.getElementById("douyinAnalysisSection");
    const summaryEl = document.getElementById("douyinSummary");
    const listEl = document.getElementById("douyinInspirationList");

    try {
        const data = await api("/api/douyin/inspirations?limit=100");
        if (!data.ok) {
            section.style.display = "none";
            return;
        }

        const items = data.data || [];
        if (items.length === 0) {
            section.style.display = "none";
            return;
        }

        section.style.display = "block";

        // 按日期分组显示分析结果
        const dateGroups = {};
        const dateOrder = [];
        for (const item of items) {
            const d = item.date || item.created_at?.substring(0, 10) || "未知";
            if (!dateGroups[d]) {
                dateGroups[d] = [];
                dateOrder.push(d);
            }
            dateGroups[d].push(item);
        }

        // 每日总结（取最新一天）
        const latestDate = dateOrder[0];
        const summaryItem = dateGroups[latestDate]?.find(i => i.inspiration_type === "summary");
        if (summaryItem) {
            const label = getDateLabel(latestDate);
            summaryEl.innerHTML = `<div class="douyin-summary-date">${label} · ${latestDate}</div>
                <div class="douyin-summary-text">${escapeHtml(summaryItem.content || summaryItem.title)}</div>`;
        }

        let html = "";

        // 按日期倒序渲染每个分组
        for (const d of dateOrder) {
            const group = dateGroups[d];
            const label = getDateLabel(d);
            const topics = group.filter(i => i.inspiration_type === "topic");
            const skips = group.filter(i => i.inspiration_type === "skip");
            const action = group.find(i => i.inspiration_type === "action");

            if (dateOrder.length > 1 && d !== latestDate) {
                html += `<div class="douyin-analysis-date-divider">${label} · ${d}</div>`;
            }

            // 选题推荐
            if (topics.length > 0) {
                html += "<h4>选题推荐</h4>" + topics.map((t, idx) => {
                    let content = t.content;
                    try { content = typeof content === 'string' ? JSON.parse(content) : content; } catch(e) {}
                    const title = content.title || t.title || '';
                    const angle = content.angle || '';
                    const format = content.format || '';
                    const difficulty = content.difficulty || '';
                    const whyWorth = content.why_worth_doing || '';
                    const sourceVideo = content.source_video || '';
                    const outline = content.outline || [];
                    const score = content.score || (t.relevance_score || 0.7) * 10;
                    const saved = t.is_saved ? 'saved' : '';
                    const scoreNum = Number(score);
                    const scoreClass = scoreNum >= 8 ? 'score-high' : scoreNum >= 6 ? 'score-mid' : 'score-low';

                    const outlineHtml = Array.isArray(outline) && outline.length > 0
                        ? `<div class="douyin-inspiration-outline"><ul>${outline.map(o => `<li>${escapeHtml(o)}</li>`).join('')}</ul></div>`
                        : '';

                    return `
                        <div class="douyin-inspiration-card ${saved}">
                            <div class="douyin-inspiration-header">
                                <span class="douyin-inspiration-num">#${idx + 1}</span>
                                <span class="douyin-inspiration-type">${escapeHtml(format)}</span>
                                <span class="douyin-inspiration-score ${scoreClass}">${scoreNum.toFixed(1)}分</span>
                                ${difficulty ? `<span class="douyin-inspiration-difficulty">${escapeHtml(difficulty)}</span>` : ''}
                            </div>
                            <div class="douyin-inspiration-title">${escapeHtml(title)}</div>
                            ${angle ? `<div class="douyin-inspiration-angle">切入点：${escapeHtml(angle)}</div>` : ''}
                            ${whyWorth ? `<div class="douyin-inspiration-why">为什么值得做：${escapeHtml(whyWorth)}</div>` : ''}
                            ${sourceVideo ? `<div class="douyin-inspiration-source">灵感来源：${escapeHtml(sourceVideo)}</div>` : ''}
                            ${outlineHtml}
                            <div class="douyin-inspiration-actions">
                                ${t.is_saved
                                    ? `<button class="douyin-btn-saved" disabled>已保存</button>`
                                    : `<button onclick="saveDouyinInspiration(${t.id})">保存为选题</button>`
                                }
                            </div>
                        </div>`;
                }).join("");
            }

            // 不推荐选题（可折叠）
            if (skips.length > 0) {
                html += `<div class="douyin-skip-toggle" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none';this.textContent=this.nextElementSibling.style.display==='none'?'▸ 不推荐做选题 (${skips.length}个)':'▾ 不推荐做选题 (${skips.length}个)'">▸ 不推荐做选题 (${skips.length}个)</div>`;
                html += `<div class="douyin-skip-list" style="display:none">` + skips.map(s => {
                    let content = s.content;
                    try { content = typeof content === 'string' ? JSON.parse(content) : content; } catch(e) {}
                    return `
                        <div class="douyin-skip-card">
                            <span class="douyin-skip-video">${escapeHtml(content.video || s.title)}</span>
                            <span class="douyin-skip-reason">${escapeHtml(content.reason || '')}</span>
                        </div>`;
                }).join("") + `</div>`;
            }

            // 行动建议
            if (action && action.content) {
                html += `<div class="douyin-action-plan">
                    <div class="douyin-action-plan-label">🎯 行动建议</div>
                    <div class="douyin-action-plan-text">${escapeHtml(action.content)}</div>
                </div>`;
            }
        }

        listEl.innerHTML = html || "<div class='empty-hint'>暂无分析结果，点击「AI 分析灵感」生成</div>";
    } catch (e) {
        console.log("抖音灵感加载失败:", e);
    }
}

async function douyinSync() {
    const btn = document.getElementById("btnDouyinSync");
    const status = document.getElementById("douyinStatus");
    btn.disabled = true;
    status.textContent = "同步启动中...";

    try {
        const data = await api("/api/douyin/sync", "POST", { max_count: 50 }, 10000);
        if (data.ok || data.status === "already_running") {
            status.textContent = "同步已启动，正在等待结果...";
            status.className = "douyin-status success";
            setTimeout(() => _pollSyncResult(0), 3000);
        } else {
            status.textContent = "启动失败: " + (data.error || "未知错误");
            status.className = "douyin-status error";
            btn.disabled = false;
        }
    } catch (e) {
        status.textContent = "同步失败: " + e.message;
        status.className = "douyin-status error";
        btn.disabled = false;
    }
}

async function _pollSyncResult(attempt) {
    const btn = document.getElementById("btnDouyinSync");
    const status = document.getElementById("douyinStatus");
    const maxAttempts = 40; // 40次 x 5秒 = 最多等200秒（subprocess timeout 300秒）

    try {
        const stateData = await api("/api/douyin/sync-status", "GET", null, 10000);
        if (stateData.ok) {
            const st = stateData.status;
            if (st === "error") {
                status.textContent = "同步失败: " + (stateData.message || "未知错误");
                status.className = "douyin-status error";
                btn.disabled = false;
                setTimeout(() => { status.textContent = ""; }, 8000);
                return;
            }
            if (st === "success") {
                const count = stateData.data?.count || 0;
                status.textContent = "同步完成，获取 " + count + " 个视频";
                status.className = "douyin-status success";
                loadDouyinStats();
                loadDouyinFavorites();
                btn.disabled = false;
                setTimeout(() => { status.textContent = ""; }, 5000);
                return;
            }
            // running / idle → 继续轮询
        }
    } catch (e) { /* 状态查询失败，忽略，继续轮询 */ }

    if (attempt >= maxAttempts) {
        status.textContent = "同步超时，请稍后刷新页面查看结果";
        status.className = "douyin-status error";
        btn.disabled = false;
        setTimeout(() => { status.textContent = ""; }, 8000);
        return;
    }

    status.textContent = "同步中...（" + (attempt + 1) + "/" + maxAttempts + "）";
    setTimeout(() => _pollSyncResult(attempt + 1), 5000);
}

async function douyinAnalyze() {
    const btn = document.getElementById("btnDouyinAnalyze");
    const status = document.getElementById("douyinStatus");
    btn.disabled = true;
    btn.textContent = "分析中...";
    status.className = "douyin-status";

    // 如果有选中的视频，自动走批量分析
    const selectedIds = getSelectedDouyinVideoIds();
    if (selectedIds.length > 0) {
        status.textContent = `AI 分析已启动，正在分析选中的 ${selectedIds.length} 个视频...`;
        try {
            const data = await api("/api/douyin/analyze", "POST", {
                max_videos: selectedIds.length,
                video_ids: selectedIds
            });
            if (data.ok) {
                status.textContent = "分析已启动，正在等待结果...";
                status.className = "douyin-status success";
                setTimeout(() => _pollAnalysisResult(0), 3000);
            } else {
                status.textContent = "启动失败: " + (data.error || "未知错误");
                status.className = "douyin-status error";
                btn.disabled = false;
                btn.textContent = "AI 分析灵感";
            }
            return;
        } catch (e) {
            status.textContent = "请求失败: " + e.message;
            status.className = "douyin-status error";
            btn.disabled = false;
            btn.textContent = "AI 分析灵感";
            return;
        }
    }

    // 没有选中视频时，分析全部（原有逻辑）
    status.textContent = "AI 分析已启动，正在分析今天的收藏视频...";

    try {
        const data = await api("/api/douyin/analyze", "POST", { max_videos: 20 });
        if (data.ok) {
            status.textContent = "分析已启动，正在等待结果...";
            status.className = "douyin-status success";
            // 3秒后开始轮询结果
            setTimeout(() => _pollAnalysisResult(0), 3000);
        } else {
            status.textContent = "启动失败: " + (data.error || "未知错误");
            status.className = "douyin-status error";
            btn.disabled = false;
            btn.textContent = "AI 分析灵感";
        }
    } catch (e) {
        status.textContent = "请求失败: " + e.message;
        status.className = "douyin-status error";
        btn.disabled = false;
        btn.textContent = "AI 分析灵感";
    }
}

/** 分析选中的视频（独立按钮） */
async function douyinAnalyzeSelected() {
    const selectedIds = getSelectedDouyinVideoIds();
    if (selectedIds.length === 0) {
        alert("请先勾选要分析的视频");
        return;
    }
    // 复用 douyinAnalyze 的逻辑（它已经会检测选中状态）
    await douyinAnalyze();
}

async function _pollAnalysisResult(attempt) {
    const btn = document.getElementById("btnDouyinAnalyze");
    const status = document.getElementById("douyinStatus");
    const maxAttempts = 24; // 24次 x 5秒 = 2分钟

    // 优先查分析状态
    try {
        const stateData = await api("/api/douyin/analysis-status");
        if (stateData.ok) {
            const st = stateData.status;
            if (st === "error") {
                // 分析失败了！立即显示错误
                status.textContent = "分析失败: " + (stateData.message || "未知错误");
                status.className = "douyin-status error";
                btn.disabled = false;
                btn.textContent = "AI 分析灵感";
                return;
            }
            if (st === "success") {
                // 分析成功！去加载灵感结果
                status.textContent = "分析完成，正在加载结果...";
                status.className = "douyin-status success";
                loadDouyinAnalysis();
                loadDouyinStats();
                btn.disabled = false;
                btn.textContent = "AI 分析灵感";
                return;
            }
            // running / idle → 继续轮询
        }
    } catch (e) { /* 状态查询失败，忽略 */ }

    if (attempt >= maxAttempts) {
        status.textContent = "分析超时，请稍后刷新页面查看结果";
        status.className = "douyin-status error";
        btn.disabled = false;
        btn.textContent = "AI 分析灵感";
        return;
    }

    status.textContent = `分析中...（${attempt + 1}/${maxAttempts}）`;
    setTimeout(() => _pollAnalysisResult(attempt + 1), 5000);
}

async function saveDouyinInspiration(id) {
    const data = await api(`/api/douyin/inspirations/${id}/save`, "POST");
    if (data.ok) {
        loadDouyinAnalysis();
        loadTopics();
        loadDashboard();
    } else {
        alert("保存失败");
    }
}

// ========== 助手 ========== 
function assistantDisplayName() {
    return assistantState?.name || loadedSettings?.branding?.assistant_name || "小助手";
}

function setXiaoliPetPose(pose = "idle", message = "", duration = 2200, celebrate = false) {
    const pet = document.getElementById("xiaoliPet");
    const bubble = document.getElementById("xiaoliPetBubble");
    if (!pet) return;
    const poseMap = { idle: "idle", blink: "blink", wave: "talk", happy: "talk", thinking: "idle", listening: "talk", talk: "talk" };
    const resolvedPose = poseMap[pose] || "idle";
    const target = pet.querySelector(`.xiaoli-pose[data-pose="${resolvedPose}"]`) || pet.querySelector('.xiaoli-pose[data-pose="idle"]');
    pet.querySelectorAll(".xiaoli-pose").forEach(image => image.classList.toggle("active", image === target));
    pet.dataset.pose = target?.dataset.pose || "idle";
    clearTimeout(xiaoliPetResetTimer);
    if (bubble) {
        bubble.textContent = message || "";
        bubble.classList.toggle("show", Boolean(message));
    }
    if (celebrate) {
        pet.classList.remove("is-celebrating");
        void pet.offsetWidth;
        pet.classList.add("is-celebrating");
        setTimeout(() => pet.classList.remove("is-celebrating"), 1000);
    }
    if (duration > 0 && pose !== "idle") {
        xiaoliPetResetTimer = setTimeout(() => setXiaoliPetPose("idle", "", 0), duration);
    }
}

function scheduleXiaoliPetIdle() {
    clearTimeout(xiaoliPetIdleTimer);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    xiaoliPetIdleTimer = setTimeout(() => {
        const pet = document.getElementById("xiaoliPet");
        if (pet && document.visibilityState === "visible" && pet.dataset.pose === "idle" && !document.body.classList.contains("assistant-drawer-open")) {
            pet.classList.add("is-nodding");
            setTimeout(() => pet.classList.remove("is-nodding"), 1300);
        }
        scheduleXiaoliPetIdle();
    }, 14000 + Math.random() * 12000);
}

function scheduleXiaoliPetBlink() {
    clearTimeout(xiaoliPetBlinkTimer);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    xiaoliPetBlinkTimer = setTimeout(() => {
        const pet = document.getElementById("xiaoliPet");
        if (pet && document.visibilityState === "visible" && pet.dataset.pose === "idle") setXiaoliPetPose("blink", "", 260);
        scheduleXiaoliPetBlink();
    }, 4500 + Math.random() * 4500);
}

function interactWithXiaoliPet() {
    xiaoliPetClickCount += 1;
    const phrases = [
        ["wave", "我在呢，要问点什么？"],
        ["happy", "今天也一起加油呀！"],
        ["thinking", "让我看看今天的重点…"],
        ["wave", "双击我，就能直接聊天哦。"],
        ["happy", "灵感来了就快记下来！"],
    ];
    const selected = xiaoliPetClickCount % 5 === 0 ? ["happy", "你是不是在偷偷戳我呀？"] : phrases[(xiaoliPetClickCount - 1) % phrases.length];
    setXiaoliPetPose(selected[0], selected[1], 2400, selected[0] === "happy");
}

function initXiaoliPet() {
    const pet = document.getElementById("xiaoliPet");
    if (!pet) return;
    pet.dataset.pose = "idle";
    pet.addEventListener("click", () => {
        clearTimeout(xiaoliPetClickTimer);
        xiaoliPetClickTimer = setTimeout(interactWithXiaoliPet, 230);
    });
    pet.addEventListener("dblclick", event => {
        event.preventDefault();
        clearTimeout(xiaoliPetClickTimer);
        setXiaoliPetPose("wave", "来啦，想聊什么？", 1900);
        openAssistantChat();
    });
    pet.addEventListener("keydown", event => {
        if (event.key === "Enter") { event.preventDefault(); openAssistantChat(); }
        if (event.key === " ") { event.preventDefault(); interactWithXiaoliPet(); }
    });
    pet.addEventListener("pointermove", event => {
        const rect = pet.getBoundingClientRect();
        pet.style.setProperty("--pet-x", `${((event.clientX - rect.left) / rect.width - .5) * 5}px`);
        pet.style.setProperty("--pet-y", `${((event.clientY - rect.top) / rect.height - .5) * 2}px`);
    });
    pet.addEventListener("pointerleave", () => {
        pet.style.setProperty("--pet-x", "0px");
        pet.style.setProperty("--pet-y", "0px");
    });
    scheduleXiaoliPetIdle();
    scheduleXiaoliPetBlink();
}

function compactAssistantText(text, maxLength = 92) {
    const clean = String(text || "").replace(/\s+/g, " ").trim();
    if (clean.length <= maxLength) return clean;
    const firstSentence = clean.match(/^.*?[。！？!?](?:\s|$)/)?.[0]?.trim();
    if (firstSentence && firstSentence.length <= maxLength) return firstSentence;
    return clean.slice(0, maxLength - 1).trimEnd() + "…";
}

function assistantListHtml(items, emptyText, limit = 3) {
    if (!Array.isArray(items) || !items.length) return `<div class="assistant-brief-empty">${escapeHtml(emptyText)}</div>`;
    return items.slice(0, limit).map(item => `<div class="assistant-brief-item" title="${escapeHtml(item?.note || item?.title || "")}">
        <strong>${escapeHtml(compactAssistantText(item?.title || "未命名", 48))}</strong>
        <em>${escapeHtml(item?.meta || "")}</em>
    </div>`).join("");
}

function renderAssistantBriefing(briefing, context) {
    assistantState.briefing = briefing || {};
    assistantState.context = context || assistantState.context || {};
    const focus = document.getElementById("assistantFocus");
    if (focus) focus.textContent = compactAssistantText(briefing?.focus || "先完成一个可交付结果", 110);
    const update = document.getElementById("assistantLastUpdate");
    if (update) update.textContent = `本地整理 · ${briefing?.generated_at || briefing?.created_at || context?.generated_at || "刚刚"}`;
    const important = document.getElementById("dashboardImportantList");
    if (important) {
        const news = Array.isArray(briefing?.news) ? briefing.news.slice(0, 5) : [];
        important.innerHTML = news.length ? news.map(item => `
            <button class="editorial-feed-item" onclick="switchTab('hotspots')">
                <span class="feed-index">重点</span>
                <strong>${escapeHtml(compactAssistantText(item?.title || "未命名", 52))}</strong>
                <small>${escapeHtml(item?.meta || "")}</small>
            </button>
        `).join("") : `<div class="editorial-empty">暂无新闻缓存，去“今天”刷新即可。</div>`;
    }
}

function renderAssistantSignals(context) {
    const accounts = context?.accounts || [];
    const readyAccounts = accounts.filter(item => Number(item.followers || 0) > 0 || item.audience?._followers_display).length;
    const ideaCount = (context?.douyin_inspirations || []).length + (context?.ima_inspirations || []).length;
    const newsCount = document.getElementById("assistantNewsCount");
    const ideaCountEl = document.getElementById("assistantIdeaCount");
    if (newsCount) newsCount.textContent = `${Math.min((context?.news || []).length, 20)}`;
    if (ideaCountEl) ideaCountEl.textContent = `${ideaCount}`;
    const knowledge = context?.knowledge || {};
    const knowledgeStatus = document.getElementById("assistantKnowledgeStatus");
    if (knowledgeStatus) {
        const counts = knowledge.counts || {};
        const wiki = Number(counts.wiki || 0);
        const topics = Number(counts.topic || 0) + Number(counts.vault_topic || 0);
        const scripts = Number(counts.script || 0) + Number(counts.vault_script || 0);
        const memories = Number(counts.memory || 0);
        knowledgeStatus.textContent = knowledge.enabled
            ? `已连接知识库 · 维基 ${wiki} · 选题 ${topics} · 脚本 ${scripts} · 我的 ${memories}`
            : `已读取工作台资料 · 本地知识库未连接`;
        knowledgeStatus.classList.toggle("connected", Boolean(knowledge.enabled));
    }
    document.getElementById("assistantFab")?.classList.toggle("has-update", Boolean(context?.updates_pending));
}

function assistantSourceHtml(source) {
    const title = escapeHtml(compactAssistantText(source?.title || "本地资料", 28));
    const path = String(source?.path || "");
    if (path) return `<button data-vault-path="${escapeHtml(path)}" onclick="openVaultInObsidian(this.dataset.vaultPath)">${title}</button>`;
    return `<span>${title}</span>`;
}

function assistantMessageHtml(message) {
    const role = message?.role === "user" ? "user" : "assistant";
    const meta = role === "assistant" ? (message?.mode === "ai" ? "AI 回答" : "本地回答") : "你";
    const sources = role === "assistant" && Array.isArray(message?.sources) ? message.sources.slice(0, 8) : [];
    return `<div class="assistant-message ${role}">
        <span class="assistant-message-avatar">里</span>
        <div class="assistant-message-bubble">${escapeHtml(message?.content || "")}
            ${sources.length ? `<div class="assistant-message-sources"><em>本次参考 ${sources.length} 条</em>${sources.map(assistantSourceHtml).join("")}</div>` : ""}
            <span class="assistant-message-meta">${escapeHtml(meta)} · ${escapeHtml((message?.created_at || "").slice(11, 16) || "现在")}</span>
        </div>
    </div>`;
}

function renderAssistantMessages() {
    const container = document.getElementById("assistantChatMessages");
    if (!container) return;
    const messages = assistantState.messages || [];
    container.innerHTML = messages.length ? messages.map(assistantMessageHtml).join("") : assistantMessageHtml({
        role: "assistant", mode: "local", created_at: "", content: `嗨，我是${assistantDisplayName()}。我会先查脚本库、选题库、我的资料和本地知识库，再回答你的问题。`,
    });
    container.scrollTop = container.scrollHeight;
}

async function loadAssistant() {
    const status = document.getElementById("assistantChatStatus");
    if (!document.getElementById("assistantOverview")) return;
    try {
        const result = await api("/api/assistant/overview", "GET", null, 45000);
        assistantState = { briefing: result.briefing || {}, context: result.context || {}, messages: result.messages || [], name: result.assistant?.name || assistantDisplayName() };
        renderAssistantBriefing(assistantState.briefing, assistantState.context);
        renderAssistantSignals(assistantState.context);
        renderAssistantMessages();
        const voiceToggle = document.getElementById("assistantVoiceToggle");
        if (voiceToggle) voiceToggle.textContent = `语音回复：${assistantVoiceEnabled ? "开" : "关"}`;
        initAssistantVoices();
        if (status) { status.textContent = ""; status.className = "assistant-chat-status"; }
    } catch (error) {
        if (status) { status.textContent = `${assistantDisplayName()}暂时无法读取工作台：` + error.message; status.className = "assistant-chat-status error"; }
    }
}

async function regenerateAssistantBriefing(button) {
    const original = button?.innerHTML;
    if (button) { button.disabled = true; button.textContent = `${assistantDisplayName()}正在整理…`; }
    setXiaoliPetPose("thinking", "我在重新整理今天的重点…", 0);
    try {
        const result = await api("/api/assistant/briefing", "POST", { use_ai: true }, 135000);
        assistantState.briefing = result.briefing || {};
        assistantState.context = result.context || {};
        renderAssistantBriefing(assistantState.briefing, assistantState.context);
        renderAssistantSignals(assistantState.context);
        setXiaoliPetPose("happy", "整理好啦，先看最重要的一件事。", 2300, true);
    } catch (error) {
        setAssistantChatStatus("重新整理失败：" + error.message, true);
        setXiaoliPetPose("wave", "这次没有整理成功，稍后再试试吧。", 2600);
    } finally {
        if (button) { button.disabled = false; button.innerHTML = original; }
    }
}

function assistantVoiceRank(voice) {
    const name = `${voice.name} ${voice.voiceURI}`.toLowerCase();
    let score = /^zh[-_]cn/i.test(voice.lang) ? 80 : /^zh/i.test(voice.lang) ? 45 : 0;
    if (/xiaoxiao|晓晓/.test(name)) score += 125;
    else if (/xiaoyi|晓伊/.test(name)) score += 115;
    else if (/yaoyao|瑶瑶/.test(name)) score += 95;
    else if (/huihui|慧慧/.test(name)) score += 65;
    if (/natural|neural|online|自然/.test(name)) score += 35;
    if (/female|女声/.test(name)) score += 18;
    if (/hong kong|taiwan|cantonese|粤语/.test(name)) score -= 20;
    return score;
}

function initAssistantVoices() {
    if (!("speechSynthesis" in window)) return;
    const select = document.getElementById("assistantVoiceSelect");
    const voices = window.speechSynthesis.getVoices();
    assistantVoiceOptions = voices.filter(voice => /^zh/i.test(voice.lang)).sort((a, b) => assistantVoiceRank(b) - assistantVoiceRank(a));
    if (!assistantVoiceOptions.length) assistantVoiceOptions = voices.slice().sort((a, b) => assistantVoiceRank(b) - assistantVoiceRank(a));
    if (!assistantVoiceOptions.length || !select) return;
    if (!assistantVoiceOptions.some(voice => voice.name === assistantSelectedVoice)) assistantSelectedVoice = assistantVoiceOptions[0].name;
    select.innerHTML = assistantVoiceOptions.slice(0, 12).map(voice => `<option value="${escapeHtml(voice.name)}">${escapeHtml(voice.name.replace(/^Microsoft\s+/i, ""))}</option>`).join("");
    select.value = assistantSelectedVoice;
}

function saveAssistantVoice(name) {
    assistantSelectedVoice = name || "";
    localStorage.setItem("xiaoli_voice_name", assistantSelectedVoice);
}

function assistantSpeechChunks(text) {
    const clean = String(text || "").replace(/[*#_`]/g, "").replace(/\s+/g, " ").trim();
    const sentences = clean.match(/[^。！？!?；;]+[。！？!?；;]?/g) || [clean];
    const chunks = [];
    sentences.forEach(sentence => {
        const part = sentence.trim();
        if (!part) return;
        if (chunks.length && (chunks[chunks.length - 1] + part).length <= 72) chunks[chunks.length - 1] += part;
        else chunks.push(part);
    });
    return chunks;
}

function speakAssistant(text, force = false) {
    if ((!assistantVoiceEnabled && !force) || !("speechSynthesis" in window) || !String(text || "").trim()) return;
    window.speechSynthesis.cancel();
    const run = ++assistantSpeechRun;
    const chunks = assistantSpeechChunks(text);
    const voice = assistantVoiceOptions.find(item => item.name === assistantSelectedVoice) || assistantVoiceOptions[0];
    const pet = document.getElementById("xiaoliPet");
    pet?.classList.add("is-speaking");
    setXiaoliPetPose("talk", "我开始播报啦。", 0);
    const speakNext = index => {
        if (run !== assistantSpeechRun) return;
        if (index >= chunks.length) { pet?.classList.remove("is-speaking"); setXiaoliPetPose("happy", "播报结束，需要我再解释吗？", 2100, true); return; }
        const utterance = new SpeechSynthesisUtterance(chunks[index]);
        utterance.lang = voice?.lang || "zh-CN";
        utterance.rate = .94;
        utterance.pitch = 1;
        utterance.volume = 1;
        if (voice) utterance.voice = voice;
        utterance.onend = () => setTimeout(() => speakNext(index + 1), /[。！？!?]$/.test(chunks[index]) ? 145 : 80);
        utterance.onerror = event => { pet?.classList.remove("is-speaking"); if (run !== assistantSpeechRun || event.error === "interrupted" || event.error === "canceled") return; setAssistantChatStatus("当前声线播放失败，请换一个声音再试听。", true); setXiaoliPetPose("wave", "这个声音没有播放成功，换一个试试吧。", 2600); };
        window.speechSynthesis.speak(utterance);
    };
    speakNext(0);
}

function previewAssistantVoice() {
    speakAssistant(`你好，我是${assistantDisplayName()}。今天的重点，我已经帮你整理好了。我们先从最重要的一件事开始。`, true);
}

function playAssistantBriefing() {
    const speech = assistantState.briefing?.speech || assistantState.briefing?.focus;
    if (!speech) { setAssistantChatStatus("播报还没准备好，请先重新整理。", true); return; }
    speakAssistant(speech);
}

function toggleAssistantVoice() {
    assistantVoiceEnabled = !assistantVoiceEnabled;
    localStorage.setItem("xiaoli_voice_enabled", assistantVoiceEnabled ? "1" : "0");
    const button = document.getElementById("assistantVoiceToggle");
    if (button) button.textContent = `语音回复：${assistantVoiceEnabled ? "开" : "关"}`;
    if (!assistantVoiceEnabled && "speechSynthesis" in window) { assistantSpeechRun += 1; window.speechSynthesis.cancel(); setXiaoliPetPose("idle", "", 0); }
}

function toggleAssistantDetails() {
    const details = document.getElementById("assistantOverviewDetails");
    const button = document.getElementById("assistantDetailsToggle");
    if (!details) return;
    const opening = details.hidden;
    details.hidden = !opening;
    if (button) button.textContent = opening ? "收起完整播报" : "查看完整播报";
}

function openAssistantChat() {
    document.getElementById("assistantDrawer")?.classList.add("open");
    document.getElementById("assistantDrawer")?.setAttribute("aria-hidden", "false");
    document.getElementById("assistantDrawerBackdrop")?.classList.add("open");
    document.body.classList.add("assistant-drawer-open");
    document.getElementById("assistantFab")?.classList.remove("has-update");
    if (document.getElementById("xiaoliPet")?.dataset.pose === "idle") setXiaoliPetPose("wave", "想聊什么？", 1700);
    renderAssistantMessages();
    setTimeout(() => document.getElementById("assistantChatInput")?.focus(), 180);
}

function closeAssistantChat() {
    document.getElementById("assistantDrawer")?.classList.remove("open");
    document.getElementById("assistantDrawer")?.setAttribute("aria-hidden", "true");
    document.getElementById("assistantDrawerBackdrop")?.classList.remove("open");
    document.body.classList.remove("assistant-drawer-open");
    if (assistantListening && assistantRecognition) assistantRecognition.stop();
}

function setAssistantChatStatus(message, isError = false) {
    const status = document.getElementById("assistantChatStatus");
    if (!status) return;
    status.textContent = message || "";
    status.className = `assistant-chat-status${isError ? " error" : ""}`;
}

async function sendAssistantMessage() {
    const input = document.getElementById("assistantChatInput");
    const button = document.getElementById("assistantSendBtn");
    const message = input?.value?.trim();
    if (!message || button?.disabled) return;
    assistantState.messages.push({ role: "user", content: message, mode: "input", created_at: new Date().toISOString().slice(0, 16).replace("T", " ") });
    renderAssistantMessages();
    input.value = "";
    button.disabled = true;
    setAssistantChatStatus(`${assistantDisplayName()}正在整理回答…`);
    setXiaoliPetPose("thinking", "让我想一想…", 0);
    try {
        const result = await api("/api/assistant/chat", "POST", { message }, 135000);
        const reply = result.message || { role: "assistant", content: "我已经整理好了。", mode: result.mode };
        assistantState.messages.push(reply);
        renderAssistantMessages();
        setAssistantChatStatus(result.mode === "ai" ? "已结合 AI 与本地数据回答" : "已使用本地数据回答");
        if (assistantAutoSpeak) speakAssistant(reply.content);
        else setXiaoliPetPose("happy", "回答整理好啦。", 1700, true);
    } catch (error) {
        setAssistantChatStatus("发送失败：" + error.message, true);
        setXiaoliPetPose("wave", "这次没有回答成功，再试一次吧。", 2300);
    } finally {
        assistantAutoSpeak = false;
        button.disabled = false;
        input?.focus();
    }
}

function askAssistant(question) {
    const input = document.getElementById("assistantChatInput");
    if (!input) return;
    input.value = question;
    sendAssistantMessage();
}

function handleAssistantInputKey(event) {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendAssistantMessage();
    }
}

function toggleAssistantListening() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const mic = document.getElementById("assistantMicBtn");
    if (!Recognition) {
        setAssistantChatStatus("当前浏览器不支持语音识别，请使用最新版 Edge 或 Chrome。", true);
        return;
    }
    if (assistantListening && assistantRecognition) {
        assistantRecognition.stop();
        return;
    }
    assistantRecognition = new Recognition();
    assistantRecognition.lang = "zh-CN";
    assistantRecognition.interimResults = true;
    assistantRecognition.continuous = false;
    assistantRecognition.onstart = () => { assistantListening = true; mic?.classList.add("listening"); setAssistantChatStatus("正在听，请说话…"); setXiaoliPetPose("listening", "我在听，请说吧。", 0); };
    assistantRecognition.onresult = event => {
        let transcript = "";
        for (let i = event.resultIndex; i < event.results.length; i++) transcript += event.results[i][0].transcript;
        const input = document.getElementById("assistantChatInput");
        if (input) input.value = transcript.trim();
        if (event.results[event.results.length - 1].isFinal && transcript.trim()) {
            assistantAutoSpeak = true;
            setXiaoliPetPose("thinking", "听到了，我来整理。", 0);
            setTimeout(sendAssistantMessage, 120);
        }
    };
    assistantRecognition.onerror = event => { setAssistantChatStatus(event.error === "not-allowed" ? "没有麦克风权限，请在浏览器地址栏允许麦克风。" : "语音识别没有成功，请再试一次。", true); setXiaoliPetPose("wave", "我没有听清，再说一次好吗？", 2200); };
    assistantRecognition.onend = () => { assistantListening = false; mic?.classList.remove("listening"); if (document.getElementById("xiaoliPet")?.dataset.pose === "listening") setXiaoliPetPose("idle", "", 0); };
    assistantRecognition.start();
}

async function syncAssistantSources(button) {
    const original = button?.innerHTML;
    if (button) { button.disabled = true; button.textContent = "正在同步…"; }
    setAssistantChatStatus("正在刷新工作台、知识库、账号、新闻和 ima…");
    setXiaoliPetPose("thinking", "我在刷新工作台和知识库资料…", 0);
    try {
        const syncResults = await Promise.allSettled([
            api("/api/knowledge/status?refresh=1", "GET", null, 70000),
            api("/api/accounts/refresh", "POST", {}, 70000),
            api("/api/ima/notes", "GET", null, 130000),
            api("/api/hotspots/refresh", "POST", {}, 15000),
        ]);
        let hotspotDone = false;
        for (let i = 0; i < 20 && !hotspotDone; i++) {
            const state = await api("/api/hotspots/refresh-status", "GET", null, 8000);
            hotspotDone = state.status !== "running";
            if (!hotspotDone) await new Promise(resolve => setTimeout(resolve, 800));
        }
        const briefingResult = await api("/api/assistant/briefing", "POST", { use_ai: true }, 135000);
        assistantState.briefing = briefingResult.briefing || {};
        assistantState.context = briefingResult.context || {};
        renderAssistantBriefing(assistantState.briefing, assistantState.context);
        renderAssistantSignals(assistantState.context);
        const failed = syncResults.filter(item => item.status === "rejected" || item.value?.ok === false).length;
        setAssistantChatStatus(failed ? `整理完成，${failed} 个来源暂时不可用，已保留原有数据。` : "全部来源已同步并重新整理。", failed > 0);
        setXiaoliPetPose(failed ? "wave" : "happy", failed ? "整理完成，部分来源稍后再试。" : "全部整理好啦！", 2500, !failed);
    } catch (error) {
        setAssistantChatStatus("同步未完全成功：" + error.message + "。本地已有数据不会丢失。", true);
        setXiaoliPetPose("wave", "同步没有完全成功，原有数据还在。", 2600);
    } finally {
        if (button) { button.disabled = false; button.innerHTML = original; }
    }
}

async function clearAssistantChat() {
    if (!confirm(`清空${assistantDisplayName()}的本地对话记录？播报和工作台数据不会删除。`)) return;
    try {
        await api("/api/assistant/messages", "DELETE", {});
        assistantState.messages = [];
        renderAssistantMessages();
        setAssistantChatStatus("对话记录已清空");
    } catch (error) {
        setAssistantChatStatus("清空失败：" + error.message, true);
    }
}

/* ===== 深链支持：DSH 侧栏方块驱动 iframe 时，按 location.hash 直达板块（如 #hotspots） ===== */
(function () {
    /* 嵌入模式：被 DSH 等外部页面 iframe 嵌入时，隐藏本应用侧栏（与宿主侧栏重复）。
       window.self 与 window.top 跨源比较可能直接抛异常，抛异常也视为被嵌入。 */
    try {
        if (window.self !== window.top) {
            document.documentElement.classList.add('wb-embedded');
        }
    } catch (e) {
        document.documentElement.classList.add('wb-embedded');
    }

    function applyDeepLinkHash() {
        var h = location.hash.replace(/^#/, '');
        if (h && typeof switchTab === 'function') {
            try { switchTab(h); } catch (e) { /* ignore */ }
        }
    }
    window.addEventListener('hashchange', applyDeepLinkHash);
    if (document.readyState === 'loading') {
        window.addEventListener('DOMContentLoaded', applyDeepLinkHash);
    }
    setTimeout(applyDeepLinkHash, 0);
    setTimeout(applyDeepLinkHash, 400);
    setTimeout(applyDeepLinkHash, 1200);
})();
