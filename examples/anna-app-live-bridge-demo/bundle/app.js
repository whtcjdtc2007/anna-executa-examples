/**
 * Live Bridge Demo — forum #376 reference example.
 *
 * Three host surfaces, three panels:
 *   1. `entry_payload`        — hello-handshake value + LIVE events pushed when
 *                               the LLM re-invokes `open_app_view` on this
 *                               single-instance window with a fresh payload.
 *                               With SDK >= 0.19.0 `anna.entryPayload` is
 *                               refreshed automatically before handlers run —
 *                               the demo asserts that.
 *   2. `runtime_state_synced` — `{patch}` events from the LLM's
 *                               `update_app_view(runtime_state_patch=...)`;
 *                               SDK >= 0.19.0 shallow-merges into
 *                               `anna.runtimeState`.
 *   3. `chat.append_artifact` — the CORRECT nested call shape
 *                               `{ artifact: { kind, summary, data } }`.
 *
 * Common pitfalls this demo exists to prevent (see README):
 *   - flat `append_artifact({kind, ...})` args → empty artifact;
 *   - `payload` field instead of `data`;
 *   - treating a successful `open_app_view` tool result as delivery proof.
 */
import { AnnaAppRuntime } from "/static/anna-apps/_sdk/latest/index.js";

const $ = (id) => document.getElementById(id);
const logEl = $("log");

function log(line) {
  const stamp = new Date().toISOString();
  logEl.textContent += `${stamp} ${line}\n`;
  logEl.scrollTop = logEl.scrollHeight;
  console.info("[live-bridge-demo]", line);
}

function show(id, value) {
  $(id).textContent =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

let anna = null;

// ---- 1. entry payload -------------------------------------------------------

function handleEntryPayload(payload, source) {
  show("entry-payload-view", payload || {});
  const req = payload && payload.liveBridgeRequest;
  log(
    `entry_payload via ${source}` +
      (req ? ` requestId=${req.requestId}` : " (no liveBridgeRequest key)")
  );
  // SDK >= 0.19.0 keeps the mirror fresh BEFORE dispatching handlers.
  if (source === "entry_payload event" && anna) {
    const mirrored =
      JSON.stringify(anna.entryPayload) === JSON.stringify(payload || {});
    log(
      mirrored
        ? "assert ok: anna.entryPayload auto-refreshed (SDK >= 0.19.0)"
        : "assert FAILED: anna.entryPayload is stale — SDK < 0.19.0?"
    );
  }
}

$("build-prompt").onclick = () => {
  const appId = Number($("app-id").value);
  if (!Number.isInteger(appId) || appId < 1) {
    log("enter this app's numeric ID first (string slugs are rejected)");
    return;
  }
  const args = {
    app_id: appId,
    view: "main",
    payload: {
      liveBridgeRequest: {
        schemaVersion: 1,
        requestId: `demo-${Date.now()}`,
        issuedAt: Date.now(),
      },
    },
  };
  show(
    "prompt-view",
    "Call open_app_view exactly once with the following arguments. " +
      "Report the tool result only — the app logs an entry_payload event " +
      "when the payload actually arrives.\n" +
      JSON.stringify(args, null, 2)
  );
  log(`prompt built for app_id=${appId} (${args.payload.liveBridgeRequest.requestId})`);
};

// ---- 3. chat artifact -------------------------------------------------------

$("send-artifact").onclick = async () => {
  const requestId = `manual-${Date.now()}`;
  try {
    // Correct shape: nested `artifact`, data under `data` (NOT `payload`).
    const ack = await anna.chat.append_artifact({
      artifact: {
        kind: "app_event",
        summary: `Live Bridge Demo · test artifact · ${requestId}`,
        data: { schemaVersion: 1, requestId, ok: true },
      },
    });
    log(`artifact acknowledged ${requestId} → ${ack && ack.artifact_id}`);
  } catch (err) {
    log(
      `artifact FAILED ${requestId} → ${(err && err.code) || err} ` +
        "(permission_denied? declare ui.host_api.chat:[\"append_artifact\"] " +
        "and `anna-app apps push` — manifest ACL is live from push)"
    );
  }
};

// ---- boot -------------------------------------------------------------------

try {
  anna = await AnnaAppRuntime.connect();
  $("conn-status").textContent = `Connected — window ${anna.windowUuid}`;
  $("wid-hint").textContent = anna.windowUuid;

  handleEntryPayload(anna.entryPayload, "hello handshake");
  anna.on("entry_payload", (p) => handleEntryPayload(p, "entry_payload event"));

  show("runtime-state-view", anna.runtimeState || {});
  anna.on("runtime_state_synced", ({ patch } = {}) => {
    log(`runtime_state_synced patch=${JSON.stringify(patch)}`);
    show("runtime-state-view", {
      lastPatch: patch || {},
      mergedRuntimeState: anna.runtimeState || {},
    });
  });

  $("build-prompt").disabled = false;
  $("send-artifact").disabled = false;
  log("connected");
} catch (err) {
  $("conn-status").textContent =
    "Standalone preview — open inside the Anna dashboard (or `anna-app dev`) " +
    "to exercise the live bridge.";
  log(`host connection failed: ${(err && err.code) || err}`);
}
