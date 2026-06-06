// APS Files Demo — two ways to reach Anna object storage, switchable at
// runtime via the header mode toggle:
//
//   Tool invoke (default): app ── anna.tools.invoke ──▶ Executa ── files/* ──▶ host
//     The app declares ONLY `ui.host_api.tools: ["required:bundled:files-via-executa"]`
//     and has no files grant — object storage is reached through the Executa's
//     own `aps.files` capability. Objects land in `scope=user`.
//
//   HOST API: app ── anna.files.upload_init ──▶ host ── presigned R2 PUT ──▶ R2
//     The app holds `ui.host_api.files` and drives the two-step upload itself
//     (init → browser PUT → finalize), plus download_url / list. Objects land
//     in the app's own `scope=app` space.
//
// Because the two modes target different scopes, a note saved in one mode is
// not listed by the other — this is faithful to production.
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

// ---- Tool invoke mode (anna.tools.invoke → bundled Executa) ----------------

const toolMode = {
  async save(path, text) {
    const reply = await invoke("save_note", { path, text });
    rawBox.textContent = JSON.stringify(reply, null, 2);
    return unwrap(reply); // { path, size_bytes, etag }
  },
  async link(path) {
    const reply = await invoke("get_link", { path });
    rawBox.textContent = JSON.stringify(reply, null, 2);
    const data = unwrap(reply);
    return data.url || data.get_url || null;
  },
  async list(prefix) {
    const reply = await invoke("list_notes", { prefix });
    rawBox.textContent = JSON.stringify(reply, null, 2);
    return unwrap(reply).items || [];
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
};

function activeImpl() {
  return currentMode() === "host" ? hostMode : toolMode;
}

function modeLabel() {
  return currentMode() === "host" ? "host-api" : "tool-invoke";
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

// Surface the active scope whenever the mode changes so the user understands
// why notes saved in one mode don't appear in the other.
for (const radio of document.querySelectorAll('input[name="mode"]')) {
  radio.addEventListener("change", () => {
    const scope = currentMode() === "host" ? "app" : "user";
    showStatus(`mode · ${modeLabel()}`, `active scope = ${scope}`, false);
  });
}
