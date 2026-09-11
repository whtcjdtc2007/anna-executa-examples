/* Mobile entry: full capture → detail → share loop with haptic feedback. */
import {
  buzz,
  captureAndPreview,
  connect,
  describeError,
  openDetail,
  shareCapture,
} from "./shared.js";

const $ = (id) => document.getElementById(id);
let capture = null;

function show(id, msg, cls) {
  const el = $(id);
  el.className = "status " + (cls || "muted");
  el.textContent = msg;
}

const anna = await connect().catch((e) => {
  show("capture-status", "SDK connect failed: " + e.message, "err");
  throw e;
});

async function doCapture(camera) {
  show("capture-status", "Opening camera…");
  try {
    capture = await captureAndPreview(anna, { camera });
    buzz(anna, "notification", "success");
    $("preview").src = capture.data_url;
    $("preview").hidden = false;
    $("capture-meta").textContent =
      `${capture.mime} · ${capture.width}×${capture.height} · ${capture.byte_size} bytes (EXIF stripped)`;
    $("btn-detail").disabled = false;
    $("btn-share-img").disabled = false;
    show("capture-status", "Captured.", "ok");
  } catch (e) {
    const { code, hint } = describeError(e);
    buzz(anna, "notification", "error");
    // cancelled is a normal outcome — render it neutrally.
    show("capture-status", `code=${code} — ${hint}`, code === "cancelled" ? "muted" : "err");
  }
}

$("btn-capture").addEventListener("click", () => doCapture("back"));
$("btn-capture-front").addEventListener("click", () => doCapture("front"));

$("btn-detail").addEventListener("click", async () => {
  if (!capture) return;
  buzz(anna, "impact", "light");
  try {
    const res = await openDetail(anna, capture);
    show("detail-status", "Opened detail window " + res.window_uuid, "ok");
  } catch (e) {
    const { code, hint } = describeError(e);
    show("detail-status", `code=${code} — ${hint}`, "err");
  }
});

$("btn-share-img").addEventListener("click", async () => {
  if (!capture) return;
  buzz(anna, "impact", "medium");
  try {
    const res = await shareCapture(anna, capture, "Anna capture");
    // {shared:false} = user dismissed the sheet — NOT an error.
    show("share-status", res.shared ? "Shared." : "Share sheet dismissed (shared: false).", res.shared ? "ok" : "muted");
  } catch (e) {
    const { code, hint } = describeError(e);
    show("share-status", `code=${code} — ${hint}`, "err");
  }
});

$("btn-share-text").addEventListener("click", async () => {
  buzz(anna, "impact", "light");
  try {
    const res = await anna.mobile.share({ text: "Hello from Mobile Bridge Demo" });
    show("share-status", res.shared ? "Shared." : "Dismissed (shared: false).", res.shared ? "ok" : "muted");
  } catch (e) {
    const { code, hint } = describeError(e);
    show("share-status", `code=${code} — ${hint}`, "err");
  }
});

$("haptics-row").addEventListener("click", (ev) => {
  const spec = ev.target && ev.target.dataset && ev.target.dataset.h;
  if (!spec) return;
  const [type, style] = spec.split(":");
  anna.mobile
    .haptics(style ? { type, style } : { type })
    .catch((e) => show("share-status", "haptics: " + describeError(e).code, "muted"));
});
