// Background Demo — the page side of a backend-owned task.
//
//   page ── tools.invoke(check_start)  ──▶ Executa     returns {task_id} at once
//   page ── tools.invoke(check_list)   ──▶ Executa     every task's complete current state
//   page ── tools.invoke(check_status) ──▶ Executa     one task's complete current state
//
// The page holds no task state of its own. It asks the Executa on load —
// that is what makes "close the window, come back later" work: whatever the
// Executa did in the meantime is in its answer.

import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

// `anna-app dev` / `apps publish` write the resolved tool_id to
// anna-tool-ids.js; the fallback must match executa.json "tool_id".
const EXECUTA_TOOL_ID =
  (window.__ANNA_TOOL_IDS__ && window.__ANNA_TOOL_IDS__["background-check"]) ||
  "tool-test-background-check-12345678";

const POLL_MS = 2000;
const UNFINISHED = ["pending", "running"];

const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const annaReady = AnnaAppRuntime.connect();

// The host unwraps the plugin's {success, tool, data} envelope; tolerate
// one that does not.
async function invoke(method, args = {}) {
  const anna = await annaReady;
  const reply = await anna.tools.invoke({ tool_id: EXECUTA_TOOL_ID, method, args });
  return reply && reply.tool && reply.data ? reply.data : reply ?? {};
}

function showError(label, err) {
  const box = $("status");
  box.hidden = !err;
  box.classList.add("err");
  if (err) box.textContent = `[${label}] ${err.code ?? "error"}: ${err.message ?? String(err)}`;
}

// ── State: exactly what the Executa last told us ─────────────────────

const tasks = new Map(); // task_id → task, newest first

function unfinished() {
  return [...tasks.values()].filter((t) => UNFINISHED.includes(t.status));
}

// ── Rendering ────────────────────────────────────────────────────────

function renderHeartbeat(hb) {
  const node = $("heartbeat");
  if (!hb) return;
  if (hb.last_error) {
    node.textContent =
      `Heartbeat: background unavailable — ${hb.last_error.code ?? "error"}: ${hb.last_error.message}`;
    node.classList.add("err");
    return;
  }
  node.classList.remove("err");
  node.textContent = hb.last_ok_at
    ? `Heartbeat: last written ${new Date(hb.last_ok_at).toLocaleTimeString()} by the Executa on its own ` +
      `(#${hb.count} since the process started, every ${hb.interval_s}s)`
    : "Heartbeat: not written yet";
}

function renderChecks(checks) {
  const box = $("checks");
  if (box.childElementCount) return; // keep the user's selection
  for (const check of checks) {
    const label = el("label");
    const input = el("input");
    input.type = "checkbox";
    input.value = check.id;
    input.checked = check.default;
    label.title = check.method;
    label.append(input, check.label);
    box.append(label);
  }
  $("start-btn").disabled = false;
}

function renderTask(task) {
  const card = el("div", "task");

  const head = el("div", "row task-head");
  const left = el("div", "row");
  left.append(el("span", `badge ${task.status}`, task.status), el("span", "id", task.task_id));
  head.append(left);
  if (UNFINISHED.includes(task.status)) {
    const cancel = el("button", "", "Cancel");
    cancel.addEventListener("click", () => cancelTask(task.task_id, cancel));
    head.append(cancel);
  }
  card.append(head);

  const done = task.items.filter((i) => !UNFINISHED.includes(i.status)).length;
  const when = task.created_at ? new Date(task.created_at).toLocaleString() : "";
  card.append(el("div", "muted", `${when} · ${done}/${task.items.length} checks finished`));
  if (task.persist_error) {
    card.append(
      el(
        "div",
        "muted err",
        `State is not being saved: ${task.persist_error.code ?? "error"}: ${task.persist_error.message}`,
      ),
    );
  }

  const table = el("table");
  const header = el("tr");
  for (const title of ["Check", "Status", "Time", "Result"]) header.append(el("th", "", title));
  table.append(header);
  for (const item of task.items) {
    const row = el("tr");
    const name = el("td", "", item.label);
    name.title = item.method;
    const status = el("td");
    status.append(el("span", `badge ${item.status}`, item.status));
    const time = el("td", "num", item.elapsed_ms == null ? "" : `${item.elapsed_ms} ms`);
    const result = item.error
      ? el("td", "err", `${item.error.code ?? "error"}: ${item.error.message}`)
      : el("td", "", item.detail ?? "");
    row.append(name, status, time, result);
    table.append(row);
  }
  card.append(table);
  return card;
}

function renderTasks() {
  const box = $("tasks");
  box.replaceChildren(
    ...(tasks.size
      ? [...tasks.values()].map(renderTask)
      : [el("p", "muted", "No tasks yet.")]),
  );
}

// ── Actions ──────────────────────────────────────────────────────────

async function loadAll() {
  const data = await invoke("check_list");
  renderHeartbeat(data.heartbeat);
  renderChecks(data.checks ?? []);
  tasks.clear();
  for (const task of data.tasks ?? []) tasks.set(task.task_id, task);
  renderTasks();
  showError("storage", data.storage_error);
}

async function startTask() {
  const items = [...$("checks").querySelectorAll("input:checked")].map((i) => i.value);
  if (!items.length) return showError("start", { message: "select at least one check" });
  $("start-btn").disabled = true;
  try {
    const data = await invoke("check_start", { items, delay_s: Number($("delay").value) || 0 });
    showError("start", null);
    await loadAll();
    if (!tasks.has(data.task_id)) tasks.set(data.task_id, data.task);
    renderTasks();
  } catch (err) {
    showError("check_start", err);
  } finally {
    $("start-btn").disabled = false;
  }
}

async function cancelTask(taskId, button) {
  button.disabled = true;
  try {
    const data = await invoke("check_cancel", { task_id: taskId });
    if (data.task) tasks.set(taskId, data.task);
    renderTasks();
  } catch (err) {
    showError("check_cancel", err);
  }
}

async function poll() {
  try {
    for (const task of unfinished()) {
      const data = await invoke("check_status", { task_id: task.task_id });
      if (data.found) tasks.set(task.task_id, data.task);
    }
    renderTasks();
  } catch (err) {
    showError("check_status", err);
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

$("start-btn").addEventListener("click", startTask);

// On every open: ask the Executa what it has been doing.
loadAll()
  .catch((err) => {
    showError("check_list", err);
    $("tasks").replaceChildren(el("p", "muted", "Could not reach the Executa."));
  })
  .finally(poll);
