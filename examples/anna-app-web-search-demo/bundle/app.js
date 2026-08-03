// Web Search Demo — exercises the host-managed web capability over BOTH
// channels so you can verify they share one wire contract:
//
//   HOST API (this iframe, no executa involved):
//     iframe ── anna.web.search({query,...}) ──▶ host web facade ─▶ provider chain
//     iframe ── anna.web.fetch({urls:[...]}) ──▶ host SSRF-guarded fetcher
//
//   Reverse RPC (bundled Executa):
//     iframe ── tools.invoke(web_search) ──▶ Executa ── web/search ─▶ host ─▶ provider chain
//     iframe ── tools.invoke(web_research) ─▶ Executa ── web/search + web/fetch ─▶ host
//
// Provider keys / routing / SSRF guarding / quota / billing all stay host-side.
// The response is provider-agnostic: {results, provider_tier, quota_consumed}.
// `provider_tier` reports the ACTUALLY-executed tier — a degraded `advanced`
// request comes back "basic" and is billed the cheaper rate.
//
// Loaded as a native ES module, so it imports the Anna App Runtime SDK below.
// The `anna.web` namespace requires @anna-ai/app-runtime >= 0.13.0.

import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

// Bundled-executa handle → concrete tool_id resolution.
//
// The manifest references `bundled:web-search-via-executa` (a stable handle).
// At publish time the server mints a real tool_id and writes it to
// `bundle/anna-tool-ids.js`. `anna-app dev` does the same with the local dev
// tool_id. We read the resolved id from the sidecar and only fall back to the
// hard-coded dev id (which must match executas/.../executa.json "tool_id")
// when the sidecar is absent.
const DEV_FALLBACK_TOOL_ID = "tool-test-web-search-12345678";
const EXECUTA_TOOL_ID =
  (typeof window !== "undefined"
    && window.__ANNA_TOOL_IDS__
    && window.__ANNA_TOOL_IDS__["web-search-via-executa"])
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

function showRaw(label, payload) {
  rawBox.textContent = `[${label}]\n${JSON.stringify(payload, null, 2)}`;
}

// matrix host unwraps the plugin's {success, tool, data} envelope before
// forwarding to the iframe, so `reply` IS the bare tool payload. Fall back to
// reply.data for forward-compat in case a host stops unwrapping.
function unwrap(reply) {
  if (reply && typeof reply === "object" && reply.data && reply.tool) {
    return reply.data;
  }
  return reply ?? {};
}

async function invokeExecuta(method, args) {
  const anna = await annaReady;
  // web_research chains search + up-to-3 fetches host-side; give it headroom.
  return anna.tools.invoke({
    tool_id: EXECUTA_TOOL_ID,
    method,
    args,
    timeoutMs: 120000,
  });
}

function searchParams() {
  const query = $("query-input").value.trim();
  if (!query) {
    showStatus("input", "query must be non-empty", true);
    return null;
  }
  const params = { query, max_results: 5, topic: $("topic-select").value };
  const range = $("range-select").value;
  if (range) params.time_range = range;
  return params;
}

// ─── Result rendering ────────────────────────────────────────────────────────

function renderResults(listEl, metaEl, payload) {
  const results = payload.results || [];
  metaEl.textContent =
    `tier=${payload.provider_tier ?? "?"} · ` +
    `quota=${payload.quota_consumed ?? 0} CU · ${results.length} results`;
  listEl.innerHTML = "";
  for (const r of results) {
    const li = document.createElement("li");
    li.className = "file-item";

    const text = document.createElement("div");
    text.className = "result-text";
    const a = document.createElement("a");
    a.href = r.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = r.title || r.url;
    const meta = document.createElement("div");
    meta.className = "muted";
    // score / published_at are OPTIONAL — never assume they exist.
    meta.textContent = [
      r.site,
      r.published_at || null,
      r.score != null ? `score ${r.score}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    const snippet = document.createElement("div");
    snippet.className = "muted snippet";
    snippet.textContent = r.snippet || "";
    text.append(a, meta, snippet);

    const fetchBtn = document.createElement("button");
    fetchBtn.className = "tiny";
    fetchBtn.textContent = "fetch";
    fetchBtn.title = "Extract this page via anna.web.fetch (HOST API)";
    fetchBtn.addEventListener("click", () => {
      $("url-input").value = r.url;
      hostFetch(r.url);
    });

    li.append(text, fetchBtn);
    listEl.appendChild(li);
  }
  if (!results.length) {
    listEl.innerHTML = '<li class="muted">(no results)</li>';
  }
}

// ─── Channel 1: HOST API (direct postMessage RPC; no executa) ────────────────

async function hostSearch() {
  const params = searchParams();
  if (!params) return;
  const btn = $("host-search-btn");
  btn.disabled = true;
  showStatus("anna.web.search", "searching…");
  try {
    const anna = await annaReady;
    const res = await anna.web.search(params);
    renderResults($("host-results"), $("host-meta"), res);
    showRaw("anna.web.search", res);
    showStatus(
      "anna.web.search",
      `ok — ${res.results.length} results, tier=${res.provider_tier}, ` +
        `quota_consumed=${res.quota_consumed} CU`
    );
  } catch (err) {
    // First call without a grant → APP_NOT_GRANTED (the host consent card
    // flow); CU quota exhausted → APP_QUOTA_EXCEEDED; bad params are
    // REJECTED (APP_INVALID_REQUEST), never silently truncated.
    showStatus("anna.web.search", err, true);
    showRaw("anna.web.search error", err);
  } finally {
    btn.disabled = false;
  }
}

async function hostFetch(rawUrl) {
  const url = (rawUrl || $("url-input").value).trim();
  if (!url) {
    showStatus("anna.web.fetch", "paste a URL first", true);
    return;
  }
  const btn = $("fetch-btn");
  btn.disabled = true;
  showStatus("anna.web.fetch", "fetching…");
  $("fetch-out").textContent = "(fetching…)";
  try {
    const anna = await annaReady;
    const res = await anna.web.fetch(
      { urls: [url], format: "markdown", max_chars: 6000 },
      { timeoutMs: 90000 }
    );
    showRaw("anna.web.fetch", res);
    // Per-item failure isolation: the CALL succeeded even if the page was
    // blocked — inspect pages[i].ok / pages[i].error per item.
    const page = (res.pages || [])[0] || {};
    if (page.ok) {
      $("fetch-out").textContent =
        `# ${page.title || "(untitled)"}\n` +
        `(final_url: ${page.final_url || url}` +
        `${page.truncated ? ", truncated" : ""})\n\n` +
        (page.content || "");
      showStatus(
        "anna.web.fetch",
        `ok — quota_consumed=${res.quota_consumed} CU`
      );
    } else {
      $("fetch-out").textContent = `page error: ${page.error}`;
      showStatus(
        "anna.web.fetch",
        `call ok, page failed: ${page.error} (per-item isolation — ` +
          `SSRF_BLOCKED / HTTP_4xx never fail the batch)`
      );
    }
  } catch (err) {
    showStatus("anna.web.fetch", err, true);
    showRaw("anna.web.fetch error", err);
  } finally {
    btn.disabled = false;
  }
}

// ─── Channel 2: Reverse RPC (bundled Executa → web/search) ───────────────────

async function rpcSearch() {
  const params = searchParams();
  if (!params) return;
  const btn = $("rpc-search-btn");
  btn.disabled = true;
  showStatus("tools.invoke(web_search)", "searching via Executa…");
  try {
    const reply = await invokeExecuta("web_search", params);
    const data = unwrap(reply);
    renderResults($("rpc-results"), $("rpc-meta"), data);
    showRaw("tools.invoke(web_search)", reply);
    showStatus(
      "tools.invoke(web_search)",
      `ok — ${(data.results || []).length} results, tier=${data.provider_tier}, ` +
        `quota_consumed=${data.quota_consumed} CU (same contract & pricing ` +
        `as the HOST API channel)`
    );
  } catch (err) {
    // The Executa passes WEB_* codes through verbatim: -32521 not granted,
    // -32522 CU quota exhausted, -32523 provider chain down.
    showStatus("tools.invoke(web_search)", err, true);
    showRaw("tools.invoke(web_search) error", err);
  } finally {
    btn.disabled = false;
  }
}

async function rpcResearch() {
  const params = searchParams();
  if (!params) return;
  const btn = $("research-btn");
  btn.disabled = true;
  showStatus("tools.invoke(web_research)", "researching (search + fetch)…");
  $("research-out").textContent = "(researching…)";
  try {
    const reply = await invokeExecuta("web_research", {
      query: params.query,
      pages: 2,
    });
    const data = unwrap(reply);
    showRaw("tools.invoke(web_research)", reply);
    const lines = [
      `query: ${data.query}`,
      `quota_consumed: ${data.quota_consumed} CU (search + per-URL fetch)`,
      "",
    ];
    for (const page of data.pages || []) {
      lines.push(`── ${page.url}`);
      if (page.ok) {
        lines.push(`   ${page.title || "(untitled)"}${page.truncated ? " (truncated)" : ""}`);
        lines.push((page.content || "").slice(0, 800));
      } else {
        lines.push(`   ✗ ${page.error} (per-item isolation)`);
      }
      lines.push("");
    }
    $("research-out").textContent = lines.join("\n") || "(no pages)";
    showStatus(
      "tools.invoke(web_research)",
      `ok — ${(data.pages || []).length} pages fetched host-side`
    );
  } catch (err) {
    showStatus("tools.invoke(web_research)", err, true);
    showRaw("tools.invoke(web_research) error", err);
  } finally {
    btn.disabled = false;
  }
}

// ─── Phase 2: image search + image download (both channels) ─────────────────

function renderImageResults(payload, channelLabel) {
  const grid = $("image-results");
  const results = payload.results || [];
  // `_meta.provider` is a diagnostic field (which provider actually served
  // the call — e.g. serper vs the ddgs/tavily fallbacks). Optional; render
  // it when present but never depend on its value.
  const provider = payload._meta && payload._meta.provider;
  $("image-meta").textContent =
    `[${channelLabel}] ${results.length} results · ` +
    `quota=${payload.quota_consumed ?? 0} CU` +
    (provider ? ` · provider=${provider}` : "") +
    (payload.cached ? " · cached (floor-CU billed)" : "");
  grid.innerHTML = "";
  for (const r of results) {
    const card = document.createElement("figure");
    card.className = "image-card";

    const img = document.createElement("img");
    // thumbnail_url / width / height / license_hint are OPTIONAL — never
    // assume they exist (provider-agnostic contract).
    img.src = r.thumbnail_url || r.image_url;
    img.alt = r.title || r.image_url;
    img.loading = "lazy";

    const cap = document.createElement("figcaption");
    const a = document.createElement("a");
    a.href = r.source_url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = r.title || r.source_url;
    const dims = document.createElement("div");
    dims.className = "muted";
    dims.textContent = r.width && r.height ? `${r.width}×${r.height}` : "";

    const grabBtn = document.createElement("button");
    grabBtn.className = "tiny";
    grabBtn.textContent = "grab";
    grabBtn.title =
      "Download host-side into APS file storage via anna.web.image_fetch";
    grabBtn.addEventListener("click", () => {
      $("image-url-input").value = r.image_url;
      hostImageFetch(r.image_url);
    });

    cap.append(a, dims, grabBtn);
    card.append(img, cap);
    grid.appendChild(card);
  }
  if (!results.length) {
    grid.innerHTML = '<p class="muted">(no results)</p>';
  }
}

function imageSearchParams() {
  const query = $("image-query-input").value.trim();
  if (!query) {
    showStatus("input", "image query must be non-empty", true);
    return null;
  }
  // max_results: platform hard cap is 50 (values above the cap are
  // REJECTED host-side, never silently truncated — so validate here).
  const rawMax = parseInt($("image-max-input").value, 10);
  if (!Number.isInteger(rawMax) || rawMax < 1 || rawMax > 50) {
    showStatus("input", "max results must be an integer in 1..50", true);
    return null;
  }
  const params = { query, max_results: rawMax };
  const aspect = $("aspect-select").value;
  if (aspect && aspect !== "any") params.aspect = aspect;
  return params;
}

async function hostImageSearch() {
  const params = imageSearchParams();
  if (!params) return;
  const btn = $("host-image-search-btn");
  btn.disabled = true;
  showStatus("anna.web.image_search", "searching images…");
  try {
    const anna = await annaReady;
    // `anna.web.imageSearch(...)` (camelCase alias) hits the same wire
    // method; we use the wire name to match the manifest host_api entry.
    const res = await anna.web.image_search(params);
    renderImageResults(res, "HOST API");
    showRaw("anna.web.image_search", res);
    showStatus(
      "anna.web.image_search",
      `ok — ${res.results.length} results, quota_consumed=${res.quota_consumed} CU` +
        (res.cached ? " (served from the 10-min result cache)" : "")
    );
  } catch (err) {
    // allowImageSearch defaults OFF → APP_NOT_GRANTED until the user (or
    // the dev-install seed) enables it in the app's permission card.
    showStatus("anna.web.image_search", err, true);
    showRaw("anna.web.image_search error", err);
  } finally {
    btn.disabled = false;
  }
}

async function rpcImageSearch() {
  const params = imageSearchParams();
  if (!params) return;
  const btn = $("rpc-image-search-btn");
  btn.disabled = true;
  showStatus("tools.invoke(web_image_search)", "searching via Executa…");
  try {
    const reply = await invokeExecuta("web_image_search", params);
    const data = unwrap(reply);
    renderImageResults(data, "Reverse RPC");
    showRaw("tools.invoke(web_image_search)", reply);
    showStatus(
      "tools.invoke(web_image_search)",
      `ok — ${(data.results || []).length} results (same contract & pricing ` +
        `as the HOST API channel)`
    );
  } catch (err) {
    showStatus("tools.invoke(web_image_search)", err, true);
    showRaw("tools.invoke(web_image_search) error", err);
  } finally {
    btn.disabled = false;
  }
}

async function hostImageFetch(rawUrl) {
  const url = (rawUrl || $("image-url-input").value).trim();
  if (!url) {
    showStatus("anna.web.image_fetch", "paste an image URL first", true);
    return;
  }
  const btn = $("image-fetch-btn");
  btn.disabled = true;
  showStatus("anna.web.image_fetch", "downloading host-side…");
  $("image-fetch-out").textContent = "(downloading…)";
  $("image-fetch-preview").innerHTML = "";
  try {
    const anna = await annaReady;
    const res = await anna.web.image_fetch(
      { url, purpose: "web-search-demo" },
      { timeoutMs: 90000 }
    );
    showRaw("anna.web.image_fetch", res);
    // Artifact REFERENCE — render the short-lived get_url directly.
    const img = document.createElement("img");
    img.src = res.get_url;
    img.alt = res.path;
    $("image-fetch-preview").appendChild(img);
    $("image-fetch-out").textContent = [
      `path:       ${res.path}   (APS files, app self-scope)`,
      `mime_type:  ${res.mime_type}`,
      `bytes_size: ${res.bytes_size}`,
      `sha256:     ${res.sha256}`,
      `final_url:  ${res.final_url}`,
      `quota:      ${res.quota_consumed} CU (+ storage quota)`,
    ].join("\n");
    showStatus(
      "anna.web.image_fetch",
      `ok — stored as ${res.path} (${res.bytes_size} bytes ${res.mime_type})`
    );
  } catch (err) {
    // SSRF-blocked / non-image / oversized URLs are rejected without
    // billing: APP_INVALID_REQUEST (SSRF/params) or APP_PROVIDER_ERROR
    // (download failed). allowImageFetch defaults OFF → APP_NOT_GRANTED.
    showStatus("anna.web.image_fetch", err, true);
    showRaw("anna.web.image_fetch error", err);
  } finally {
    btn.disabled = false;
  }
}

// ─── Wire up ─────────────────────────────────────────────────────────────────

$("host-search-btn").addEventListener("click", hostSearch);
$("rpc-search-btn").addEventListener("click", rpcSearch);
$("both-search-btn").addEventListener("click", () => {
  hostSearch();
  rpcSearch();
});
$("fetch-btn").addEventListener("click", () => hostFetch());
$("research-btn").addEventListener("click", rpcResearch);
$("host-image-search-btn").addEventListener("click", hostImageSearch);
$("rpc-image-search-btn").addEventListener("click", rpcImageSearch);
$("image-fetch-btn").addEventListener("click", () => hostImageFetch());

annaReady.then((anna) => {
  anna.window.set_title({ title: "Web Search Demo" }).catch(() => {});
  showStatus("runtime", "connected — run a search over either channel");
});
