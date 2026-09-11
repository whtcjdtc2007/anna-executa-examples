/* Detail sub-view: renders the capture handed over via entry_payload. */
import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

const anna = await AnnaAppRuntime.connect();
const p = anna.entryPayload || {};
if (p.data_url) {
  const img = document.getElementById("img");
  img.src = p.data_url;
  img.hidden = false;
  document.getElementById("meta").textContent =
    `${p.mime || "?"} · ${p.width || "?"}×${p.height || "?"} · ${p.byte_size || "?"} bytes`;
} else {
  document.getElementById("meta").textContent = "No capture payload.";
}
