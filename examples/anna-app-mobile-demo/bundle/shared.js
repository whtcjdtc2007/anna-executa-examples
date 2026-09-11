/* Mobile Bridge Demo — shared logic for both entries.
 *
 * Error-code cheat sheet (mobile runtime Phase 3):
 *   unsupported_container      — grant OK, but not inside the anna-mobile
 *                                shell (every desktop container). Degrade.
 *   permission_denied          — manifest grant missing. Fix the manifest.
 *   permission_denied_by_user  — OS-level camera refusal. Offer settings.
 *   cancelled                  — user backed out of the capture. Not an error.
 *   too_large                  — capture exceeded max_bytes after one
 *                                re-compression pass. Lower quality.
 *   mobile_bridge_timeout      — native layer unresponsive (30s).
 * `share` user-cancel is NOT an error: it resolves `{shared: false}`.
 */
import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

export const ERROR_HINTS = {
  unsupported_container:
    "This capability only exists inside the Anna mobile app. On desktop, render a notice like this one — don't retry.",
  permission_denied:
    "The manifest does not declare this method in ui.host_api.mobile.",
  permission_denied_by_user:
    "The user declined the OS camera permission. Point them at system settings; don't loop the prompt.",
  cancelled: "The user backed out. Treat as a normal outcome.",
  too_large:
    "Still over max_bytes after re-compression. Ask for a lower quality capture.",
  mobile_bridge_timeout: "Native layer didn't answer in 30s. Offer a retry.",
};

export function describeError(err) {
  const code = (err && err.code) || "unknown";
  const hint = ERROR_HINTS[code] || err?.message || "";
  return { code, hint };
}

export async function connect() {
  const anna = await AnnaAppRuntime.connect();
  return anna;
}

/** Fire-and-forget haptic; failures never disturb the main flow. */
export function buzz(anna, type, style) {
  anna.mobile.haptics({ type, style }).catch(() => {});
}

export async function captureAndPreview(anna, opts) {
  const res = await anna.mobile.camera_capture({
    camera: (opts && opts.camera) || "back",
    quality: 0.7,
    max_bytes: 4 * 1024 * 1024,
  });
  return res; // {data_url, mime, width, height, byte_size}
}

export async function shareCapture(anna, capture, title) {
  const res = await anna.mobile.share({
    title: title || "Anna capture",
    filename: "anna-capture.jpg",
    data_url: capture.data_url,
  });
  return res; // {shared: boolean} — false = user dismissed the sheet
}

/** Open the capture-detail sub-view. On mobile the child window MUST load
 * detail-mobile.html (mobile_entry inheritance — nexus beta.144 anchor). */
export function openDetail(anna, capture) {
  return anna.window.open_view({
    view: "capture-detail",
    payload: {
      data_url: capture.data_url,
      mime: capture.mime,
      width: capture.width,
      height: capture.height,
      byte_size: capture.byte_size,
    },
  });
}
