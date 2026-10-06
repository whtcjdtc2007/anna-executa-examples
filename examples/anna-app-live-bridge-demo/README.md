# anna-app-live-bridge-demo

A minimal `schema: 3` Anna app that exercises the **three host surfaces
connecting Anna's LLM to an already-open app window** (the forum #376
reference example and regression fixture):

1. **Live `entry_payload` delivery** — when the LLM calls `open_app_view`
   on a `single_instance: true` view that is **already open** with a fresh
   non-empty `payload`, the host pushes an `entry_payload` event (the full
   *merged* payload) into the running iframe. No refresh required.
   With `@anna-ai/app-runtime >= 0.19.0` the SDK also auto-refreshes
   `anna.entryPayload` before your handler runs — the demo asserts this.
2. **`runtime_state_synced` patches** — the LLM's
   `update_app_view(window_uuid, runtime_state_patch={...})` pushes a
   `{patch}` event; SDK >= 0.19.0 shallow-merges it into
   `anna.runtimeState`.
3. **`chat.append_artifact`** — appends a transient `app_event` card to the
   active chat, using the **correct nested argument shape**.

No Executas, storage, LLM calls or media — the manifest declares only
`window: ["ready", "set_title"]` and `chat: ["append_artifact"]`.

---

## Common pitfalls this demo exists to prevent

| Pitfall | Symptom | Fix |
| --- | --- | --- |
| Flat `append_artifact({kind, summary, ...})` args | RPC succeeds but the artifact is empty | Wrap in `artifact`: `anna.chat.append_artifact({ artifact: { kind, summary, data } })` |
| `payload` field on the artifact | Data silently dropped | The fields are `data` (inline JSON) / `payload_ref` |
| `permission_denied` on `append_artifact` | Manifest lacks the grant **as seen by the host** | Declare `ui.host_api.chat: ["append_artifact"]` and `anna-app apps push` — since forum #376 the manifest ACL is live from push (grants like llm/image still need `apps install`) |
| String app ID in `open_app_view` | `Input should be a valid integer` | `app_id` is the numeric `AnnaApp.id` — the demo auto-fills it from `anna.app.id` (hosts ≥ dispatcher 0.25.0) |
| Treating `open_app_view` success as delivery | "✅ opened existing window" but the app never saw the request | Only an `entry_payload` event (or a matching artifact) proves delivery — the demo logs both |

---

## Run locally

```bash
cd examples/anna-app-live-bridge-demo
npx --yes @anna-ai/cli@latest validate
npx --yes @anna-ai/cli@latest dev --no-llm
```

> The local harness serves the bundle and host bridge, but the
> *live re-invocation push* and the *chat artifact card* are dashboard
> host behaviours — verify those in production (below).

## Verify in production

Push + install as a disposable dev app and open the window — on hosts
≥ dispatcher 0.25.0 the numeric app ID is **auto-detected** from the
`hello` handshake (`anna.app.id`) and pre-filled; on older hosts type it
manually. Then run the three scripted scenarios:

1. **Live entry payload** — type the app ID into panel 1, click
   *Build open_app_view prompt*, paste the prompt into Anna main chat
   **while keeping the window open**. Expected: the event log prints
   `entry_payload via entry_payload event requestId=demo-…` within ~1s,
   plus `assert ok: anna.entryPayload auto-refreshed`. Re-running with the
   window open must keep working; a dashboard refresh must show the same
   merged payload from the hello handshake.
2. **Runtime state** — ask Anna to call `update_app_view` on the window
   UUID shown in panel 2 with `runtime_state_patch={"demoNote":"hello"}`.
   Expected: panel 2 shows the patch and the merged runtime state.
3. **Artifact** — click *Send test artifact*. Expected: the log prints the
   acknowledged `artifact_id` and a transient card appears in the chat
   (realtime-only — it disappears on refresh by design; thread
   persistence is Phase 3). Then remove the `chat` grant from
   `manifest.json`, `apps push` (no install) and click again: expected
   `permission_denied` immediately. Restore the grant, push, click —
   success again. This proves the ACL is live from push.

## Files

```
anna-app-live-bridge-demo/
├── app.json            # store metadata
├── manifest.json       # schema 3 — single_instance view, minimal host_api
├── package.json        # anna-app dev / validate scripts
└── bundle/
    ├── index.html      # three panels + event log
    ├── app.js          # SDK wiring (see inline comments)
    └── style.css
```
