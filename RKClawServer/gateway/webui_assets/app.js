const state = { csrf: "", view: "dashboard", range: "24h", sessionCursor: "", sessionDetail: null, sessionLogMode: "api", expandedSessionRequests: new Set(), logsPaused: false, config: null, devices: [], fileTarget: null, fileMode: "file", fileExtensions: [], fileBrowser: null, deployment: null, timers: {} };
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (state.csrf && options.method && options.method !== "GET") headers["X-CSRF-Token"] = state.csrf;
  const response = await fetch(path, Object.assign({ credentials: "same-origin" }, options, { headers }));
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) {
    const error = new Error(payload.error?.message || "请求失败");
    error.code = payload.error?.code; error.status = response.status; throw error;
  }
  return payload;
}
function toast(message, error = false) {
  const node = $("#toast"); node.textContent = message; node.classList.toggle("error", error); node.classList.add("show");
  clearTimeout(state.timers.toast); state.timers.toast = setTimeout(() => node.classList.remove("show"), 2800);
}
function showLogin() { $("#login-view").classList.remove("hidden"); $("#app-view").classList.add("hidden"); state.csrf = ""; }
function showApp() { $("#login-view").classList.add("hidden"); $("#app-view").classList.remove("hidden"); loadDashboard(); }
async function bootstrap() {
  try { const session = await api("/api/webui/auth/session"); state.csrf = session.csrf_token; showApp(); }
  catch (_) { showLogin(); }
}
$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("#login-error").textContent = "";
  try {
    const result = await api("/api/webui/auth/login", { method: "POST", body: JSON.stringify({ token: $("#token").value }) });
    state.csrf = result.csrf_token; $("#token").value = ""; showApp();
  } catch (error) { $("#login-error").textContent = error.message; }
});
$("#logout").addEventListener("click", async () => {
  try { await api("/api/webui/auth/logout", { method: "POST" }); } catch (_) {}
  showLogin();
});

const titles = { dashboard: "Dashboard", sessions: "会话日志", logs: "Server 日志", config: "Server 配置" };
$$(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
function switchView(view) {
  state.view = view;
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach((item) => item.classList.remove("active-view"));
  $("#view-" + view).classList.add("active-view");
  $("#page-title").textContent = titles[view]; refreshCurrent();
}
$("#refresh").addEventListener("click", refreshCurrent);
function refreshCurrent() {
  if (state.view === "dashboard") loadDashboard();
  if (state.view === "sessions") loadSessions(true);
  if (state.view === "logs") loadLogs();
  if (state.view === "config") loadConfig();
}
$$("[data-range]").forEach((button) => button.addEventListener("click", () => {
  state.range = button.dataset.range;
  $$("[data-range]").forEach((item) => item.classList.toggle("active", item === button));
  loadDashboard();
}));

async function loadDashboard() {
  try {
    const payload = await api("/api/webui/dashboard?range=" + state.range);
    renderServer(payload.server); renderMetrics(payload.totals); drawTrend(payload.trend || []);
  } catch (error) { if (error.status === 401) return showLogin(); toast(error.message, true); }
}
function renderServer(server) {
  const stateName = server.ready ? "ready" : server.state;
  const ready = stateName === "ready", indicatorClass = serverStateClass(stateName);
  $("#runtime-state").textContent = server.state;
  $("#runtime-copy").textContent = ready ? "模型已加载，OpenAI API 正常接收推理请求。" : (server.last_error || "服务正在切换运行状态。");
  $("#runtime-dot").className = indicatorClass;
  $("#side-dot").className = indicatorClass;
  $("#side-status").textContent = serverStateLabel(stateName);
  $("#runtime-model").textContent = server.model; $("#active-count").textContent = server.active_requests;
  $("#queued-count").textContent = server.queued_requests;
  $("#queue-fill").style.width = Math.min(100, server.queued_requests * 12) + "%";
  $("#uptime").textContent = duration(server.uptime_s);
}
function serverStateClass(stateName) {
  if (stateName === "ready") return "state-ready";
  if (stateName === "failed") return "state-failed";
  if (stateName === "draining" || stateName === "reloading") return "state-warning";
  if (stateName === "stopped") return "state-inactive";
  return "state-loading";
}
function serverStateLabel(stateName) {
  return ({ ready: "服务就绪", starting: "正在启动", draining: "正在排空请求", reloading: "正在重新加载", failed: "服务异常", stopped: "服务已停止" })[stateName] || "正在连接";
}
function renderMetrics(totals = {}) {
  const metrics = [
    ["请求", number(totals.requests), "选定时间范围"],
    ["成功率", (totals.success_rate || 0).toFixed(1) + "%", number(totals.success) + " 成功"],
    ["输入 Tokens", number(totals.input_tokens), "累计 prompt"],
    ["输出 Tokens", number(totals.output_tokens), "累计 completion"],
    ["平均延迟", formatMs(totals.average_latency_ms), "峰值 " + formatMs(totals.max_latency_ms)],
  ];
  const grid = $("#metric-grid"); grid.replaceChildren();
  metrics.forEach(([label, value, note]) => {
    const card = document.createElement("article"); card.className = "metric panel";
    const p = document.createElement("p"); p.textContent = label;
    const strong = document.createElement("strong"); strong.textContent = value;
    const small = document.createElement("small"); small.textContent = note;
    card.append(p, strong, small); grid.append(card);
  });
}
function drawTrend(rows) {
  const canvas = $("#trend-chart"), ratio = window.devicePixelRatio || 1, width = canvas.clientWidth || 700, height = 260;
  canvas.width = width * ratio; canvas.height = height * ratio;
  const ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio); ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "#e4e4e7"; ctx.lineWidth = 1;
  for (let line = 0; line < 5; line++) { const y = 18 + line * 52; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke(); }
  if (!rows.length) { ctx.fillStyle = "#71717a"; ctx.font = "12px system-ui"; ctx.fillText("暂无请求数据", 16, 38); return; }
  const max = Math.max(1, ...rows.map((row) => row.requests));
  const points = rows.map((row, index) => ({ x: rows.length === 1 ? width / 2 : index * width / (rows.length - 1), y: height - 22 - (row.requests / max) * (height - 48) }));
  const gradient = ctx.createLinearGradient(0, 0, 0, height); gradient.addColorStop(0, "rgba(239,63,45,.20)"); gradient.addColorStop(1, "rgba(239,63,45,0)");
  ctx.beginPath(); points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  ctx.lineTo(points[points.length - 1].x, height); ctx.lineTo(points[0].x, height); ctx.closePath(); ctx.fillStyle = gradient; ctx.fill();
  ctx.beginPath(); points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); ctx.strokeStyle = "#ef3f2d"; ctx.lineWidth = 2; ctx.stroke();
}

$("#search-sessions").addEventListener("click", () => loadSessions(true));
$("#more-sessions").addEventListener("click", () => loadSessions(false));
async function loadSessions(reset = true) {
  if (reset) { state.sessionCursor = ""; $("#session-rows").replaceChildren(); }
  const params = new URLSearchParams({ cursor: state.sessionCursor, query: $("#session-query").value, status: $("#session-status").value });
  try {
    const payload = await api("/api/webui/sessions?" + params);
    $("#session-disabled").classList.toggle("hidden", payload.enabled);
    $("#session-disabled").textContent = "会话正文记录当前未开启。请在 [logging] 中设置 session_logs_enabled = true 后重新加载配置。";
    payload.items.forEach(renderSessionRow); state.sessionCursor = payload.next_cursor || "";
    $("#more-sessions").classList.toggle("hidden", !payload.next_cursor);
  } catch (error) { toast(error.message, true); }
}
function renderSessionRow(item) {
  const tr = document.createElement("tr");
  const values = ["session", item.model, number(item.request_count), number(item.input_tokens), number(item.output_tokens), item.status, dateTime(item.last_activity)];
  values.forEach((value, index) => {
    const td = document.createElement("td");
    if (index === 0) {
      const title = document.createElement("div"); title.className = "session-title"; title.textContent = item.preview;
      const id = document.createElement("div"); id.className = "session-id"; id.textContent = item.id.slice(0, 12); td.append(title, id);
    } else if (index === 5) {
      const badge = document.createElement("span"); badge.className = "badge " + item.status; badge.textContent = item.status; td.append(badge);
    } else td.textContent = value;
    tr.append(td);
  });
  tr.addEventListener("click", () => openSession(item.id)); $("#session-rows").append(tr);
}
async function openSession(id) {
  try {
    state.sessionDetail = await api("/api/webui/sessions/" + encodeURIComponent(id));
    state.expandedSessionRequests.clear();
    $("#dialog-title").textContent = state.sessionDetail.preview;
    setSessionLogMode("api");
    $("#session-dialog").showModal();
  } catch (error) { toast(error.message, true); }
}
function setSessionLogMode(mode) {
  state.sessionLogMode = mode;
  [["api", $("#session-api-tab")], ["model", $("#session-model-tab")]].forEach(([name, button]) => {
    const active = name === mode; button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active));
  });
  renderSessionDetail();
}
function renderSessionDetail() {
  const detail = $("#session-detail"); detail.replaceChildren();
  if (!state.sessionDetail) return;
  state.sessionDetail.requests.forEach((request, index) => {
    const requestKey = request.id || String(index);
    const card = document.createElement("details"); card.className = "request-card request-log-entry"; card.open = state.expandedSessionRequests.has(requestKey);
    const summary = document.createElement("summary"); summary.className = "request-summary";
    const sequence = document.createElement("strong"); sequence.className = "request-sequence"; sequence.textContent = "请求 #" + (index + 1);
    const time = document.createElement("span"); time.className = "request-time"; time.textContent = dateTime(request.created_at);
    const status = document.createElement("span"); status.className = "badge " + request.status; status.textContent = request.status;
    const latency = document.createElement("span"); latency.className = "request-stat"; latency.textContent = Math.round(request.latency_ms) + "ms";
    const inputTokens = document.createElement("span"); inputTokens.className = "request-stat"; inputTokens.textContent = "INPUT TOKENS " + number(request.input_tokens);
    const outputTokens = document.createElement("span"); outputTokens.className = "request-stat"; outputTokens.textContent = "OUTPUT TOKENS " + number(request.output_tokens);
    const toggle = document.createElement("span"); toggle.className = "request-toggle"; toggle.textContent = "›"; toggle.setAttribute("aria-hidden", "true");
    summary.append(sequence, time, status, latency, inputTokens, outputTokens, toggle);
    const payloads = document.createElement("div"); payloads.className = "request-payloads";
    if (state.sessionLogMode === "api") {
      payloads.append(payloadBlock("API REQUEST", request.request));
      if (request.response !== null) payloads.append(payloadBlock("API RESPONSE", request.response));
    } else {
      payloads.append(payloadBlock("MODEL INPUT", request.model_input !== null ? request.model_input : "该请求没有 MODEL INPUT 记录（旧会话或在模型调用前失败）。"));
      payloads.append(payloadBlock("MODEL OUTPUT", request.model_output !== null ? request.model_output : "该请求没有 MODEL OUTPUT 记录（旧会话或在模型调用前失败）。"));
    }
    if (request.error) payloads.append(payloadBlock("ERROR", request.error));
    card.append(summary, payloads);
    card.addEventListener("toggle", () => {
      if (card.open) state.expandedSessionRequests.add(requestKey);
      else state.expandedSessionRequests.delete(requestKey);
    });
    detail.append(card);
  });
}

$("#session-api-tab").addEventListener("click", () => setSessionLogMode("api"));
$("#session-model-tab").addEventListener("click", () => setSessionLogMode("model"));

function payloadBlock(label, value) {
  const fragment = document.createDocumentFragment(), title = document.createElement("div"), pre = document.createElement("pre");
  title.className = "payload-label"; title.textContent = label;
  pre.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  fragment.append(title, pre); return fragment;
}
function safeDownloadPart(value) {
  const safe = String(value || "").replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "");
  return safe.slice(0, 120) || "session";
}
function exportSession() {
  if (!state.sessionDetail) { toast("暂无可导出的会话日志", true); return; }
  let objectUrl = "";
  try {
    const content = JSON.stringify(state.sessionDetail, null, 2) + "\n";
    const blob = new Blob([content], { type: "application/json;charset=utf-8" });
    objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = "rkclaw-session-" + safeDownloadPart(state.sessionDetail.id) + ".json";
    document.body.append(link);
    link.click();
    link.remove();
    toast("会话日志已导出");
  } catch (error) {
    toast("会话日志导出失败：" + (error?.message || "未知错误"), true);
  } finally {
    if (objectUrl) setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  }
}
$("#export-session").addEventListener("click", exportSession);
$("#close-dialog").addEventListener("click", () => $("#session-dialog").close());

$("#pause-logs").addEventListener("click", () => { state.logsPaused = !state.logsPaused; $("#pause-logs").textContent = state.logsPaused ? "继续" : "暂停"; });
$("#clear-log-view").addEventListener("click", () => $("#log-lines").replaceChildren());
$("#log-level").addEventListener("change", loadLogs);
$("#log-query").addEventListener("input", debounce(loadLogs, 300));
async function loadLogs() {
  if (state.logsPaused) return;
  const params = new URLSearchParams({ level: $("#log-level").value, query: $("#log-query").value, limit: "500" });
  try {
    const payload = await api("/api/webui/server-logs?" + params), box = $("#log-lines"); box.replaceChildren();
    if (!payload.enabled) { box.textContent = "Server 文件日志未配置。"; return; }
    payload.lines.forEach((line) => {
      const row = document.createElement("div"); row.className = "log-line " + line.level;
      [line.timestamp, line.level, line.logger, line.message].forEach((value, index) => {
        const cell = document.createElement("span"); cell.className = ["time", "level", "logger", "message"][index]; cell.textContent = value; row.append(cell);
      });
      box.append(row);
    });
    box.scrollTop = box.scrollHeight;
  } catch (error) { toast(error.message, true); }
}

const fieldGroups = [
  { title: "Server", description: "HTTP 服务、排队与流式传输参数。", fields: [
    { source: "host", label: "监听地址", section: "server", key: "host", type: "text" },
    { source: "port", label: "端口", section: "server", key: "port", type: "number" },
    { source: "queue_size", label: "队列容量", section: "server", key: "queue_size", type: "number" },
    { source: "sse_heartbeat_interval_s", label: "SSE 心跳秒数", section: "server", key: "sse_heartbeat_interval_s", type: "number" },
    { source: "enable_streaming", label: "允许流式响应", section: "server", key: "enable_streaming", type: "checkbox" },
  ]},
  { title: "Runtime", description: "RKNN Runtime、计算卡与本地运行库。", fields: [
    { source: "runtime_target", label: "Runtime Target", section: "runtime", key: "target", type: "text" },
    { source: "core_mask", label: "Core Mask", section: "runtime", key: "core_mask", type: "number" },
    { source: "toolkit_lite_wheel", label: "Toolkit Lite Wheel", section: "runtime", key: "toolkit_lite_wheel", type: "text", wide: true, pathMode: "file", extensions: [".whl"] },
    { source: "native_library", label: "Native Library", section: "runtime", key: "native_library", type: "text", wide: true, pathMode: "file", extensions: [".so"] },
  ]},
  { title: "Model", description: "模型公共文件、推理卡数量与单卡或多卡模型分段。", fields: [
    { source: "model_id", label: "模型 ID", section: "model", key: "id", type: "text" },
    { source: "max_context_tokens", label: "Context Tokens", section: "model", key: "max_context_tokens", type: "number" },
    { source: "max_new_tokens", label: "最大输出 Tokens", section: "model", key: "max_new_tokens", type: "number" },
    { source: "enable_thinking", label: "默认开启 Thinking", section: "model", key: "enable_thinking", type: "checkbox" },
    { source: "clear_kv_cache", label: "请求后清理 KV Cache", section: "model", key: "clear_kv_cache", type: "checkbox" },
    { source: "tokenizer_path", label: "Tokenizer 路径", section: "model", key: "tokenizer_path", type: "text", wide: true, pathMode: "file", extensions: [".gguf"] },
    { source: "embed_path", label: "Embedding 路径", section: "model", key: "embed_path", type: "text", wide: true, pathMode: "file", extensions: [".bin"] },
    { source: "per_layer_embed_path", label: "Per-layer Embedding 路径", section: "model", key: "per_layer_embed_path", type: "text", wide: true, pathMode: "file", extensions: [".bin"] },
    { source: "rope_cache_path", label: "RoPE Cache 路径", section: "model", key: "rope_cache_path", type: "text", wide: true, pathMode: "file" },
    { source: "chat_template_file", label: "Chat Template 文件", section: "model", key: "chat_template_file", type: "text", wide: true, pathMode: "file" },
    { source: "kv_cache_dir", label: "KV Cache 目录", section: "model", key: "kv_cache_dir", type: "text", wide: true, pathMode: "directory" },
    { source: "kv_cache_system_marker", label: "KV Cache System Marker", section: "model", key: "kv_cache_system_marker", type: "text", wide: true },
  ]},
  { title: "Checkpoint", description: "长上下文 KV checkpoint 保存策略。", fields: [
    { source: "checkpoint_enabled", label: "启用 Checkpoint", section: "checkpoint", key: "enabled", type: "checkbox" },
    { source: "checkpoint_start_pos", label: "Start Position", section: "checkpoint", key: "start_pos", type: "number" },
    { source: "checkpoint_interval", label: "Interval", section: "checkpoint", key: "interval", type: "number" },
    { source: "checkpoint_max_count", label: "最大数量", section: "checkpoint", key: "max_count", type: "number" },
  ]},
  { title: "Sampling & Reasoning", description: "生成采样与 reasoning 输出拆分。", fields: [
    { source: "temperature", label: "Temperature", section: "sampling", key: "temperature", type: "number" },
    { source: "top_p", label: "Top P", section: "sampling", key: "top_p", type: "number" },
    { source: "top_k", label: "Top K", section: "sampling", key: "top_k", type: "number" },
    { source: "repeat_penalty", label: "Repeat Penalty", section: "sampling", key: "repeat_penalty", type: "number" },
    { source: "separate_reasoning", label: "拆分 Reasoning 输出", section: "reasoning", key: "separate_output", type: "checkbox" },
    { source: "fallback_delimiter", label: "Fallback Delimiter", section: "reasoning", key: "fallback_delimiter", type: "text", wide: true },
    { source: "enable_tool_call_correction", label: "启用 Tool Call 纠错", section: "tool_call_correction", key: "enabled", type: "checkbox" },
  ]},
  { title: "XGrammar & Native Sampling", description: "结构化工具调用约束和 C++ 原生采样。", fields: [
    { source: "enable_xgrammar", label: "启用 XGrammar", section: "xgrammar", key: "enabled", type: "checkbox" },
    { source: "xgrammar_debug", label: "XGrammar Debug", section: "xgrammar", key: "debug", type: "checkbox" },
    { source: "xgrammar_model_structure", label: "模型结构", section: "xgrammar", key: "model_structure", type: "select", options: [["qwen3", "Qwen3"], ["qwen3.5", "Qwen3.5"]] },
    { source: "enable_native_sampling", label: "启用 Native Sampling", section: "native_sampling", key: "enabled", type: "checkbox" },
    { source: "native_sampling_seed", label: "Seed", section: "native_sampling", key: "seed", type: "number" },
    { source: "native_repeat_last_n", label: "Repeat Last N", section: "native_sampling", key: "repeat_last_n", type: "number" },
    { source: "native_penalize_newline", label: "惩罚换行 Token", section: "native_sampling", key: "penalize_newline", type: "checkbox" },
  ]},
  { title: "Logging", description: "请求明细、会话存储及 Server 日志轮转。", fields: [
    { source: "debug_logs", label: "Debug Logs", section: "logging", key: "debug_logs", type: "checkbox" },
    { source: "session_logs_enabled", label: "记录完整会话", section: "logging", key: "session_logs_enabled", type: "checkbox" },
    { source: "logger_detail_log_max_chars", label: "Logger 明细最大字符", section: "logging", key: "logger_detail_log_max_chars", type: "number" },
    { source: "session_retention_days", label: "会话保留天数", section: "logging", key: "session_retention_days", type: "number" },
    { source: "openai_request_log", label: "OpenAI Request Log", section: "logging", key: "openai_request_log", type: "select", options: [["off","Off"],["logger","Logger"],["file","File"],["both","Both"]] },
    { source: "llm_input_log", label: "LLM Input Log", section: "logging", key: "llm_input_log", type: "select", options: [["off","Off"],["logger","Logger"],["file","File"],["both","Both"]] },
    { source: "llm_output_log", label: "LLM Output Log", section: "logging", key: "llm_output_log", type: "select", options: [["off","Off"],["logger","Logger"],["file","File"],["both","Both"]] },
    { source: "openai_response_log", label: "OpenAI Response Log", section: "logging", key: "openai_response_log", type: "select", options: [["off","Off"],["logger","Logger"],["file","File"],["both","Both"]] },
    { source: "server_log_path", label: "Server 日志路径", section: "logging", key: "server_log_path", type: "text", wide: true, pathMode: "file" },
    { source: "server_log_max_bytes", label: "单文件最大字节", section: "logging", key: "server_log_max_bytes", type: "number" },
    { source: "server_log_backup_count", label: "轮转文件数量", section: "logging", key: "server_log_backup_count", type: "number" },
  ]},
  { title: "WebUI", description: "管理控制台存储、统计与登录会话参数；Token 在 TOML 编辑器中安全脱敏。", fields: [
    { source: "webui_enabled", label: "启用 WebUI", section: "webui", key: "enabled", type: "checkbox" },
    { source: "webui_data_path", label: "WebUI 数据库路径", section: "webui", key: "data_path", type: "text", wide: true, pathMode: "file", extensions: [".sqlite3", ".db"] },
    { source: "webui_stats_retention_days", label: "统计保留天数", section: "webui", key: "stats_retention_days", type: "number" },
    { source: "webui_reload_drain_timeout_s", label: "重载排空超时秒数", section: "webui", key: "reload_drain_timeout_s", type: "number" },
    { source: "webui_session_cookie_ttl_s", label: "登录会话有效秒数", section: "webui", key: "session_cookie_ttl_s", type: "number" },
  ]},
];

async function loadConfig() {
  try {
    state.config = await api("/api/webui/config");
    await refreshDevices(false);
    $("#toml-editor").value = state.config.toml;
    renderConfigForm(state.config.structured);
    $("#config-state").textContent = state.config.saved_is_active ? "磁盘配置与当前模型一致" : "磁盘配置尚未应用到当前模型";
  } catch (error) { toast(error.message, true); }
}

async function refreshDevices(showError = true) {
  try {
    const payload = await api("/api/webui/devices");
    state.devices = payload.devices || [];
    $$('[data-device-select]').forEach((select) => populateDeviceOptions(select, select.value));
  } catch (error) {
    state.devices = [];
    if (showError) toast("计算卡枚举失败：" + error.message, true);
  }
}

function populateDeviceOptions(select, currentValue = "") {
  select.replaceChildren();
  const automatic = document.createElement("option"); automatic.value = ""; automatic.textContent = "自动选择"; select.append(automatic);
  state.devices.forEach((device) => {
    const option = document.createElement("option"); option.value = device.id; option.textContent = device.label; select.append(option);
  });
  if (currentValue && !state.devices.some((device) => device.id === currentValue)) {
    const current = document.createElement("option"); current.value = currentValue; current.textContent = currentValue + "（当前配置）"; select.append(current);
  }
  select.value = currentValue;
}

function createDeviceSelect(value = "") {
  const select = document.createElement("select"); select.dataset.deviceSelect = "true"; populateDeviceOptions(select, value); return select;
}


function renderConfigForm(values) {
  const root = $("#config-form"); root.replaceChildren();
  const stages = (values.multicard_stages || []).map((stage) => Object.assign({}, stage));
  state.deployment = {
    single: { device_id: values.device_id || "", rknn_path: values.rknn_path || "", weight_path: values.weight_path || "" },
    stages,
    bucketSize: values.multicard_bucket_size ?? 128,
  };
  const initialCount = stages.length >= 2 ? stages.length : 1;
  fieldGroups.forEach((group) => {
    const section = document.createElement("section"); section.className = "form-section";
    const heading = document.createElement("h4"); heading.textContent = group.title;
    const description = document.createElement("p"); description.className = "section-description"; description.textContent = group.description;
    const grid = document.createElement("div"); grid.className = "form-grid";
    group.fields.forEach((definition) => grid.append(configField(definition, values[definition.source])));
    section.append(heading, description, grid);
    if (group.title === "Model") section.append(renderModelDeployment(initialCount));
    root.append(section);
  });
}

function configField(definition, value) {
  if (definition.type === "checkbox") {
    const wrapper = document.createElement("label"); wrapper.className = "checkbox-field";
    const input = document.createElement("input"); input.type = "checkbox"; input.checked = Boolean(value); setConfigDataset(input, definition);
    wrapper.append(input, document.createTextNode(definition.label)); return wrapper;
  }
  const wrapper = document.createElement("div"); wrapper.className = "field" + (definition.wide ? " wide-field" : "");
  const caption = document.createElement("label"); caption.textContent = definition.label;
  let input;
  if (definition.type === "device") input = createDeviceSelect(value || "");
  else if (definition.type === "select") {
    input = document.createElement("select");
    (definition.options || []).forEach(([optionValue, optionLabel]) => {
      const option = document.createElement("option"); option.value = optionValue; option.textContent = optionLabel; input.append(option);
    });
    input.value = value ?? "";
  } else {
    input = document.createElement("input"); input.type = definition.type; input.value = value ?? ""; if (definition.type === "number") input.step = "any";
  }
  setConfigDataset(input, definition);
  wrapper.append(caption, definition.pathMode ? pathControl(input, definition.pathMode, definition.extensions || []) : input);
  if (definition.type === "device") {
    const refresh = document.createElement("button"); refresh.className = "ghost"; refresh.type = "button"; refresh.textContent = "刷新"; refresh.addEventListener("click", () => refreshDevices(true));
    const select = wrapper.lastElementChild; wrapper.removeChild(select); const group = document.createElement("div"); group.className = "input-action-group"; group.append(select, refresh); wrapper.append(group);
  }
  if (definition.help) { const hint = document.createElement("small"); hint.className = "field-hint"; hint.textContent = definition.help; wrapper.append(hint); }
  return wrapper;
}

function setConfigDataset(input, definition) {
  input.dataset.source = definition.source; input.dataset.section = definition.section; input.dataset.key = definition.key;
}

function pathControl(input, mode, extensions = []) {
  const group = document.createElement("div"); group.className = "input-action-group";
  const browse = document.createElement("button"); browse.type = "button"; browse.className = "ghost"; browse.textContent = "选择";
  browse.addEventListener("click", () => openPathPicker(input, mode, extensions)); group.append(input, browse); return group;
}


function renderModelDeployment(initialCount) {
  const root = document.createElement("div"); root.className = "model-deployment";
  const header = document.createElement("div"); header.className = "deployment-header";
  const copy = document.createElement("div");
  const heading = document.createElement("h5"); heading.textContent = "模型卡配置";
  const description = document.createElement("p"); description.className = "section-description"; description.textContent = "选择推理使用的模型卡数量，界面会自动切换单卡路径或多卡 Stage 配置。";
  copy.append(heading, description);
  const countField = document.createElement("div"); countField.className = "field deployment-count";
  const label = document.createElement("label"); label.htmlFor = "model-card-count"; label.textContent = "模型卡数量";
  const select = document.createElement("select"); select.id = "model-card-count";
  const optionLimit = Math.max(8, initialCount);
  for (let count = 1; count <= optionLimit; count += 1) {
    const option = document.createElement("option"); option.value = String(count); option.textContent = cardCountLabel(count); select.append(option);
  }
  select.value = String(initialCount); countField.append(label, select); header.append(copy, countField);
  const body = document.createElement("div"); body.className = "deployment-body";
  select.addEventListener("change", () => {
    captureModelDeployment(root);
    renderModelDeploymentFields(body, Number(select.value));
  });
  root.append(header, body); renderModelDeploymentFields(body, initialCount); return root;
}

function cardCountLabel(count) {
  const labels = ["", "单卡", "双卡", "三卡", "四卡", "五卡", "六卡", "七卡", "八卡"];
  return labels[count] || count + " 卡";
}

function captureModelDeployment(root = $(".model-deployment")) {
  if (!root || !state.deployment) return;
  const singleEditor = root.querySelector(".single-card-editor");
  if (singleEditor) {
    singleEditor.querySelectorAll("[data-deployment-key]").forEach((input) => { state.deployment.single[input.dataset.deploymentKey] = input.value; });
    return;
  }
  const bucket = root.querySelector("[data-deployment-bucket]");
  if (bucket) state.deployment.bucketSize = bucket.value;
  root.querySelectorAll(".stage-editor").forEach((editor, index) => {
    const stage = {};
    editor.querySelectorAll("[data-stage-key]").forEach((input) => { stage[input.dataset.stageKey] = input.value; });
    state.deployment.stages[index] = stage;
  });
}

function renderModelDeploymentFields(body, count) {
  body.replaceChildren();
  if (count === 1) {
    const note = document.createElement("p"); note.className = "deployment-note"; note.textContent = "单卡模式使用 [runtime].device_id 与 [model] 下的 RKNN、Weight 路径。";
    body.append(note, singleCardEditor()); return;
  }
  const note = document.createElement("p"); note.className = "deployment-note"; note.textContent = cardCountLabel(count) + "流水线将生成 " + count + " 个 [[multicard.stages]]，执行顺序与 Stage 顺序一致。";
  const options = document.createElement("div"); options.className = "form-grid deployment-options";
  const bucket = configField({ source: "multicard_bucket_size", label: "Bucket Size", section: "multicard", key: "bucket_size", type: "number" }, state.deployment.bucketSize);
  const bucketInput = bucket.querySelector("[data-section]"); bucketInput.dataset.deploymentBucket = "true"; options.append(bucket);
  body.append(note, options);
  for (let index = 0; index < count; index += 1) body.append(stageEditor(state.deployment.stages[index] || {}, index));
}

function singleCardEditor() {
  const editor = document.createElement("div"); editor.className = "form-grid request-card single-card-editor";
  const definitions = [
    { source: "device_id", label: "Device ID", section: "runtime", key: "device_id", type: "device" },
    { source: "rknn_path", label: "RKNN 路径", section: "model", key: "rknn_path", type: "text", wide: true, pathMode: "file", extensions: [".rknn"] },
    { source: "weight_path", label: "Weight 路径", section: "model", key: "weight_path", type: "text", wide: true, pathMode: "file", extensions: [".weight"] },
  ];
  definitions.forEach((definition) => {
    const field = configField(definition, state.deployment.single[definition.key]);
    field.querySelector("[data-section]").dataset.deploymentKey = definition.key; editor.append(field);
  });
  return editor;
}

function stageEditor(stage, index) {
  const grid = document.createElement("div"); grid.className = "form-grid request-card stage-editor";
  const heading = document.createElement("h5"); heading.className = "stage-title"; heading.textContent = "Stage " + (index + 1) + " · 模型卡 " + (index + 1); grid.append(heading);
  const definitions = [
    ["device_id", "Device ID", "device"], ["rknn_path", "RKNN 路径", "path", [".rknn"]],
    ["weight_path", "Weight 路径", "path", [".weight"]], ["output_tensor_name", "Output Tensor Name", "text"],
  ];
  definitions.forEach(([key, labelText, type, extensions]) => {
    const field = document.createElement("div"); field.className = "field" + (type === "path" ? " wide-field" : "");
    const label = document.createElement("label"); label.textContent = labelText;
    const input = type === "device" ? createDeviceSelect(stage[key] || "") : document.createElement("input");
    if (type !== "device") input.value = stage[key] || "";
    input.dataset.stageKey = key;
    field.append(label, type === "path" ? pathControl(input, "file", extensions || []) : input); grid.append(field);
  });
  return grid;
}

function openPathPicker(input, mode = "file", extensions = []) {
  state.fileTarget = input; state.fileMode = mode; state.fileExtensions = extensions.map((item) => item.toLowerCase());
  $("#file-dialog-title").textContent = mode === "directory" ? "选择设备端目录" : "选择设备端文件";
  $("#choose-current-directory").classList.toggle("hidden", mode !== "directory");
  const value = input.value.trim();
  const initial = value.startsWith("/") ? (mode === "file" ? deviceDirname(value) : value) : "/userdata";
  $("#file-dialog").showModal(); browsePath(initial || "/");
}

function deviceDirname(path) { const trimmed = path.replace(/\/+$/, ""); const index = trimmed.lastIndexOf("/"); return index <= 0 ? "/" : trimmed.slice(0, index); }

async function browsePath(path) {
  const list = $("#file-browser-list"); list.replaceChildren(); list.textContent = "正在读取设备目录…";
  try {
    const payload = await api("/api/webui/files?" + new URLSearchParams({ path })); state.fileBrowser = payload;
    $("#file-browser-path").value = payload.path; $("#file-parent").disabled = payload.parent === payload.path; list.replaceChildren();
    const visible = payload.entries.filter((entry) => entry.is_dir || !state.fileExtensions.length || state.fileExtensions.some((extension) => entry.name.toLowerCase().endsWith(extension)));
    if (!visible.length) { const empty = document.createElement("div"); empty.className = "empty-file-list"; empty.textContent = "当前目录没有匹配项"; list.append(empty); }
    visible.forEach((entry) => {
      const row = document.createElement("button"); row.type = "button"; row.className = "file-entry";
      const icon = document.createElement("span"); icon.className = "file-icon"; icon.textContent = entry.is_dir ? "▸" : "•";
      const name = document.createElement("span"); name.className = "file-name"; name.textContent = entry.name;
      const meta = document.createElement("span"); meta.className = "file-meta"; meta.textContent = entry.is_dir ? "目录" : formatBytes(entry.size);
      row.append(icon, name, meta); row.addEventListener("click", () => entry.is_dir ? browsePath(entry.path) : choosePath(entry.path)); list.append(row);
    });
    $("#file-browser-note").textContent = payload.truncated ? "目录内容超过 500 项，仅显示前 500 项。" : (state.fileExtensions.length ? "文件筛选：" + state.fileExtensions.join("、") : "");
  } catch (error) { list.textContent = "无法读取目录"; $("#file-browser-note").textContent = error.message; }
}

function choosePath(path) { if (state.fileTarget) { state.fileTarget.value = path; state.fileTarget.dispatchEvent(new Event("change")); } $("#file-dialog").close(); }
function formatBytes(bytes) { if (!bytes) return "0 B"; const units = ["B","KB","MB","GB"]; const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024))); return (bytes / Math.pow(1024, index)).toFixed(index ? 1 : 0) + " " + units[index]; }

$("#file-parent").addEventListener("click", () => state.fileBrowser && browsePath(state.fileBrowser.parent));
$("#file-browser-go").addEventListener("click", () => browsePath($("#file-browser-path").value));
$("#file-browser-path").addEventListener("keydown", (event) => { if (event.key === "Enter") browsePath(event.currentTarget.value); });
$("#choose-current-directory").addEventListener("click", () => state.fileBrowser && choosePath(state.fileBrowser.path));
$("#close-file-dialog").addEventListener("click", () => $("#file-dialog").close());
$("#cancel-file-dialog").addEventListener("click", () => $("#file-dialog").close());
$("#form-tab").addEventListener("click", () => {
  $("#form-tab").classList.add("active"); $("#raw-tab").classList.remove("active"); $("#config-form").classList.remove("hidden"); $("#toml-editor").classList.add("hidden");
});
$("#raw-tab").addEventListener("click", () => {
  syncFormToToml(); $("#raw-tab").classList.add("active"); $("#form-tab").classList.remove("active"); $("#toml-editor").classList.remove("hidden"); $("#config-form").classList.add("hidden");
});
function currentToml() { if (!$("#config-form").classList.contains("hidden")) syncFormToToml(); return $("#toml-editor").value; }

function syncFormToToml() {
  captureModelDeployment();
  let text = $("#toml-editor").value;
  $$('[data-section][data-key]').forEach((input) => {
    const value = input.type === "checkbox" ? input.checked : (input.type === "number" && input.value !== "" ? Number(input.value) : input.value);
    text = setTomlValue(text, input.dataset.section, input.dataset.key, value);
  });
  const cardCount = Number($("#model-card-count")?.value || 1);
  const stages = $$(".stage-editor").map((editor) => {
    const stage = {}; editor.querySelectorAll("[data-stage-key]").forEach((input) => { stage[input.dataset.stageKey] = input.value; }); return stage;
  });
  if (cardCount === 1) text = replaceStages(text, []);
  else {
    text = removeTomlKey(text, "runtime", "device_id");
    text = removeTomlKey(text, "model", "rknn_path");
    text = removeTomlKey(text, "model", "weight_path");
    text = replaceStages(text, stages);
  }
  $("#toml-editor").value = text;
}

function setTomlValue(text, section, key, value) {
  const sectionPattern = new RegExp("(^|\\n)\\[" + escapeRegex(section) + "\\]\\s*(?:\\n|$)"), match = sectionPattern.exec(text);
  if (!match) return text + (text.endsWith("\n") ? "" : "\n") + "\n[" + section + "]\n" + key + " = " + tomlValue(value) + "\n";
  const start = match.index + match[0].length, next = text.slice(start).search(/\n\[\[?/), end = next < 0 ? text.length : start + next, block = text.slice(start, end);
  const keyPattern = new RegExp("(^|\\n)(\\s*" + escapeRegex(key) + "\\s*=\\s*)[^\\n]*");
  if (keyPattern.test(block)) return text.slice(0, start) + block.replace(keyPattern, "$1$2" + tomlValue(value)) + text.slice(end);
  return text.slice(0, end) + (block.endsWith("\n") ? "" : "\n") + key + " = " + tomlValue(value) + "\n" + text.slice(end);
}

function removeTomlKey(text, section, key) {
  const sectionPattern = new RegExp("(^|\\n)\\[" + escapeRegex(section) + "\\]\\s*(?:\\n|$)"), match = sectionPattern.exec(text);
  if (!match) return text;
  const start = match.index + match[0].length, next = text.slice(start).search(/\n\[\[?/), end = next < 0 ? text.length : start + next;
  const block = text.slice(start, end);
  const keyPattern = new RegExp("(^|\\n)\\s*" + escapeRegex(key) + "\\s*=\\s*[^\\n]*(?=\\n|$)");
  return text.slice(0, start) + block.replace(keyPattern, "$1") + text.slice(end);
}

function replaceStages(text, stages) {
  const pattern = /\n\[\[multicard\.stages\]\][\s\S]*?(?=\n\[(?!\[)|$)/;
  const block = stages.map((stage) => {
    const lines = ["[[multicard.stages]]"];
    ["device_id","rknn_path","weight_path","output_tensor_name"].forEach((key) => { if (stage[key]) lines.push(key + " = " + tomlValue(stage[key])); });
    return lines.join("\n");
  }).join("\n\n");
  if (pattern.test(text)) return text.replace(pattern, block ? "\n" + block : "");
  if (!block) return text;
  const parentPattern = new RegExp("(^|\\n)\\[multicard\\]\\s*(?:\\n|$)"), parent = parentPattern.exec(text);
  if (parent) {
    const start = parent.index + parent[0].length, next = text.slice(start).search(/\n\[\[?/), position = next < 0 ? text.length : start + next;
    return text.slice(0, position) + "\n\n" + block + text.slice(position);
  }
  const sampling = text.search(/\n\[sampling\]/), position = sampling < 0 ? text.length : sampling;
  return text.slice(0, position) + "\n\n[multicard]\n\n" + block + text.slice(position);
}
function tomlValue(value) { if (typeof value === "boolean" || typeof value === "number") return String(value); return JSON.stringify(String(value)); }

$("#validate-config").addEventListener("click", async () => {
  try {
    const result = await api("/api/webui/config/validate", { method: "POST", body: JSON.stringify({ toml: currentToml() }) });
    $("#config-state").textContent = result.warnings.length ? "校验通过；" + result.warnings.join("；") : "配置校验通过"; toast("配置校验通过");
  } catch (error) { $("#config-state").textContent = error.message; toast(error.message, true); }
});
$("#save-config").addEventListener("click", async () => {
  if (!state.config) return;
  try {
    const result = await api("/api/webui/config", { method: "PUT", body: JSON.stringify({ toml: currentToml(), revision: state.config.revision }) });
    state.config.revision = result.revision; const restart = result.restart_required_fields || [];
    $("#config-state").textContent = restart.length ? "已保存；以下配置需重启进程：" + restart.join(", ") : "已保存，尚未重新加载模型"; toast("配置已安全保存");
  } catch (error) { $("#config-state").textContent = error.message; toast(error.message, true); }
});
$("#reload-model").addEventListener("click", async () => {
  if (!confirm("将停止接收新推理并等待现有请求排空。确定重新加载模型吗？")) return;
  try { const operation = await api("/api/webui/reloads", { method: "POST" }); showReload(operation); pollReload(operation.id); }
  catch (error) { toast(error.message, true); }
});
async function pollReload(id) {
  try {
    const operation = await api("/api/webui/reloads/" + id); showReload(operation);
    if (operation.status === "running") state.timers.reload = setTimeout(() => pollReload(id), 1000);
    else { toast(operation.status === "succeeded" ? "模型重新加载完成" : "模型加载失败，已执行回滚", operation.status !== "succeeded"); loadDashboard(); loadConfig(); }
  } catch (error) { toast(error.message, true); }
}
function showReload(operation) {
  const node = $("#reload-progress"); node.classList.remove("hidden");
  node.textContent = "状态: " + operation.status + "\n阶段: " + operation.stage + (operation.error ? "\n错误: " + operation.error : "") + (operation.rollback ? "\n回滚: " + operation.rollback : "");
}

function number(value) { return new Intl.NumberFormat("zh-CN").format(value || 0); }
function formatMs(value) { return value ? (value >= 1000 ? (value / 1000).toFixed(1) + "s" : Math.round(value) + "ms") : "0ms"; }
function duration(seconds) {
  const days = Math.floor(seconds / 86400), hours = Math.floor(seconds % 86400 / 3600), minutes = Math.floor(seconds % 3600 / 60);
  return (days ? days + "d " : "") + String(hours).padStart(2, "0") + "h " + String(minutes).padStart(2, "0") + "m";
}
function dateTime(value) { return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false }); }
function escapeRegex(value) { return value.replace(/[.*+?^{}$()|[\]\\]/g, "\\$&"); }
function debounce(fn, delay) { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); }; }

state.timers.logs = setInterval(() => { if (state.view === "logs") loadLogs(); }, 3000);
state.timers.dashboard = setInterval(() => { if (state.view === "dashboard") loadDashboard(); }, 5000);
window.addEventListener("resize", debounce(() => { if (state.view === "dashboard") loadDashboard(); }, 200));
bootstrap();
