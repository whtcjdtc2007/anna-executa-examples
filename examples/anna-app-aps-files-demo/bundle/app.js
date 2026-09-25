// APS Files Demo — every APS scope, reachable two ways, switchable at
// runtime via the header toggles:
//
//   Tool invoke (default): app ── anna.tools.invoke ──▶ Executa ── files/* ──▶ host
//     The app declares ONLY `ui.host_api.tools: ["required:bundled:files-via-executa"]`
//     and has no files grant — object storage is reached through the Executa's
//     own `aps.files` capability. Objects land in `scope=user` (user-wide,
//     default) or `scope=tool` (plugin-private) — the Executa scope selector
//     picks which. Plugin storage_tokens never cover `scope=app`.
//
//   HOST API: app ── anna.files.upload_init ──▶ host ── presigned R2 PUT ──▶ R2
//     The app holds `ui.host_api.files` and drives the two-step upload itself
//     (init → browser PUT → finalize), plus download_url / list. Objects land
//     in the app's own `scope=app` space.
//
// Every scope is an isolated bucket, so a note saved under one scope is not
// listed by another — this is faithful to production.
//
// Loaded as a native ES module, so it imports the Anna App Runtime SDK below.
// The SDK (@anna-ai/app-runtime >= 0.5.0) is a named ESM export.

import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

// Bundled-executa handle → concrete tool_id resolution.
//
// The manifest references `bundled:files-via-executa` (a stable handle).
// At publish time the server mints a real tool_id and writes it to
// `bundle/anna-tool-ids.js` as `window.__ANNA_TOOL_IDS__["files-via-executa"]`.
// `anna-app dev` does the same with the local dev tool_id. We therefore read
// the resolved id from the sidecar and only fall back to the hard-coded dev id
// (which must match executas/files-via-executa-python/executa.json "tool_id")
// when the sidecar is absent.
const DEV_FALLBACK_TOOL_ID = "tool-test-files-via-executa-12345678";
const EXECUTA_TOOL_ID =
  (typeof window !== "undefined"
    && window.__ANNA_TOOL_IDS__
    && window.__ANNA_TOOL_IDS__["files-via-executa"])
  || DEV_FALLBACK_TOOL_ID;

const $ = (id) => document.getElementById(id);
const statusBox = $("status");
const rawBox = $("raw");

const annaReady = (async () => {
  const anna = await AnnaAppRuntime.connect();
  window.anna = anna;
  return anna;
})().catch((err) => {
  showStatus("runtime.connect", err, true);
  throw err;
});

function showStatus(label, payload, isError) {
  const msg = isError
    ? `${(payload && (payload.code || payload.error?.code)) || "error"}: ${
        (payload && (payload.message || payload.error?.message)) || String(payload)
      }`
    : payload;
  statusBox.textContent = `[${label}] ${msg}`;
  statusBox.classList.toggle("err", !!isError);
  statusBox.classList.toggle("ok", !isError);
}

// matrix host unwraps the plugin's {success, tool, data} envelope before
// forwarding to the iframe, so `reply` IS the bare tool payload. Fall back
// to reply.data for forward-compat in case a host stops unwrapping.
function unwrap(reply) {
  if (reply && typeof reply === "object" && reply.data && reply.tool) {
    return reply.data;
  }
  return reply ?? {};
}

async function invoke(method, args) {
  const anna = await annaReady;
  return anna.tools.invoke({ tool_id: EXECUTA_TOOL_ID, method, args });
}

// Which access pattern is selected in the header toggle.
function currentMode() {
  const checked = document.querySelector('input[name="mode"]:checked');
  return checked ? checked.value : "tool";
}

// Which APS scope the Executa should write to (tool-invoke mode only):
// "user" (user-wide, default) or "tool" (plugin-private).
function currentToolScope() {
  const checked = document.querySelector('input[name="tool-scope"]:checked');
  return checked ? checked.value : "user";
}

function activeScope() {
  return currentMode() === "host" ? "app" : currentToolScope();
}

// ---- Tool invoke mode (anna.tools.invoke → bundled Executa) ----------------

const toolMode = {
  async save(path, text) {
    const reply = await invoke("save_note", { path, text, scope: currentToolScope() });
    rawBox.textContent = JSON.stringify(reply, null, 2);
    return unwrap(reply); // { path, scope, size_bytes, etag }
  },
  async link(path) {
    const reply = await invoke("get_link", { path, scope: currentToolScope() });
    rawBox.textContent = JSON.stringify(reply, null, 2);
    const data = unwrap(reply);
    return data.url || data.get_url || null;
  },
  async list(prefix) {
    const reply = await invoke("list_notes", { prefix, scope: currentToolScope() });
    rawBox.textContent = JSON.stringify(reply, null, 2);
    return unwrap(reply).items || [];
  },
  async download(path) {
    // "Executa generated a file — how does the app download it?" — the
    // canonical answer depends on the scope the note was saved in:
    //
    //   scope=user — the app reaches it with the CROSS-SCOPE form of the
    //     host-mediated download: `scope: "user"`. Gating is two-layer:
    //     `ui.host_api.files: ["download"]` (dispatcher ACL) +
    //     the manifest `storage.scopes.user: "r"` declaration (scope gate —
    //     download is a read, so "r" suffices). APS rows are always
    //     filtered by user_id, so this only ever reads the CURRENT user's
    //     own space.
    //
    //   scope=tool — plugin-private: the app-side `files.download` is not
    //     exposed for tool scope, so the app asks the EXECUTA for a
    //     presigned link (`get_link`) and renders that instead. The Executa
    //     owns the bucket, so this is the intended delegation path. (No
    //     window.open — a sandboxed iframe can't reliably trigger saves,
    //     which is the whole reason host-mediated download exists.)
    if (currentToolScope() === "tool") {
      const url = await this.link(path);
      if (!url) throw new Error("executa returned no url for tool-scope note");
      return {
        ok: true,
        filename: path.split("/").pop() || "note.txt",
        executa_link: url,
      };
    }
    const anna = await annaReady;
    const res = await anna.files.download({
      path,
      scope: "user",
      filename: path.split("/").pop() || "note.txt",
    });
    rawBox.textContent = JSON.stringify(res, null, 2);
    return res; // { ok, filename, expires_at, etag, size_bytes, content_type }
  },
};

// ---- HOST API mode (anna.files.* directly, app's own grant) ----------------
//
// upload_init → browser PUT to presigned R2 URL → upload_finalize. The app
// itself holds the `ui.host_api.files` grant; objects land in `scope=app`.

const hostMode = {
  async save(path, text) {
    const anna = await annaReady;
    const bytes = new TextEncoder().encode(text);
    const contentType = "text/plain; charset=utf-8";
    const init = await anna.files.upload_init({
      path,
      content_type: contentType,
      size: bytes.length,
    });
    rawBox.textContent = JSON.stringify(init, null, 2);
    // Upload the bytes straight to the presigned R2 URL. The `headers` the
    // host returns are part of the signature — send them verbatim.
    const putRes = await fetch(init.put_url, {
      method: "PUT",
      headers: init.headers || {},
      body: bytes,
    });
    if (!putRes.ok) {
      const body = await putRes.text().catch(() => "");
      throw new Error(`R2 PUT ${putRes.status}: ${body.slice(0, 200)}`);
    }
    const etag = (putRes.headers.get("ETag") || "").replace(/"/g, "") || init.upload_id;
    const fin = await anna.files.upload_finalize({
      path,
      etag,
      size_bytes: bytes.length,
    });
    rawBox.textContent = JSON.stringify({ init, finalize: fin }, null, 2);
    return {
      path: fin.path ?? path,
      size_bytes: fin.size_bytes ?? bytes.length,
      etag: fin.etag ?? etag,
    };
  },
  async link(path) {
    const anna = await annaReady;
    const res = await anna.files.download_url({ path });
    rawBox.textContent = JSON.stringify(res, null, 2);
    // Host emits `get_url`; tolerate `url` for forward-compat.
    return res.get_url || res.url || null;
  },
  async list(prefix) {
    const anna = await annaReady;
    const res = await anna.files.list({ prefix });
    rawBox.textContent = JSON.stringify(res, null, 2);
    return res.items || [];
  },
  async download(path) {
    const anna = await annaReady;
    // Host-mediated save (dispatcher ≥ 0.17.0, forum #175): the host presigns
    // with a forced `Content-Disposition: attachment`, clicks a top-level
    // anchor in the HOST page (a sandboxed iframe can't reliably save files),
    // and strips `get_url` before this result arrives — note the response has
    // `ok: true` + metadata but NO URL. `filename` is optional and defaults
    // to the last path segment.
    const res = await anna.files.download({
      path,
      filename: path.split("/").pop() || "note.txt",
    });
    rawBox.textContent = JSON.stringify(res, null, 2);
    return res; // { ok, filename, expires_at, etag, size_bytes, content_type }
  },
};

function activeImpl() {
  return currentMode() === "host" ? hostMode : toolMode;
}

function modeLabel() {
  return currentMode() === "host"
    ? "host-api · scope=app"
    : `tool-invoke · scope=${currentToolScope()}`;
}

$("save-btn").addEventListener("click", async () => {
  const path = $("note-path").value.trim() || "notes/hello.txt";
  const text = $("note-text").value;
  $("save-btn").disabled = true;
  showStatus(`save · ${modeLabel()}`, "uploading…", false);
  try {
    const data = await activeImpl().save(path, text);
    $("r-path").textContent = data.path ?? "—";
    $("r-size").textContent = data.size_bytes != null ? `${data.size_bytes} B` : "—";
    $("r-etag").textContent = data.etag ?? "—";
    showStatus(`save · ${modeLabel()}`, "saved ✓", false);
  } catch (err) {
    rawBox.textContent = rawBox.textContent || "(failed)";
    showStatus(`save · ${modeLabel()}`, err, true);
  } finally {
    $("save-btn").disabled = false;
  }
});

$("link-btn").addEventListener("click", async () => {
  const path = $("note-path").value.trim() || "notes/hello.txt";
  $("link-btn").disabled = true;
  try {
    const url = await activeImpl().link(path);
    if (url) {
      $("link-out").innerHTML = "";
      const a = document.createElement("a");
      a.href = url;
      a.textContent = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      $("link-out").appendChild(a);
      showStatus(`get_link · ${modeLabel()}`, "link ready ✓", false);
    } else {
      $("link-out").textContent = "(no url returned)";
      showStatus(`get_link · ${modeLabel()}`, "no url", true);
    }
  } catch (err) {
    $("link-out").textContent = "(failed)";
    showStatus(`get_link · ${modeLabel()}`, err, true);
  } finally {
    $("link-btn").disabled = false;
  }
});

$("dl-btn").addEventListener("click", async () => {
  const path = $("note-path").value.trim() || "notes/hello.txt";
  $("dl-btn").disabled = true;
  showStatus(`download · ${modeLabel()}`, "requesting host-mediated save…", false);
  try {
    const res = await activeImpl().download(path);
    if (res.executa_link) {
      // tool-scope note: plugin-private — the Executa minted the link.
      $("link-out").innerHTML = "";
      const a = document.createElement("a");
      a.href = res.executa_link;
      a.textContent = res.executa_link;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      $("link-out").append(
        "scope=tool is plugin-private — app-side files.download is not\n"
          + "available; the Executa minted this presigned link instead:\n",
        a,
      );
      showStatus(`download · ${modeLabel()}`, "executa link ready ✓", false);
      return;
    }
    // The browser save dialog was opened by the HOST page — the result that
    // reaches this iframe deliberately contains no URL.
    $("link-out").textContent =
      `download triggered by host ✓\n`
      + `filename:     ${res.filename ?? "—"}\n`
      + `size_bytes:   ${res.size_bytes ?? "—"}\n`
      + `content_type: ${res.content_type ?? "—"}\n`
      + `get_url:      (stripped by host — never enters the iframe)`;
    showStatus(`download · ${modeLabel()}`, "browser save triggered ✓", false);
  } catch (err) {
    $("link-out").textContent = "(failed)";
    showStatus(`download · ${modeLabel()}`, err, true);
  } finally {
    $("dl-btn").disabled = false;
  }
});

$("list-btn").addEventListener("click", async () => {
  const prefix = ($("note-path").value.split("/")[0] || "") + "/";
  $("list-btn").disabled = true;
  try {
    const items = await activeImpl().list(prefix);
    $("link-out").textContent = items.length
      ? items.map((it) => `${it.path}  (${it.size_bytes ?? "?"} B)`).join("\n")
      : "(no objects under prefix)";
    showStatus(`list · ${modeLabel()}`, `${items.length} object(s)`, false);
  } catch (err) {
    $("link-out").textContent = "(failed)";
    showStatus(`list · ${modeLabel()}`, err, true);
  } finally {
    $("list-btn").disabled = false;
  }
});

// Surface the active scope whenever a toggle changes so the user understands
// why notes saved under one scope don't appear under another; the Executa
// scope selector only applies to tool-invoke mode, so grey it out otherwise.
function refreshScopeUI() {
  const isTool = currentMode() === "tool";
  const row = $("tool-scope-row");
  if (row) row.style.opacity = isTool ? "" : "0.4";
  for (const radio of document.querySelectorAll('input[name="tool-scope"]')) {
    radio.disabled = !isTool;
  }
  showStatus(`mode · ${modeLabel()}`, `active scope = ${activeScope()}`, false);
}

for (const radio of document.querySelectorAll(
  'input[name="mode"], input[name="tool-scope"]',
)) {
  radio.addEventListener("change", refreshScopeUI);
}
