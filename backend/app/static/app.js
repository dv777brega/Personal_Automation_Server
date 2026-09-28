"use strict";
const $ = (id) => document.getElementById(id);
const state = { token: "", offset: 0, limit: 15, busy: false, ready: false, task: null, taskId: null, scripts: "", noticeTimer: null };
const active = (status) => ["PENDING", "RUNNING"].includes(status);
const date = (value) => value ? new Date(value).toLocaleString() : "—";
function duration(task) {
  if (!task.started_at) return "—";
  const seconds = Math.max(0, Math.round(((task.finished_at ? new Date(task.finished_at) : new Date()) - new Date(task.started_at)) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function notice(message, error = false) {
  clearTimeout(state.noticeTimer);
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
  $("notice").hidden = false;
  if (!error) state.noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 7000);
}
function connected(ok, label) {
  $("connection").textContent = label;
  $("connection-dot").classList.toggle("offline", !ok);
}
function lock() {
  state.token = "";
  state.ready = false;
  state.task = null;
  state.taskId = null;
  $("task-dialog").close();
  $("auth-panel").hidden = false;
  $("workspace").hidden = true;
  $("lock").hidden = true;
  connected(false, "Workspace locked");
}
async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`/api${path}`, { ...options, headers, signal: AbortSignal.timeout(15000) });
  if (response.status === 401) {
    lock();
    throw new Error("Enter a valid API token to unlock the workspace.");
  }
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : (body.detail || []).map((item) => item.msg).join("; ") || message;
    } catch (_) { /* Preserve the HTTP status if the server returned non-JSON. */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}
function action(text, className, callback) {
  const button = element("button", text, className);
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try { await callback(); } catch (error) { notice(error.message, true); }
    finally { button.disabled = false; }
  });
  return button;
}
function renderTasks(data) {
  const rows = data.items.map((task) => {
    const row = element("tr");
    const name = element("td", task.script_name);
    name.append(element("small", `#${task.task_id}${task.schedule_id ? ` · Schedule #${task.schedule_id}` : " · Manual run"}`));
    const status = element("td");
    status.append(element("span", task.status.replaceAll("_", " "), `badge ${task.status}`));
    const actions = element("td");
    actions.append(action("Details ↗", "secondary", () => openTask(task.task_id)));
    row.append(name, status, element("td", date(task.created_at)), element("td", duration(task)), actions);
    return row;
  });
  $("tasks").replaceChildren(...rows);
  $("tasks-empty").hidden = data.total > 0;
  $("page-caption").textContent = data.total ? `${state.offset + 1}–${Math.min(state.offset + state.limit, data.total)} of ${data.total} runs` : "No matching tasks";
  $("previous").disabled = state.offset === 0;
  $("next").disabled = state.offset + state.limit >= data.total;
}
function renderSchedules(items) {
  $("schedules-empty").hidden = items.length > 0;
  $("schedule-list").replaceChildren(...items.map((schedule) => {
    const card = element("article", undefined, "schedule-card");
    const details = element("div");
    details.append(element("h3", schedule.name), element("p", `${schedule.script_name} · Every ${schedule.interval_seconds}s · ${schedule.enabled ? "Enabled" : "Paused"}`), element("p", schedule.enabled ? `Next due: ${date(schedule.next_run_at)}` : "Resume to start a fresh interval"));
    if (schedule.last_error) details.append(element("p", schedule.last_error, "schedule-error"));
    const actions = element("div", undefined, "schedule-actions");
    actions.append(action(schedule.enabled ? "Pause" : "Resume", "secondary", async () => {
      await api(`/schedules/${schedule.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !schedule.enabled }) });
      await refresh();
    }), action("Delete", "secondary", async () => {
      if (!confirm(`Delete schedule “${schedule.name}”? Task history is kept.`)) return;
      await api(`/schedules/${schedule.id}`, { method: "DELETE" });
      notice("Schedule deleted.");
      await refresh();
    }));
    card.append(details, actions);
    return card;
  }));
}
function renderDetail(task) {
  state.task = task;
  $("task-number").textContent = `TASK #${task.task_id}`;
  $("task-title").textContent = task.script_name;
  $("task-meta").replaceChildren(element("span", task.status.replaceAll("_", " "), `badge ${task.status}`), element("span", `Exit code: ${task.exit_code ?? "—"}`), element("span", `Duration: ${duration(task)}`), element("div", `Started: ${date(task.started_at)} · Finished: ${date(task.finished_at)}`), element("div", `Arguments: ${JSON.stringify(task.arguments)}`));
  $("task-stdout").textContent = task.stdout || (active(task.status) ? "Waiting for the task to finish…" : "No standard output.");
  $("task-stderr").textContent = task.stderr || "No standard error.";
  $("task-hint").textContent = active(task.status) ? "This view updates automatically. Output is saved when the task finishes." : "Run again starts a new task using the same arguments and timeout.";
  $("cancel-task").hidden = !active(task.status);
  $("rerun-task").hidden = active(task.status);
}
async function openTask(id) {
  state.taskId = id;
  const task = await api(`/tasks/${id}`);
  renderDetail(task);
  if (!$("task-dialog").open) $("task-dialog").showModal();
}
async function refresh() {
  if (!state.ready || state.busy) return;
  state.busy = true;
  try {
    const filter = $("status-filter").value;
    const [scripts, tasks, stats, schedules] = await Promise.all([
      api("/scripts"), api(`/tasks?limit=${state.limit}&offset=${state.offset}${filter ? `&status=${filter}` : ""}`), api("/stats"), api("/schedules"),
    ]);
    if (!state.ready) return;
    const signature = JSON.stringify(scripts.items);
    if (signature !== state.scripts) {
      const selected = $("script").value;
      $("script").replaceChildren(...scripts.items.map((script) => {
        const option = element("option", script.name);
        option.value = script.name;
        return option;
      }));
      if (!scripts.items.length) $("script").append(new Option("Add a .py file to scripts/ to get started", ""));
      else if (scripts.items.some((script) => script.name === selected)) $("script").value = selected;
      else if (scripts.items.some((script) => script.name === "disk_space.py")) $("script").value = "disk_space.py";
      $("script-count").textContent = `${scripts.items.length} scripts`;
      state.scripts = signature;
    }
    $("run-button").disabled = !scripts.items.length;
    renderTasks(tasks);
    renderSchedules(schedules.items);
    $("stat-total").textContent = stats.total;
    $("stat-active").textContent = (stats.by_status.RUNNING || 0) + (stats.by_status.PENDING || 0);
    $("stat-completed").textContent = stats.by_status.COMPLETED || 0;
    $("stat-failed").textContent = ["FAILED", "TIMED_OUT", "INTERRUPTED"].reduce((sum, key) => sum + (stats.by_status[key] || 0), 0);
    if ($("task-dialog").open && state.taskId) {
      const id = state.taskId;
      const detail = await api(`/tasks/${id}`);
      if (state.taskId === id && $("task-dialog").open) renderDetail(detail);
    }
    connected(true, "Server online");
  } catch (error) {
    connected(false, state.ready ? "Connection interrupted" : "Workspace locked");
    notice(error.message, true);
  } finally { state.busy = false; }
}
async function initialize() {
  try {
    const health = await api("/health");
    if (health.authentication_required && !state.token) { lock(); return; }
    const config = await api("/config");
    $("timeout").value = config.timeout_seconds;
    $("worker-caption").textContent = `${config.max_workers} concurrent workers · running + queued`;
    state.ready = true;
    $("auth-panel").hidden = true;
    $("workspace").hidden = false;
    $("lock").hidden = !health.authentication_required;
    $("notice").hidden = true;
    await refresh();
  } catch (error) { connected(false, "Unable to connect"); notice(error.message, true); }
}
$("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.token = $("token").value;
  $("token").value = "";
  await initialize();
});
$("lock").addEventListener("click", lock);
function updateMode() {
  const scheduled = $("mode").value === "schedule";
  $("schedule-fields").hidden = !scheduled;
  $("schedule-name").required = scheduled;
  $("interval").required = scheduled;
  $("interval").disabled = !scheduled;
  $("run-button").textContent = scheduled ? "＋ Create schedule" : "▷ Run script";
}
$("mode").addEventListener("change", updateMode);
$("new-schedule").addEventListener("click", () => { $("mode").value = "schedule"; updateMode(); $("script").focus(); });
$("run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("run-button");
  button.disabled = true;
  try {
    let args;
    try { args = JSON.parse($("arguments").value || "[]"); } catch (_) { throw new Error('Arguments must be a JSON array, for example ["--min-free-gb", "10"].'); }
    if (!Array.isArray(args) || args.some((arg) => typeof arg !== "string")) throw new Error("Arguments must be a JSON array of strings.");
    const body = { arguments: args, timeout_seconds: Number($("timeout").value) };
    if ($("mode").value === "schedule") {
      Object.assign(body, { name: $("schedule-name").value.trim(), script_name: $("script").value, interval_seconds: Number($("interval").value) });
      await api("/schedules", { method: "POST", body: JSON.stringify(body) });
      notice("Schedule created. Its first run starts after the selected interval.");
    } else {
      const task = await api(`/scripts/${encodeURIComponent($("script").value)}/run`, { method: "POST", body: JSON.stringify(body) });
      state.offset = 0;
      $("status-filter").value = "";
      notice(`Task #${task.task_id} queued.`);
      await openTask(task.task_id);
    }
    await refresh();
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = !$("script").value; }
});
$("status-filter").addEventListener("change", () => { state.offset = 0; refresh(); });
$("previous").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); refresh(); });
$("next").addEventListener("click", () => { state.offset += state.limit; refresh(); });
$("close-dialog").addEventListener("click", () => $("task-dialog").close());
$("task-dialog").addEventListener("close", () => { state.taskId = null; state.task = null; });
$("cancel-task").addEventListener("click", async () => {
  try { await api(`/tasks/${state.taskId}/cancel`, { method: "POST" }); notice("Cancellation requested."); await refresh(); }
  catch (error) { notice(error.message, true); }
});
$("rerun-task").addEventListener("click", async () => {
  const task = state.task;
  if (!task) return;
  $("rerun-task").disabled = true;
  try {
    const next = await api(`/scripts/${encodeURIComponent(task.script_name)}/run`, { method: "POST", body: JSON.stringify({ arguments: task.arguments, timeout_seconds: task.timeout_seconds }) });
    state.offset = 0;
    await openTask(next.task_id);
    await refresh();
  } catch (error) { notice(error.message, true); }
  finally { $("rerun-task").disabled = false; }
});
$("copy-output").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(state.task?.stdout || ""); $("copy-output").textContent = "Copied"; setTimeout(() => { $("copy-output").textContent = "Copy"; }, 1500); }
  catch (_) { notice("Clipboard unavailable. Select and copy the output directly.", true); }
});
updateMode();
initialize();
setInterval(() => { if (!document.hidden) { if (state.ready) refresh(); else if ($("auth-panel").hidden) initialize(); } }, 2000);
