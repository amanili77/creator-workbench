const experience = document.querySelector("#experience");
const viewport = document.querySelector("#viewport");
const scene = document.querySelector("#scene");
const image = document.querySelector("#sceneImage");
const loader = document.querySelector("#loader");
const loadPercent = document.querySelector("#loadPercent");
const panel = document.querySelector("#panel");
const panelDemo = document.querySelector("#panelDemo");
const speechBubble = document.querySelector("#xiaoliSpeech");
const toast = document.querySelector("#spaceToast");
const deskMonitor = document.querySelector("#deskMonitor");
const deskMonitorDate = document.querySelector("#deskMonitorDate");
const deskMonitorClock = document.querySelector("#deskMonitorClock");
const deskMonitorBody = document.querySelector("#deskMonitorBody");
const deskMonitorProgress = document.querySelector("#deskMonitorProgress");
const deskMonitorProgressBar = document.querySelector("#deskMonitorProgressBar");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const ui = {
    close: document.querySelector("#panelClose"),
    reset: document.querySelector("#panelReset"),
    globalReset: document.querySelector("#resetView"),
    sound: document.querySelector("#soundToggle"),
    primary: document.querySelector("#primaryAction"),
    secondary: document.querySelector("#secondaryAction"),
    openFeature: document.querySelector("#openFeature"),
    number: document.querySelector("#panelNumber"),
    kicker: document.querySelector("#panelKicker"),
    title: document.querySelector("#panelTitle"),
    description: document.querySelector("#panelDescription"),
};

const MODULES = {
    weekly: {
        number: "01", effect: "calendar", kicker: "WEEKLY RHYTHM", title: "本周计划",
        description: "先看本周节奏，在日历上直接新增任务、推进或完成。",
        link: "/?tab=weekly", openLabel: "打开完整周计划", primary: "新增任务", secondary: "刷新任务",
        speech: "这是本周计划。你可以直接新增任务，也可以在这里推进完成状态。",
        focus: { x: 8.5, y: 65, scale: 1.42, panX: 58, panY: -12 },
    },
    radio: {
        number: "02", effect: "radio", kicker: "DAILY SIGNAL", title: "今日新闻",
        description: "先听最重要的内容，需要时再进入“今天”。",
        link: "/?tab=hotspots", openLabel: "打开今天", primary: "播放今日简报", secondary: "刷新新闻",
        speech: "收音机已接入“今天”。我先为你整理最值得关注的五条。",
        focus: { x: 23, y: 54, scale: 1.52, panX: 40, panY: 9 },
    },
    inspirations: {
        number: "03", effect: "camera", kicker: "IDEA CAPTURE", title: "灵感流",
        description: "把 IMA 与抖音灵感放到同一条时间线，并可直接收进选题库。",
        link: "/?tab=inspirations", openLabel: "打开完整灵感流", primary: "同步 IMA", secondary: "同步抖音",
        speech: "相机里是最近的灵感。可以按来源查看，也可以直接收进大号或小号选题库。",
        focus: { x: 25, y: 76, scale: 1.44, panX: 42, panY: -42 },
    },
    archive: {
        number: "04", effect: "book", kicker: "PERSONAL CONTEXT", title: "我的资料库",
        description: "保存你的背景、经历、偏好和目标，让 AI 写作更懂你。",
        link: "/?tab=archive", openLabel: "打开完整资料库", primary: "新增个人背景", secondary: "刷新资料",
        speech: "这本书保存你的个人背景。标记为允许 AI 引用后，脚本和回答会结合这些信息。",
        focus: { x: 51, y: 78, scale: 1.38, panX: 0, panY: -46 },
    },
    assistant: {
        number: "05", effect: "xiaoli", kicker: "PERSONAL ASSISTANT", title: "小助手",
        description: "读取本地新闻、任务与灵感，向你汇报并回答工作台相关问题。",
        link: "/?tab=dashboard", openLabel: "返回助理总览", primary: "播放今日汇报", secondary: "重新整理",
        speech: "我在。你可以输入或直接说话，让我汇报新闻、任务和灵感。",
        focus: { x: 70.5, y: 56, scale: 1.42, panX: -31, panY: 4 },
    },
    memo: {
        number: "06", effect: "safe", kicker: "LOCAL NOTES", title: "备忘录",
        description: "常用文字和私密内容都保存在本机；私密内容默认隐藏。",
        link: "/?tab=archive", openLabel: "打开完整资料库", primary: "新增备忘录", secondary: "刷新备忘录",
        speech: "铁盒里是你的本地备忘录。私密内容需要主动解锁，而且不会交给 AI。",
        focus: { x: 90.5, y: 68, scale: 1.42, panX: -58, panY: -17 },
    },
    topics: {
        number: "07", effect: "notebook", kicker: "CONTENT PIPELINE", title: "选题与脚本",
        description: "大号与 Vlog 小号分开管理，支持选题前进、退回与进入脚本。",
        link: "/?tab=topics", openLabel: "打开完整选题库", primary: "新建选题", secondary: "刷新选题",
        speech: "笔记本里是两个账号的内容管线。你可以推进，也可以把选题退回上一步。",
        focus: { x: 82, y: 82, scale: 1.38, panX: -49, panY: -43 },
    },
};

const CACHE_KEY = "space_radio_digest_v1";
const PLAN_FLOW = ["todo", "doing", "done"];
const PLAN_LABELS = { todo: "待办", doing: "进行中", done: "已完成" };
const TOPIC_FLOW = ["idea", "confirmed", "scripting", "filming", "done"];
const TOPIC_LABELS = { idea: "点子", confirmed: "已确认", scripting: "写稿中", filming: "拍摄中", done: "已发布", abandoned: "已放弃" };
const ACCOUNT_LABELS = { all: "全部账号", main: "大号", vlog: "Vlog 小号" };

const state = {
    weekly: [],
    radio: null,
    radioView: "personal",
    inspirations: { items: [], counts: {}, filter: "all", account: "main" },
    archive: { items: [], stats: {}, categories: {} },
    memo: { items: [], stats: {} },
    assistant: null,
    topics: { items: [], account: "main" },
    forms: { weekly: false, archive: false, memo: false, topics: false },
};

let profileName = "你";
let appName = "创作者工作台";
let assistantName = "小助手";
let activeModule = "";
let focused = false;
let targetX = 0;
let targetY = 0;
let currentX = 0;
let currentY = 0;
let targetScale = 1;
let currentScale = 1;
let focusOriginX = 50;
let focusOriginY = 50;
let audioState = null;
let speechRun = 0;
let toastTimer = null;
let assistantRecognition = null;
let assistantListening = false;
const revealedMemos = new Map();

init();

function init() {
    bindEvents();
    setupDeskMonitor();
    startLoader();
    animate();
    loadIdentity();
    loadDeskTodos();
}

function bindEvents() {
    viewport.addEventListener("pointermove", event => {
        const rect = viewport.getBoundingClientRect();
        const nx = (event.clientX - rect.left) / rect.width;
        const ny = (event.clientY - rect.top) / rect.height;
        viewport.style.setProperty("--pointer-x", `${nx * 100}%`);
        viewport.style.setProperty("--pointer-y", `${ny * 100}%`);
        if (!focused && !reduceMotion) {
            targetX = (nx - .5) * -13;
            targetY = (ny - .5) * -7;
        }
    });
    viewport.addEventListener("pointerleave", () => {
        if (!focused) {
            targetX = 0;
            targetY = 0;
        }
    });
    document.querySelectorAll("[data-object]").forEach(button => {
        button.addEventListener("click", () => focusModule(button.dataset.object));
    });
    panelDemo.addEventListener("click", handlePanelClick);
    panelDemo.addEventListener("change", handlePanelChange);
    panelDemo.addEventListener("submit", handlePanelSubmit);
    ui.close.addEventListener("click", resetView);
    ui.reset.addEventListener("click", resetView);
    ui.globalReset.addEventListener("click", resetView);
    ui.sound.addEventListener("click", toggleSound);
    ui.primary.addEventListener("click", () => runHeaderAction("primary"));
    ui.secondary.addEventListener("click", () => runHeaderAction("secondary"));
    window.addEventListener("keydown", event => {
        if (event.key === "Escape") resetView();
        const index = Number(event.key) - 1;
        const keys = Object.keys(MODULES);
        if (index >= 0 && index < keys.length && !isTypingTarget(event.target)) focusModule(keys[index]);
    });
}

function setupDeskMonitor() {
    const refreshLayout = () => requestAnimationFrame(layoutDeskMonitor);
    if (image.complete) refreshLayout();
    image.addEventListener("load", refreshLayout, { once: true });
    window.addEventListener("resize", refreshLayout);
    updateDeskClock();
    window.setInterval(updateDeskClock, 30000);
}

function layoutDeskMonitor() {
    if (!deskMonitor || !image.naturalWidth || !image.naturalHeight) return;
    const sceneWidth = scene.clientWidth;
    const sceneHeight = scene.clientHeight;
    const scale = Math.max(sceneWidth / image.naturalWidth, sceneHeight / image.naturalHeight);
    const renderedWidth = image.naturalWidth * scale;
    const renderedHeight = image.naturalHeight * scale;
    const offsetX = (sceneWidth - renderedWidth) / 2;
    const offsetY = (sceneHeight - renderedHeight) / 2;
    const screen = { x: 580, y: 290, width: 494, height: 263 };
    deskMonitor.style.left = `${offsetX + screen.x * scale}px`;
    deskMonitor.style.top = `${offsetY + screen.y * scale}px`;
    deskMonitor.style.width = `${screen.width * scale}px`;
    deskMonitor.style.height = `${screen.height * scale}px`;
}

function updateDeskClock() {
    const now = new Date();
    deskMonitorDate.textContent = new Intl.DateTimeFormat("zh-CN", {
        month: "long", day: "numeric", weekday: "short",
    }).format(now);
    deskMonitorClock.textContent = new Intl.DateTimeFormat("zh-CN", {
        hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(now);
}

async function loadDeskTodos() {
    try {
        const plans = await apiJson("/api/weekly-plans");
        state.weekly = Array.isArray(plans) ? plans : [];
        renderDeskMonitor();
    } catch {
        deskMonitorBody.innerHTML = `<span class="desk-monitor-empty">暂时无法读取待办<br><small>点击屏幕打开周计划</small></span>`;
    }
}

function renderDeskMonitor() {
    const plans = Array.isArray(state.weekly) ? state.weekly : [];
    const completed = plans.filter(plan => plan.status === "done").length;
    const progress = plans.length ? Math.round(completed / plans.length * 100) : 0;
    const active = plans
        .filter(plan => plan.status !== "done")
        .sort((a, b) => Number(b.status === "doing") - Number(a.status === "doing"))
        .slice(0, 3);
    deskMonitorProgress.textContent = `本周进度 ${progress}%`;
    deskMonitorProgressBar.style.width = `${progress}%`;
    if (!active.length) {
        deskMonitorBody.innerHTML = `<span class="desk-monitor-empty"><strong>${plans.length ? "今天的安排已完成" : "今天还没有待办"}</strong><small>点击屏幕添加第一件要做的事</small></span>`;
        return;
    }
    deskMonitorBody.innerHTML = active.map((plan, index) => `
        <span class="desk-monitor-task ${plan.status === "doing" ? "is-doing" : ""}">
            <i>${String(index + 1).padStart(2, "0")}</i>
            <b>${escapeHtml(plan.title || "未命名任务")}</b>
            <em>${plan.status === "doing" ? "进行中" : "待办"}</em>
        </span>`).join("");
}

async function loadIdentity() {
    try {
        const payload = await apiJson("/api/settings");
        profileName = payload.config?.profile?.display_name || profileName;
        appName = payload.config?.branding?.app_name || appName;
        assistantName = payload.config?.branding?.assistant_name || assistantName;
        MODULES.assistant.title = `${assistantName}助手`;
        document.title = `${appName} · 空间模式`;
        experience.setAttribute("aria-label", `${appName}空间模式`);
        document.querySelectorAll("[data-assistant-name]").forEach(element => { element.textContent = assistantName; });
    } catch {
        // 身份读取失败不影响空间模式使用。
    }
}

async function focusModule(key) {
    const config = MODULES[key];
    if (!config) return;
    activeModule = key;
    focused = true;
    experience.classList.add("has-focus");
    panel.setAttribute("aria-hidden", "false");
    ui.number.textContent = config.number;
    ui.kicker.textContent = config.kicker;
    ui.title.textContent = config.title;
    ui.description.textContent = config.description;
    ui.openFeature.href = config.link;
    ui.openFeature.querySelector("span").textContent = config.openLabel;
    setHeaderButton(ui.primary, config.primary);
    setHeaderButton(ui.secondary, config.secondary);
    panelDemo.innerHTML = loadingMarkup(`正在读取${config.title}`);
    applyFocus(config);
    playChime();
    showXiaoliSpeech(config.speech);
    await loadModule(key);
}

function applyFocus(config) {
    experience.dataset.focus = "";
    const focus = config.focus;
    focusOriginX = focus.x;
    focusOriginY = focus.y;
    targetScale = reduceMotion ? 1.06 : focus.scale;
    targetX = focus.panX;
    targetY = focus.panY;
    requestAnimationFrame(() => requestAnimationFrame(() => {
        if (focused && activeModule) experience.dataset.focus = MODULES[activeModule].effect;
    }));
}

function resetView() {
    focused = false;
    activeModule = "";
    experience.dataset.focus = "";
    experience.classList.remove("has-focus");
    panel.setAttribute("aria-hidden", "true");
    targetScale = 1;
    targetX = 0;
    targetY = 0;
    focusOriginX = 50;
    focusOriginY = 50;
    stopSpeech();
    stopAssistantRecognition();
}

async function loadModule(key, options = {}) {
    const loaders = {
        weekly: loadWeekly,
        radio: loadRadioNews,
        inspirations: loadInspirations,
        archive: loadArchive,
        assistant: loadAssistant,
        memo: loadMemos,
        topics: loadTopics,
    };
    try {
        await loaders[key]?.(options);
    } catch (error) {
        if (activeModule === key) panelDemo.innerHTML = errorMarkup(`${MODULES[key].title}暂时没有加载成功`, humanError(error));
        console.warn(`空间模式 ${key} 加载失败`, error);
    }
}

async function runHeaderAction(kind) {
    const actions = {
        weekly: {
            primary: () => toggleForm("weekly"),
            secondary: () => loadWeekly({ quiet: true }),
        },
        radio: {
            primary: toggleRadioBriefing,
            secondary: refreshNews,
        },
        inspirations: {
            primary: syncIma,
            secondary: syncDouyin,
        },
        archive: {
            primary: () => toggleForm("archive"),
            secondary: () => loadArchive({ quiet: true }),
        },
        assistant: {
            primary: toggleAssistantBriefing,
            secondary: regenerateAssistant,
        },
        memo: {
            primary: () => toggleForm("memo"),
            secondary: () => loadMemos({ quiet: true }),
        },
        topics: {
            primary: () => toggleForm("topics"),
            secondary: () => loadTopics({ quiet: true }),
        },
    };
    const action = actions[activeModule]?.[kind];
    if (action) await action();
}

function toggleForm(key) {
    state.forms[key] = !state.forms[key];
    if (key === "weekly") renderWeekly();
    if (key === "archive") renderArchive();
    if (key === "memo") renderMemos();
    if (key === "topics") renderTopics();
    requestAnimationFrame(() => panelDemo.querySelector("form:not([hidden]) input, form:not([hidden]) textarea")?.focus());
}

function handlePanelClick(event) {
    const cancelButton = event.target.closest("[data-form-cancel]");
    if (cancelButton) {
        const key = cancelButton.dataset.formCancel;
        state.forms[key] = false;
        if (key === "weekly") renderWeekly();
        if (key === "archive") renderArchive();
        if (key === "memo") renderMemos();
        if (key === "topics") renderTopics();
        return;
    }
    const radioTab = event.target.closest("[data-radio-view]");
    if (radioTab) {
        state.radioView = radioTab.dataset.radioView;
        renderRadioPanel();
        return;
    }
    const ideaTab = event.target.closest("[data-idea-filter]");
    if (ideaTab) {
        state.inspirations.filter = ideaTab.dataset.ideaFilter;
        renderInspirations();
        return;
    }
    const topicTab = event.target.closest("[data-topic-account]");
    if (topicTab) {
        state.topics.account = topicTab.dataset.topicAccount;
        renderTopics();
        return;
    }
    const planButton = event.target.closest("[data-plan-status]");
    if (planButton) {
        updatePlanStatus(planButton.dataset.planId, planButton.dataset.planStatus, planButton);
        return;
    }
    const saveIdeaButton = event.target.closest("[data-save-idea]");
    if (saveIdeaButton) {
        saveIdea(saveIdeaButton.dataset.saveIdea, saveIdeaButton);
        return;
    }
    const revealButton = event.target.closest("[data-reveal-memo]");
    if (revealButton) {
        revealMemo(revealButton.dataset.revealMemo, revealButton);
        return;
    }
    const copyButton = event.target.closest("[data-copy-memo]");
    if (copyButton) {
        copyMemo(copyButton.dataset.copyMemo);
        return;
    }
    const topicButton = event.target.closest("[data-topic-move]");
    if (topicButton) {
        moveTopic(topicButton.dataset.topicId, topicButton.dataset.topicMove, topicButton);
        return;
    }
    const readButton = event.target.closest("[data-read-message]");
    if (readButton) {
        const message = state.assistant?.messages?.find(item => String(item.id) === readButton.dataset.readMessage);
        if (message) speakText(message.content, "朗读回答");
        return;
    }
    if (event.target.closest("[data-assistant-mic]")) toggleAssistantRecognition();
}

function handlePanelChange(event) {
    if (event.target.id === "ideaAccountSelect") state.inspirations.account = event.target.value;
}

function handlePanelSubmit(event) {
    event.preventDefault();
    if (event.target.id === "weeklyForm") createWeeklyPlan(event.target);
    if (event.target.id === "archiveForm") createArchiveEntry(event.target);
    if (event.target.id === "memoForm") createMemo(event.target);
    if (event.target.id === "topicForm") createTopic(event.target);
    if (event.target.id === "assistantForm") sendAssistantMessage(event.target);
}

async function loadWeekly({ quiet = false } = {}) {
    if (!quiet && activeModule === "weekly") panelDemo.innerHTML = loadingMarkup("正在读取本周计划");
    const plans = await apiJson("/api/weekly-plans");
    state.weekly = Array.isArray(plans) ? plans : [];
    renderDeskMonitor();
    if (activeModule === "weekly") renderWeekly();
}

function renderWeekly() {
    const counts = countBy(state.weekly, "status");
    const rows = state.weekly.slice(0, 14).map(plan => {
        const current = PLAN_FLOW.includes(plan.status) ? plan.status : "todo";
        const next = PLAN_FLOW[Math.min(PLAN_FLOW.indexOf(current) + 1, PLAN_FLOW.length - 1)];
        const nextLabel = current === "done" ? "恢复待办" : `标为${PLAN_LABELS[next]}`;
        const target = current === "done" ? "todo" : next;
        return `<article class="module-item ${current === "done" ? "is-done" : ""}">
            <span class="status-pill status-${escapeHtml(current)}">${escapeHtml(PLAN_LABELS[current])}</span>
            <div><strong>${escapeHtml(plan.title || "未命名任务")}</strong>${plan.description ? `<p>${escapeHtml(plan.description)}</p>` : ""}</div>
            <button class="mini-button" type="button" data-plan-id="${escapeHtml(plan.id)}" data-plan-status="${target}">${escapeHtml(nextLabel)}</button>
        </article>`;
    }).join("");
    panelDemo.innerHTML = `
        <div class="module-summary-grid three"><div><strong>${state.weekly.length}</strong><span>本周任务</span></div><div><strong>${counts.doing || 0}</strong><span>进行中</span></div><div><strong>${counts.done || 0}</strong><span>已完成</span></div></div>
        ${state.forms.weekly ? weeklyFormMarkup() : ""}
        <div class="module-list">${rows || emptyMarkup("本周还没有任务，先写下一件最重要的事。")}</div>`;
}

function weeklyFormMarkup() {
    return `<form id="weeklyForm" class="module-form">
        <label><span>任务</span><input name="title" maxlength="120" placeholder="例如：完成大号视频初稿" required></label>
        <label><span>补充说明</span><textarea name="description" rows="2" maxlength="500" placeholder="可不填"></textarea></label>
        <div class="form-actions"><button type="submit">保存任务</button><button type="button" data-form-cancel="weekly">取消</button></div>
    </form>`;
}

async function createWeeklyPlan(form) {
    const title = form.elements.title.value.trim();
    if (!title) return;
    const submit = form.querySelector("[type=submit]");
    setButtonBusy(submit, "保存中…", true);
    try {
        await apiJson("/api/weekly-plans", { method: "POST", body: { title, description: form.elements.description.value.trim(), week_start: currentWeekStart() } });
        state.forms.weekly = false;
        await loadWeekly({ quiet: true });
        showToast("任务已加入本周计划");
    } catch (error) {
        showToast(`保存失败：${humanError(error)}`);
        setButtonBusy(submit, "保存任务", false);
    }
}

async function updatePlanStatus(id, status, button) {
    setButtonBusy(button, "更新中…", true);
    try {
        await apiJson(`/api/weekly-plans/${encodeURIComponent(id)}`, { method: "PUT", body: { status } });
        await loadWeekly({ quiet: true });
        showToast(`任务已更新为${PLAN_LABELS[status]}`);
    } catch (error) {
        setButtonBusy(button, "重试", false);
        showToast(`更新失败：${humanError(error)}`);
    }
}

async function loadRadioNews({ quiet = false } = {}) {
    if (!quiet && activeModule === "radio") panelDemo.innerHTML = loadingMarkup("正在读取今天的事件");
    try {
        const payload = await apiJson("/api/hotspots/digest", { timeout: 15000 });
        const digest = payload.data || {};
        const all = payload.all || {};
        state.radio = {
            items: Array.isArray(digest.items) ? digest.items : [],
            total: Number(digest.total || 0),
            profileName: digest.profile_name || profileName,
            date: digest.date || all.date || "",
            summary: all.summary || {},
            cached: false,
            loadedAt: new Date().toISOString(),
        };
        localStorage.setItem(CACHE_KEY, JSON.stringify(state.radio));
    } catch (error) {
        const cached = readCachedDigest();
        if (!cached) throw error;
        state.radio = { ...cached, cached: true };
        showToast("新闻读取失败，已显示上次成功内容");
    }
    if (activeModule === "radio") renderRadioPanel();
}

function renderRadioPanel() {
    if (!state.radio) return;
    const allItems = state.radio.items.slice(0, 20);
    const visibleItems = state.radioView === "personal" ? allItems.slice(0, 5) : allItems;
    const sourceCount = Number(state.radio.summary.sources || 0);
    panelDemo.innerHTML = `
        <div class="radio-overview"><div><strong>${allItems.length}</strong><span>条今日简报</span></div><div><strong>${sourceCount || "—"}</strong><span>个新闻来源</span></div><small>${escapeHtml(formatUpdateTime(state.radio.loadedAt, state.radio.cached))}</small></div>
        <div class="module-tabs" role="tablist" aria-label="新闻摘要范围">
            <button type="button" role="tab" data-radio-view="personal" aria-selected="${state.radioView === "personal"}" class="${state.radioView === "personal" ? "active" : ""}">为你精选 5 条</button>
            <button type="button" role="tab" data-radio-view="all" aria-selected="${state.radioView === "all"}" class="${state.radioView === "all" ? "active" : ""}">快速浏览 20 条</button>
        </div>
        ${visibleItems.length ? `<ol class="radio-news-list">${visibleItems.map((item, index) => newsItemMarkup(item, index)).join("")}</ol>` : emptyMarkup("今天还没有可显示的新闻，请尝试刷新。")}
        ${state.radio.cached ? `<p class="radio-cache-note">当前为上次成功缓存，刷新成功后会自动替换。</p>` : ""}`;
}

function newsItemMarkup(item, index) {
    const source = repairMojibake(item.source || item.category || "热点");
    const reason = repairMojibake(item.reason || item.heat || "榜单靠前");
    const url = safeUrl(item.url);
    const title = escapeHtml(repairMojibake(item.title || "未命名新闻"));
    const titleNode = url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${title}</a>` : `<span>${title}</span>`;
    return `<li><b>${String(index + 1).padStart(2, "0")}</b><div>${titleNode}<small><em>${escapeHtml(source)}</em>${escapeHtml(reason)}</small></div></li>`;
}

async function refreshNews() {
    if (ui.secondary.disabled) return;
    stopSpeech();
    setButtonBusy(ui.secondary, "正在刷新…", true);
    panelDemo.classList.add("is-refreshing");
    try {
        await apiJson("/api/hotspots/refresh", { method: "POST", timeout: 15000 });
        showToast("正在更新全部新闻来源");
        const result = await waitForStatus("/api/hotspots/refresh-status", ["done"], ["error"], 70);
        if (result.result?.success === false) throw new Error(result.result.message || "刷新失败");
        await loadRadioNews({ quiet: true });
        showToast("今日新闻已更新");
    } catch (error) {
        showToast(`刷新失败：${humanError(error)}`);
    } finally {
        panelDemo.classList.remove("is-refreshing");
        setButtonBusy(ui.secondary, MODULES.radio.secondary, false);
    }
}

function toggleRadioBriefing() {
    const items = state.radio?.items?.slice(0, 5) || [];
    if (!items.length) return showToast("新闻加载完成后才能播放简报");
    const intro = `${state.radio.profileName}，这是今天最值得关注的五条新闻。`;
    const lines = items.map((item, index) => `第${index + 1}条，${repairMojibake(item.title)}。${repairMojibake(item.reason || "")}`);
    toggleSpeech([intro, ...lines, "以上是今天的简要播报。"].join("\n"), MODULES.radio.primary);
}

async function loadInspirations({ quiet = false } = {}) {
    if (!quiet && activeModule === "inspirations") panelDemo.innerHTML = loadingMarkup("正在整理 IMA 与抖音灵感");
    const payload = await apiJson("/api/idea-stream", { timeout: 20000 });
    state.inspirations.items = Array.isArray(payload.items) ? payload.items : [];
    state.inspirations.counts = payload.counts || {};
    if (activeModule === "inspirations") renderInspirations();
}

function renderInspirations() {
    const data = state.inspirations;
    const filtered = data.filter === "all" ? data.items : data.items.filter(item => item.source === data.filter);
    const rows = filtered.slice(0, 14).map(item => {
        const saved = Boolean(item.imported || item.saved);
        return `<article class="module-card">
            <div class="module-card-head"><span class="source-pill source-${escapeHtml(item.source)}">${escapeHtml(item.source_label || item.source)}</span><time>${escapeHtml(shortDate(item.time))}</time></div>
            <strong>${escapeHtml(item.title || "未命名灵感")}</strong>
            ${item.summary ? `<p>${escapeHtml(item.summary)}</p>` : ""}
            <div class="module-card-actions">${saved ? `<span class="saved-mark">已收选题</span>` : `<button class="mini-button" type="button" data-save-idea="${escapeHtml(item.id)}">收进${escapeHtml(ACCOUNT_LABELS[data.account])}</button>`}</div>
        </article>`;
    }).join("");
    panelDemo.innerHTML = `
        <div class="module-summary-grid three"><div><strong>${data.counts.all || data.items.length}</strong><span>全部灵感</span></div><div><strong>${data.counts.ima || 0}</strong><span>IMA</span></div><div><strong>${data.counts.douyin || 0}</strong><span>抖音近两天</span></div></div>
        <div class="module-toolbar"><div class="module-tabs compact">${["all", "ima", "douyin"].map(key => `<button type="button" data-idea-filter="${key}" class="${data.filter === key ? "active" : ""}">${key === "all" ? "全部" : key === "ima" ? "IMA" : "抖音"}</button>`).join("")}</div><label class="inline-select"><span>收进</span><select id="ideaAccountSelect"><option value="main" ${data.account === "main" ? "selected" : ""}>大号</option><option value="vlog" ${data.account === "vlog" ? "selected" : ""}>Vlog 小号</option></select></label></div>
        <div class="module-list cards">${rows || emptyMarkup("暂无灵感。可以点击下方按钮主动同步来源。")}</div>`;
}

async function saveIdea(id, button) {
    const item = state.inspirations.items.find(entry => String(entry.id) === String(id));
    if (!item) return;
    setButtonBusy(button, "保存中…", true);
    try {
        if (item.source === "ima") {
            await apiJson("/api/ima/import-topic", { method: "POST", body: { note_id: item.original_id, account_key: state.inspirations.account }, timeout: 20000 });
            item.imported = true;
        } else {
            await apiJson(`/api/douyin/inspirations/${encodeURIComponent(item.original_id)}/save`, { method: "POST", body: {}, timeout: 20000 });
            item.saved = true;
        }
        renderInspirations();
        showToast(`已收进${ACCOUNT_LABELS[state.inspirations.account]}选题库`);
    } catch (error) {
        setButtonBusy(button, "重试", false);
        showToast(`保存失败：${humanError(error)}`);
    }
}

async function syncIma() {
    setButtonBusy(ui.primary, "同步 IMA 中…", true);
    try {
        const payload = await apiJson("/api/ima/notes", { timeout: 60000 });
        await loadInspirations({ quiet: true });
        showToast(`IMA 同步完成，共 ${payload.notes?.length || state.inspirations.counts.ima || 0} 条`);
    } catch (error) {
        showToast(`IMA 同步失败：${humanError(error)}`);
    } finally {
        setButtonBusy(ui.primary, MODULES.inspirations.primary, false);
    }
}

async function syncDouyin() {
    setButtonBusy(ui.secondary, "同步抖音中…", true);
    try {
        const started = await apiJson("/api/douyin/sync", { method: "POST", body: {}, timeout: 20000 });
        if (started.status !== "already_running") showToast("正在同步最近三天收藏");
        const result = await waitForStatus("/api/douyin/sync-status", ["success"], ["error"], 80);
        await loadInspirations({ quiet: true });
        showToast(result.message || "抖音同步完成");
    } catch (error) {
        showToast(`抖音同步失败：${humanError(error)}`);
    } finally {
        setButtonBusy(ui.secondary, MODULES.inspirations.secondary, false);
    }
}

async function loadArchive({ quiet = false } = {}) {
    if (!quiet && activeModule === "archive") panelDemo.innerHTML = loadingMarkup("正在读取个人背景");
    const payload = await apiJson("/api/personal-memories?type=about", { timeout: 15000 });
    state.archive = { items: payload.items || [], stats: payload.stats || {}, categories: payload.categories || {} };
    if (activeModule === "archive") renderArchive();
}

function renderArchive() {
    const data = state.archive;
    const rows = data.items.slice(0, 14).map(item => `<article class="module-card ${item.pinned ? "is-pinned" : ""}">
        <div class="module-card-head"><span class="source-pill">${escapeHtml(item.category_label || "个人背景")}</span><span class="scope-pill">${escapeHtml(ACCOUNT_LABELS[item.account_scope] || "全部账号")}</span></div>
        <strong>${escapeHtml(item.title || "未命名资料")}</strong>
        <p>${escapeHtml(item.content_preview || item.content || "")}</p>
        <div class="module-card-meta"><span>${item.ai_enabled ? "AI 可引用" : "仅自己查看"}</span>${item.tags?.length ? `<span>${escapeHtml(item.tags.slice(0, 3).join(" · "))}</span>` : ""}</div>
    </article>`).join("");
    panelDemo.innerHTML = `
        <div class="module-summary-grid three"><div><strong>${data.stats.about_count || data.items.length}</strong><span>个人背景</span></div><div><strong>${data.stats.ai_count || 0}</strong><span>AI 可引用</span></div><div><strong>${data.stats.main_count || 0}/${data.stats.vlog_count || 0}</strong><span>大号 / 小号</span></div></div>
        ${state.forms.archive ? archiveFormMarkup(data.categories) : ""}
        <div class="module-list cards">${rows || emptyMarkup("还没有个人背景。写下经历、偏好或目标，AI 会更懂你。")}</div>`;
}

function archiveFormMarkup(categories) {
    const options = Object.entries(categories || {}).filter(([key]) => key !== "memo").map(([key, label]) => `<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`).join("");
    return `<form id="archiveForm" class="module-form">
        <div class="form-grid"><label><span>标题</span><input name="title" maxlength="120" placeholder="例如：我的内容风格" required></label><label><span>分类</span><select name="category">${options || '<option value="experience">经历</option>'}</select></label></div>
        <label><span>具体内容</span><textarea name="content" rows="4" maxlength="12000" placeholder="写下真实背景、经历、偏好、目标或边界" required></textarea></label>
        <div class="form-grid"><label><span>适用账号</span><select name="account_scope"><option value="all">全部账号</option><option value="main">大号</option><option value="vlog">Vlog 小号</option></select></label><label><span>标签</span><input name="tags" placeholder="AI、职场、表达"></label></div>
        <label class="check-line"><input type="checkbox" name="ai_enabled" checked><span>允许 AI 在相关问题和脚本中引用</span></label>
        <div class="form-actions"><button type="submit">保存个人背景</button><button type="button" data-form-cancel="archive">取消</button></div>
    </form>`;
}

async function createArchiveEntry(form) {
    const payload = {
        item_type: "about", title: form.elements.title.value.trim(), content: form.elements.content.value.trim(),
        category: form.elements.category.value, account_scope: form.elements.account_scope.value,
        tags: form.elements.tags.value, ai_enabled: form.elements.ai_enabled.checked,
    };
    if (!payload.content) return;
    const button = form.querySelector("[type=submit]");
    setButtonBusy(button, "保存中…", true);
    try {
        await apiJson("/api/personal-memories", { method: "POST", body: payload });
        state.forms.archive = false;
        await loadArchive({ quiet: true });
        showToast("个人背景已保存到本机");
    } catch (error) {
        setButtonBusy(button, "保存个人背景", false);
        showToast(`保存失败：${humanError(error)}`);
    }
}

async function loadMemos({ quiet = false } = {}) {
    if (!quiet && activeModule === "memo") panelDemo.innerHTML = loadingMarkup("正在打开本地备忘录");
    const payload = await apiJson("/api/personal-memories?type=memo", { timeout: 15000 });
    state.memo = { items: payload.items || [], stats: payload.stats || {} };
    if (activeModule === "memo") renderMemos();
}

function renderMemos() {
    const data = state.memo;
    const rows = data.items.slice(0, 16).map(item => {
        const revealed = revealedMemos.get(String(item.id));
        const content = revealed || item.content || item.content_preview || "";
        return `<article class="module-card memo-card ${item.is_private ? "is-private" : ""} ${item.pinned ? "is-pinned" : ""}">
            <div class="module-card-head"><span class="source-pill">${item.is_private ? "私密" : "普通"}</span><time>${escapeHtml(shortDate(item.updated_at))}</time></div>
            <strong>${escapeHtml(item.title || "未命名备忘录")}</strong>
            <p class="memo-content">${escapeHtml(item.is_private && !revealed ? "内容已隐藏，点击解锁后查看" : content)}</p>
            <div class="module-card-actions">${item.is_private && !revealed ? `<button class="mini-button" type="button" data-reveal-memo="${escapeHtml(item.id)}">解锁查看</button>` : `<button class="mini-button" type="button" data-copy-memo="${escapeHtml(item.id)}">复制内容</button>`}</div>
        </article>`;
    }).join("");
    panelDemo.innerHTML = `
        <div class="module-summary-grid three"><div><strong>${data.stats.memo_count || data.items.length}</strong><span>备忘录</span></div><div><strong>${data.stats.private_count || 0}</strong><span>私密内容</span></div><div><strong>本机</strong><span>保存位置</span></div></div>
        ${state.forms.memo ? memoFormMarkup() : ""}
        <div class="module-list cards">${rows || emptyMarkup("还没有备忘录。可以记录常用提示词、文字或账号信息。")}</div>`;
}

function memoFormMarkup() {
    return `<form id="memoForm" class="module-form">
        <label><span>标题</span><input name="title" maxlength="120" placeholder="例如：常用视频提示词" required></label>
        <label><span>内容</span><textarea name="content" rows="5" maxlength="12000" placeholder="内容只保存在当前电脑" required></textarea></label>
        <div class="check-grid"><label class="check-line"><input type="checkbox" name="is_private"><span>设为私密，使用当前 Windows 账户加密</span></label><label class="check-line"><input type="checkbox" name="pinned"><span>置顶</span></label></div>
        <div class="form-actions"><button type="submit">保存备忘录</button><button type="button" data-form-cancel="memo">取消</button></div>
    </form>`;
}

async function createMemo(form) {
    const payload = {
        item_type: "memo", title: form.elements.title.value.trim(), content: form.elements.content.value.trim(),
        is_private: form.elements.is_private.checked, pinned: form.elements.pinned.checked, ai_enabled: false,
    };
    if (!payload.content) return;
    const button = form.querySelector("[type=submit]");
    setButtonBusy(button, "保存中…", true);
    try {
        await apiJson("/api/personal-memories", { method: "POST", body: payload });
        state.forms.memo = false;
        await loadMemos({ quiet: true });
        showToast(payload.is_private ? "私密备忘录已加密保存" : "备忘录已保存到本机");
    } catch (error) {
        setButtonBusy(button, "保存备忘录", false);
        showToast(`保存失败：${humanError(error)}`);
    }
}

async function revealMemo(id, button) {
    setButtonBusy(button, "解锁中…", true);
    try {
        const payload = await apiJson(`/api/personal-memories/${encodeURIComponent(id)}/private-content`, { method: "POST", body: {} });
        revealedMemos.set(String(id), payload.content || "");
        renderMemos();
        showToast("已在本次页面中临时解锁");
    } catch (error) {
        setButtonBusy(button, "重试", false);
        showToast(`解锁失败：${humanError(error)}`);
    }
}

async function copyMemo(id) {
    const item = state.memo.items.find(entry => String(entry.id) === String(id));
    const text = revealedMemos.get(String(id)) || item?.content || "";
    if (!text) return showToast("没有可复制的内容");
    try {
        await navigator.clipboard.writeText(text);
        showToast("内容已复制");
    } catch {
        const area = document.createElement("textarea");
        area.value = text;
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
        showToast("内容已复制");
    }
}

async function loadAssistant({ quiet = false } = {}) {
    if (!quiet && activeModule === "assistant") panelDemo.innerHTML = loadingMarkup(`${assistantName}正在读取工作台`);
    state.assistant = await apiJson("/api/assistant/overview", { timeout: 25000 });
    if (activeModule === "assistant") renderAssistant();
}

function renderAssistant() {
    const data = state.assistant || {};
    const briefing = data.briefing || {};
    const messages = (data.messages || []).slice(-8);
    const newsCount = briefing.news?.length || data.context?.news?.length || 0;
    const taskCount = briefing.tasks?.length || data.context?.tasks?.length || 0;
    const inspirationCount = briefing.inspirations?.length || 0;
    const highlights = [
        ...(briefing.news || []).slice(0, 2).map(item => ({ label: "新闻", title: item.title, note: item.note })),
        ...(briefing.tasks || []).slice(0, 2).map(item => ({ label: "任务", title: item.title, note: item.note })),
    ];
    const chatRows = messages.map(message => `<article class="chat-message ${message.role === "user" ? "is-user" : "is-assistant"}">
        <span>${message.role === "user" ? "你" : escapeHtml(assistantName)}</span><p>${escapeHtml(message.content || "")}</p>
        ${message.role === "assistant" ? `<button type="button" data-read-message="${escapeHtml(message.id)}">朗读</button>` : ""}
    </article>`).join("");
    panelDemo.innerHTML = `
        <div class="module-summary-grid three"><div><strong>${newsCount}</strong><span>新闻</span></div><div><strong>${taskCount}</strong><span>任务</span></div><div><strong>${inspirationCount}</strong><span>灵感</span></div></div>
        <section class="assistant-brief"><span>今日焦点</span><strong>${escapeHtml(briefing.focus || briefing.title || "今天先完成一件可交付的事")}</strong>${briefing.opening ? `<p>${escapeHtml(briefing.opening)}</p>` : ""}</section>
        ${highlights.length ? `<div class="assistant-highlights">${highlights.map(item => `<div><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.title || "")}</strong><p>${escapeHtml(item.note || "")}</p></div>`).join("")}</div>` : ""}
        <div class="chat-list">${chatRows || emptyMarkup("还没有对话。可以直接问：我今天先做什么？")}</div>
        <form id="assistantForm" class="assistant-form"><button class="mic-button" type="button" data-assistant-mic aria-label="语音输入">麦</button><input name="message" maxlength="2000" placeholder="问${escapeHtml(assistantName)}：我今天先做什么？" required><button type="submit">发送</button></form>
        <p class="inline-note">语音只在点击“麦”后启用；AI 不可用时会自动使用本地数据回答。</p>`;
    requestAnimationFrame(() => {
        const list = panelDemo.querySelector(".chat-list");
        if (list) list.scrollTop = list.scrollHeight;
    });
}

async function sendAssistantMessage(form) {
    const input = form.elements.message;
    const message = input.value.trim();
    if (!message) return;
    const button = form.querySelector("[type=submit]");
    setButtonBusy(button, "整理中…", true);
    try {
        const payload = await apiJson("/api/assistant/chat", { method: "POST", body: { message }, timeout: 90000 });
        input.value = "";
        await loadAssistant({ quiet: true });
        showToast(payload.mode === "ai" ? `${assistantName}已结合 AI 与本地数据回答` : `${assistantName}已使用本地数据回答`);
    } catch (error) {
        setButtonBusy(button, "发送", false);
        showToast(`发送失败：${humanError(error)}`);
    }
}

async function regenerateAssistant() {
    setButtonBusy(ui.secondary, "正在整理…", true);
    try {
        const payload = await apiJson("/api/assistant/briefing", { method: "POST", body: { use_ai: true }, timeout: 90000 });
        state.assistant = { ...(state.assistant || {}), ...payload, messages: state.assistant?.messages || [] };
        if (activeModule === "assistant") renderAssistant();
        showToast("今日汇报已重新整理");
    } catch (error) {
        showToast(`整理失败：${humanError(error)}`);
    } finally {
        setButtonBusy(ui.secondary, MODULES.assistant.secondary, false);
    }
}

function toggleAssistantBriefing() {
    const text = state.assistant?.briefing?.speech;
    if (!text) return showToast("今日汇报还没有准备好");
    toggleSpeech(text, MODULES.assistant.primary);
}

function toggleAssistantRecognition() {
    if (assistantListening) {
        stopAssistantRecognition();
        return;
    }
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) return showToast("当前浏览器不支持语音识别，请使用最新版 Edge 或 Chrome");
    assistantRecognition = new Recognition();
    assistantRecognition.lang = "zh-CN";
    assistantRecognition.interimResults = false;
    assistantRecognition.continuous = false;
    assistantRecognition.onstart = () => {
        assistantListening = true;
        const button = panelDemo.querySelector("[data-assistant-mic]");
        if (button) { button.textContent = "停"; button.classList.add("is-listening"); }
        showToast("正在听，请开始说话");
    };
    assistantRecognition.onresult = event => {
        const input = panelDemo.querySelector('#assistantForm input[name="message"]');
        if (input) input.value = Array.from(event.results).map(result => result[0].transcript).join("");
    };
    assistantRecognition.onerror = event => showToast(event.error === "not-allowed" ? "没有获得麦克风权限" : "语音识别没有成功，请重试");
    assistantRecognition.onend = stopAssistantRecognition;
    assistantRecognition.start();
}

function stopAssistantRecognition() {
    if (assistantRecognition && assistantListening) {
        try { assistantRecognition.stop(); } catch { /* noop */ }
    }
    assistantListening = false;
    const button = panelDemo.querySelector("[data-assistant-mic]");
    if (button) { button.textContent = "麦"; button.classList.remove("is-listening"); }
}

async function loadTopics({ quiet = false } = {}) {
    if (!quiet && activeModule === "topics") panelDemo.innerHTML = loadingMarkup("正在读取选题与脚本");
    const payload = await apiJson("/api/topics", { timeout: 20000 });
    state.topics.items = Array.isArray(payload) ? payload : [];
    if (activeModule === "topics") renderTopics();
}

function renderTopics() {
    const account = state.topics.account;
    const items = state.topics.items.filter(item => item.account_key === account);
    const counts = countBy(items, "status");
    const activeItems = items.filter(item => !["done", "abandoned"].includes(item.status));
    const rows = (activeItems.length ? activeItems : items).slice(0, 14).map(item => {
        const current = TOPIC_FLOW.includes(item.status) ? item.status : "idea";
        const index = TOPIC_FLOW.indexOf(current);
        const previous = index > 0 ? TOPIC_FLOW[index - 1] : "";
        const next = index >= 0 && index < TOPIC_FLOW.length - 1 ? TOPIC_FLOW[index + 1] : "";
        return `<article class="module-card topic-card">
            <div class="module-card-head"><span class="status-pill status-${escapeHtml(current)}">${escapeHtml(TOPIC_LABELS[current] || current)}</span><span>${item.scripts?.length || 0} 个脚本</span></div>
            <strong>${escapeHtml(item.title || "未命名选题")}</strong>
            ${item.angle ? `<p>${escapeHtml(item.angle)}</p>` : ""}
            <div class="module-card-actions">${previous ? `<button class="mini-button" type="button" data-topic-id="${escapeHtml(item.id)}" data-topic-move="${previous}">← ${escapeHtml(TOPIC_LABELS[previous])}</button>` : ""}${next ? `<button class="mini-button accent" type="button" data-topic-id="${escapeHtml(item.id)}" data-topic-move="${next}">${escapeHtml(TOPIC_LABELS[next])} →</button>` : ""}${item.scripts?.length ? `<a class="mini-link" href="/?tab=scripts">进入脚本</a>` : ""}</div>
        </article>`;
    }).join("");
    panelDemo.innerHTML = `
        <div class="module-tabs account-tabs"><button type="button" data-topic-account="main" class="${account === "main" ? "active" : ""}">大号 · ${state.topics.items.filter(item => item.account_key === "main").length}</button><button type="button" data-topic-account="vlog" class="${account === "vlog" ? "active" : ""}">Vlog 小号 · ${state.topics.items.filter(item => item.account_key === "vlog").length}</button></div>
        <div class="module-summary-grid three"><div><strong>${counts.idea || 0}</strong><span>点子</span></div><div><strong>${(counts.confirmed || 0) + (counts.scripting || 0)}</strong><span>筹备中</span></div><div><strong>${counts.done || 0}</strong><span>已发布</span></div></div>
        ${state.forms.topics ? topicFormMarkup(account) : ""}
        <div class="module-list cards">${rows || emptyMarkup(`${ACCOUNT_LABELS[account]}还没有选题。`)}</div>`;
}

function topicFormMarkup(account) {
    return `<form id="topicForm" class="module-form">
        <div class="form-grid"><label><span>账号</span><select name="account_key"><option value="main" ${account === "main" ? "selected" : ""}>大号</option><option value="vlog" ${account === "vlog" ? "selected" : ""}>Vlog 小号</option></select></label><label><span>来源</span><input name="source" placeholder="日常、IMA、热点"></label></div>
        <label><span>选题名称</span><input name="title" maxlength="160" placeholder="这个选题要解决观众什么问题？" required></label>
        <label><span>内容角度</span><textarea name="angle" rows="3" maxlength="1000" placeholder="为什么值得做、准备怎么讲"></textarea></label>
        <div class="form-actions"><button type="submit">保存选题</button><button type="button" data-form-cancel="topics">取消</button></div>
    </form>`;
}

async function createTopic(form) {
    const payload = {
        account_key: form.elements.account_key.value, title: form.elements.title.value.trim(),
        source: form.elements.source.value.trim(), angle: form.elements.angle.value.trim(), status: "idea",
    };
    if (!payload.title) return;
    const button = form.querySelector("[type=submit]");
    setButtonBusy(button, "保存中…", true);
    try {
        await apiJson("/api/topics", { method: "POST", body: payload });
        state.topics.account = payload.account_key;
        state.forms.topics = false;
        await loadTopics({ quiet: true });
        showToast(`选题已加入${ACCOUNT_LABELS[payload.account_key]}`);
    } catch (error) {
        setButtonBusy(button, "保存选题", false);
        showToast(`保存失败：${humanError(error)}`);
    }
}

async function moveTopic(id, status, button) {
    setButtonBusy(button, "更新中…", true);
    try {
        await apiJson(`/api/topics/${encodeURIComponent(id)}`, { method: "PUT", body: { status } });
        await loadTopics({ quiet: true });
        showToast(`选题已调整为“${TOPIC_LABELS[status]}”`);
    } catch (error) {
        setButtonBusy(button, "重试", false);
        showToast(`更新失败：${humanError(error)}`);
    }
}

function toggleSpeech(text, defaultLabel) {
    if (!("speechSynthesis" in window)) return showToast("当前浏览器不支持语音播报");
    if (speechSynthesis.speaking) {
        stopSpeech();
        return;
    }
    speakText(text, defaultLabel);
}

function speakText(text, defaultLabel = "播放") {
    if (!("speechSynthesis" in window) || !text) return;
    stopSpeech();
    const run = ++speechRun;
    const utterance = new SpeechSynthesisUtterance(String(text));
    utterance.lang = "zh-CN";
    utterance.rate = .92;
    utterance.pitch = 1.02;
    utterance.volume = 1;
    utterance.voice = pickChineseVoice();
    utterance.onstart = () => {
        if (run !== speechRun) return;
        setHeaderButton(ui.primary, "停止播报");
        ui.primary.classList.add("is-speaking");
        showXiaoliSpeech("正在为你播报。再次点击可以停止。");
    };
    utterance.onend = utterance.onerror = () => {
        if (run !== speechRun) return;
        setHeaderButton(ui.primary, defaultLabel);
        ui.primary.classList.remove("is-speaking");
    };
    speechSynthesis.speak(utterance);
}

function stopSpeech() {
    speechRun += 1;
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    if (activeModule && MODULES[activeModule]) setHeaderButton(ui.primary, MODULES[activeModule].primary);
    ui.primary.classList.remove("is-speaking");
}

function pickChineseVoice() {
    const voices = speechSynthesis.getVoices();
    const chinese = voices.filter(voice => /^zh/i.test(voice.lang));
    return chinese.find(voice => /Xiaoxiao|Yunxi|Natural|晓晓|云希/i.test(voice.name)) || chinese[0] || null;
}

async function apiJson(path, { method = "GET", timeout = 10000, body } = {}) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeout);
    try {
        const options = {
            method,
            cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            signal: controller.signal,
        };
        if (body !== undefined) options.body = JSON.stringify(body);
        const response = await fetch(path, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload?.ok === false) throw new Error(payload?.error || payload?.message || `HTTP ${response.status}`);
        return payload;
    } finally {
        window.clearTimeout(timer);
    }
}

async function waitForStatus(path, successStates, errorStates, attempts = 60) {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
        await delay(attempt < 4 ? 700 : 1200);
        const payload = await apiJson(path, { timeout: 10000 });
        if (successStates.includes(payload.status)) return payload;
        if (errorStates.includes(payload.status)) throw new Error(payload.message || "操作失败");
    }
    throw new Error("处理时间较长，请稍后刷新查看");
}

function readCachedDigest() {
    try {
        const value = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
        return value && Array.isArray(value.items) ? value : null;
    } catch {
        return null;
    }
}

function startLoader() {
    let displayed = 0;
    let finished = image.complete;
    const complete = () => { finished = true; };
    if (!image.complete) image.addEventListener("load", complete, { once: true });
    image.addEventListener("error", () => { loadPercent.textContent = "场景资源加载失败"; }, { once: true });
    const tick = () => {
        const cap = finished ? 100 : 92;
        displayed += Math.max(1, Math.round((cap - displayed) * .12));
        displayed = Math.min(displayed, cap);
        loadPercent.textContent = `${displayed}%`;
        if (displayed >= 100) return window.setTimeout(() => loader.classList.add("is-hidden"), 180);
        window.setTimeout(tick, 42);
    };
    tick();
}

function animate() {
    currentX += (targetX - currentX) * (reduceMotion ? 1 : .065);
    currentY += (targetY - currentY) * (reduceMotion ? 1 : .065);
    currentScale += (targetScale - currentScale) * (reduceMotion ? 1 : .065);
    scene.style.transformOrigin = `${focusOriginX}% ${focusOriginY}%`;
    scene.style.transform = `translate3d(${currentX}px, ${currentY}px, 0) scale(${currentScale})`;
    requestAnimationFrame(animate);
}

function ensureAudio() {
    if (audioState) return audioState;
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return null;
    const context = new AudioContext();
    const master = context.createGain();
    master.gain.value = 0;
    master.connect(context.destination);
    const hum = context.createOscillator();
    hum.type = "sine";
    hum.frequency.value = 73.42;
    const humGain = context.createGain();
    humGain.gain.value = .024;
    hum.connect(humGain).connect(master);
    hum.start();
    audioState = { context, master, enabled: false };
    return audioState;
}

function toggleSound() {
    const audio = ensureAudio();
    if (!audio) return showToast("当前浏览器不支持环境音");
    if (audio.context.state === "suspended") audio.context.resume();
    audio.enabled = !audio.enabled;
    audio.master.gain.setTargetAtTime(audio.enabled ? .34 : 0, audio.context.currentTime, .12);
    ui.sound.setAttribute("aria-pressed", String(audio.enabled));
    ui.sound.querySelector("span").textContent = audio.enabled ? "环境音开启" : "环境音关闭";
}

function playChime() {
    const audio = ensureAudio();
    if (!audio || !audio.enabled) return;
    const now = audio.context.currentTime;
    [392, 523.25].forEach((frequency, index) => {
        const oscillator = audio.context.createOscillator();
        const gain = audio.context.createGain();
        oscillator.type = "sine";
        oscillator.frequency.value = frequency;
        gain.gain.setValueAtTime(0, now + index * .08);
        gain.gain.linearRampToValueAtTime(.045, now + .025 + index * .08);
        gain.gain.exponentialRampToValueAtTime(.0001, now + .45 + index * .08);
        oscillator.connect(gain).connect(audio.master);
        oscillator.start(now + index * .08);
        oscillator.stop(now + .5 + index * .08);
    });
}

function showXiaoliSpeech(text) {
    speechBubble.querySelector("p").textContent = text;
    speechBubble.classList.remove("is-visible");
    requestAnimationFrame(() => speechBubble.classList.add("is-visible"));
}

function showToast(message) {
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.add("is-visible");
    toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 3200);
}

function setHeaderButton(button, label) {
    button.textContent = label;
    button.dataset.defaultLabel = label;
}

function setButtonBusy(button, label, busy) {
    if (!button) return;
    button.disabled = busy;
    button.textContent = label;
}

function countBy(items, key) {
    return (items || []).reduce((result, item) => {
        const value = item[key] || "unknown";
        result[value] = (result[value] || 0) + 1;
        return result;
    }, {});
}

function currentWeekStart() {
    const now = new Date();
    const day = (now.getDay() + 6) % 7;
    now.setDate(now.getDate() - day);
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function shortDate(value) {
    if (!value) return "刚刚";
    const date = new Date(String(value).replace(" ", "T"));
    if (Number.isNaN(date.getTime())) return String(value).slice(5, 16);
    return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function loadingMarkup(label) {
    return `<div class="live-loading" aria-label="${escapeHtml(label)}"><i></i><i></i><i></i><span>${escapeHtml(label)}</span></div>`;
}

function emptyMarkup(text) {
    return `<div class="live-empty">${escapeHtml(text)}</div>`;
}

function errorMarkup(title, detail) {
    return `<div class="live-error"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div>`;
}

function formatUpdateTime(value, cached) {
    const date = new Date(value || Date.now());
    const time = Number.isNaN(date.getTime()) ? "刚刚" : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    return `${cached ? "缓存于" : "更新于"} ${time}`;
}

function safeUrl(value) {
    try {
        const url = new URL(String(value || ""), window.location.origin);
        return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch {
        return "";
    }
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function repairMojibake(value) {
    const text = String(value ?? "");
    if (!/[ÃÂâåæç]/.test(text)) return text;
    try {
        const bytes = Uint8Array.from(Array.from(text), character => {
            const code = character.charCodeAt(0);
            if (code > 255) throw new Error("not latin-1 mojibake");
            return code;
        });
        const repaired = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
        const originalChinese = (text.match(/[\u3400-\u9fff]/g) || []).length;
        const repairedChinese = (repaired.match(/[\u3400-\u9fff]/g) || []).length;
        return repairedChinese > originalChinese ? repaired : text;
    } catch {
        return text;
    }
}

function humanError(error) {
    if (error?.name === "AbortError") return "连接超时";
    return String(error?.message || "未知错误").slice(0, 100);
}

function isTypingTarget(target) {
    return target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

function delay(ms) {
    return new Promise(resolve => window.setTimeout(resolve, ms));
}
