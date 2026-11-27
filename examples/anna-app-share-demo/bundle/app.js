// Share Demo — full lifecycle of Anna's public share links (share.* Host API;
// matrix-nexus docs/design/app-public-share-static-hosting.md):
//
//   1. Generate a tiny self-contained HTML document + stylesheet and upload
//      both to this app's own APS object space:
//      anna.files.upload_init → browser PUT to the presigned URL →
//      anna.files.upload_finalize   (requires `anna-app dev --storage aps`)
//   2. anna.share.publish — snapshot the finalized objects into a
//      platform-hosted public URL (manifest ui.host_api.share: true).
//   3. anna.share.list / update / revoke, and republish by passing pub_id
//      (same URL, version += 1).
//
// Loaded as a native ES module; the SDK is served by the host/harness.

import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

const annaReady = AnnaAppRuntime.connect();

const $ = (s) => document.querySelector(s);
const raw = $("#raw");
const show = (obj) => { raw.textContent = JSON.stringify(obj, null, 2); };
const setStatus = (el, html, err = false) => {
  el.innerHTML = html;
  el.classList.toggle("err", err);
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Paths of the most recent upload — published in step 2.
let uploaded = null;

// ---------------------------------------------------------------- upload

async function uploadOne(anna, path, bytes, contentType) {
  const init = await anna.files.upload_init({
    path,
    content_type: contentType,
    size: bytes.length,
  });
  const putRes = await fetch(init.put_url, {
    method: "PUT",
    headers: init.headers || {},
    body: bytes,
  });
  if (!putRes.ok) throw new Error(`R2 PUT ${putRes.status}`);
  const etag = (putRes.headers.get("ETag") || "").replace(/"/g, "") || init.upload_id;
  return anna.files.upload_finalize({ path, etag, size_bytes: bytes.length });
}

$("#btn-upload").addEventListener("click", async () => {
  const status = $("#upload-status");
  const headline = $("#headline").value.trim() || "Hello from Anna Share Demo";
  setStatus(status, "Uploading…");
  try {
    const anna = await annaReady;
    const stamp = Date.now().toString(36);
    const base = `share-demo/${stamp}`;
    const html = [
      "<!DOCTYPE html><html><head><meta charset=\"utf-8\">",
      `<title>${esc(headline)}</title>`,
      "<link rel=\"stylesheet\" href=\"doc.css\"></head><body>",
      `<main><h1>${esc(headline)}</h1>`,
      `<p>Published with the <code>anna.share.*</code> Host API at ${new Date().toISOString()}.</p>`,
      "<p>This page is a self-contained static snapshot — edit and republish from the Share Demo app; this link never changes.</p>",
      "</main></body></html>",
    ].join("");
    const css =
      "body{margin:0;display:grid;place-items:center;min-height:100vh;" +
      "font-family:Georgia,serif;background:#faf7f2;color:#222}" +
      "main{max-width:560px;padding:48px}h1{font-size:34px;margin:0 0 16px}" +
      "p{line-height:1.6;color:#555}code{background:#eee;padding:1px 6px;border-radius:4px}";
    const enc = new TextEncoder();
    await uploadOne(anna, `${base}/index.html`, enc.encode(html), "text/html; charset=utf-8");
    const fin = await uploadOne(anna, `${base}/doc.css`, enc.encode(css), "text/css");
    uploaded = { base, paths: [`${base}/index.html`, `${base}/doc.css`], entry: `${base}/index.html` };
    show(fin);
    $("#btn-publish").disabled = false;
    setStatus(status, `Uploaded <code>${esc(base)}/index.html</code> + <code>doc.css</code> — ready to publish.`);
  } catch (e) {
    setStatus(status, `Upload failed: ${esc(e.message)} — run <code>anna-app dev --storage aps</code>.`, true);
  }
});

// ---------------------------------------------------------------- publish

$("#btn-publish").addEventListener("click", async () => {
  const status = $("#publish-status");
  if (!uploaded) return;
  setStatus(status, "Publishing…");
  try {
    const anna = await annaReady;
    const pub = await anna.share.publish({
      paths: uploaded.paths,
      entry: uploaded.entry,
      title: $("#pub-title").value.trim() || "Share Demo Document",
      description: "Published from the anna-app-share-demo example app.",
    });
    show(pub);
    setStatus(status,
      `Published v${pub.version}: <a href="${esc(pub.url)}" target="_blank" rel="noopener">${esc(pub.url)}</a>`);
    await refreshList();
  } catch (e) {
    setStatus(status, `Publish failed: ${esc(e.message)}`, true);
  }
});

// ---------------------------------------------------------------- list + actions

async function refreshList() {
  const box = $("#pub-list");
  try {
    const anna = await annaReady;
    const { items } = await anna.share.list({});
    show({ items });
    if (!items.length) {
      box.innerHTML = '<p style="color:#8b949e;">No publications yet.</p>';
      return;
    }
    box.innerHTML = `
      <table><thead><tr><th>Title</th><th>Status</th><th>v</th><th>Link</th><th></th></tr></thead>
      <tbody>${items.map((it) => `
        <tr>
          <td>${esc(it.title)}</td>
          <td><span class="badge ${esc(it.status)}">${esc(it.status)}</span></td>
          <td>v${it.version}</td>
          <td><a href="${esc(it.url)}" target="_blank" rel="noopener">open</a></td>
          <td><div class="row-actions">
            ${it.status === "active" ? `
              <button class="secondary" data-act="republish" data-pub="${esc(it.pub_id)}">Republish</button>
              <button class="secondary" data-act="rename" data-pub="${esc(it.pub_id)}">Rename</button>
              <button class="danger" data-act="revoke" data-pub="${esc(it.pub_id)}">Revoke</button>` : ""}
          </div></td>
        </tr>`).join("")}
      </tbody></table>`;
    box.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => action(btn.dataset.act, btn.dataset.pub));
    });
  } catch (e) {
    box.innerHTML = `<p class="status err">list failed: ${esc(e.message)}</p>`;
  }
}

async function action(act, pubId) {
  const anna = await annaReady;
  try {
    if (act === "revoke") {
      if (!confirm("Revoke this publication? The public URL becomes a tombstone immediately.")) return;
      show(await anna.share.revoke({ pub_id: pubId }));
    } else if (act === "rename") {
      const title = prompt("New title:");
      if (!title) return;
      show(await anna.share.update({ pub_id: pubId, title }));
    } else if (act === "republish") {
      if (!uploaded) { alert("Generate & upload a document first (step 1)."); return; }
      show(await anna.share.publish({
        paths: uploaded.paths,
        entry: uploaded.entry,
        title: $("#pub-title").value.trim() || "Share Demo Document",
        pub_id: pubId, // same URL, version += 1
      }));
    }
    await refreshList();
  } catch (e) {
    alert(`${act} failed: ${e.message}`);
  }
}

$("#btn-refresh").addEventListener("click", refreshList);
refreshList();
