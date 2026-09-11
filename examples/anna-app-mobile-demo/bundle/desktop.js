/* Desktop entry: shows the degradation contract in action. */
import { connect, describeError } from "./shared.js";

const statusEl = document.getElementById("status");

function show(msg, cls) {
  statusEl.className = "status " + (cls || "muted");
  statusEl.textContent = msg;
}

const anna = await connect().catch((e) => {
  show("SDK connect failed: " + e.message, "err");
  throw e;
});
show("Connected. Click a method — expect unsupported_container on desktop.");

function wire(id, call) {
  document.getElementById(id).addEventListener("click", async () => {
    show("Calling…");
    try {
      const res = await call();
      show("Result: " + JSON.stringify(res), "ok");
    } catch (e) {
      const { code, hint } = describeError(e);
      show(`Rejected with code=${code}\n${hint}`, code === "unsupported_container" ? "muted" : "err");
    }
  });
}

wire("btn-camera", () => anna.mobile.camera_capture({}));
wire("btn-share", () => anna.mobile.share({ text: "Hello from Mobile Bridge Demo" }));
wire("btn-haptics", () => anna.mobile.haptics({ type: "impact", style: "medium" }));
