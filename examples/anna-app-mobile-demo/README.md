# Mobile Bridge Demo (`anna-app-mobile-demo`)

[简体中文](./README.zh-CN.md)

Reference implementation for the **Anna App mobile native bridge**
(`anna.mobile.*`, mobile runtime Phase 3) — and for the dual-entry
(`mobile_entry`) mechanism.

> **Read this first**: responsive **single-entry** apps are the recommended
> path (see `finder` / `inbox-app`). `mobile_entry` is an escape hatch for
> heavy UIs; this demo uses it deliberately to exercise the mechanism, not
> as an endorsement.

## What it demonstrates

| Surface | Where |
| --- | --- |
| Declaring the bridge (`ui.host_api.mobile`) + `ui.form_factors` | `manifest.json` |
| `camera_capture` → preview → `share({data_url})`, with `haptics` feedback | `bundle/mobile.js` |
| Sub-view via `window.open_view` whose child window keeps the **mobile** entry (`mobile_entry` inheritance) | `bundle/detail-mobile.html` vs `bundle/detail.html` badges |
| Clean desktop degradation on `unsupported_container` | `bundle/desktop.js` |
| Local development with `dev.mocks["mobile.*"]` | `manifest.json` `dev` block |

## Error-code layering (memorize this)

| Code | Meaning | App reaction |
| --- | --- | --- |
| `unsupported_container` | Grant OK, container is not the anna-mobile shell | Degradation notice; never retry |
| `permission_denied` | `ui.host_api.mobile` grant missing | Fix the manifest |
| `permission_denied_by_user` | OS camera refusal | Point at system settings |
| `cancelled` | User backed out of the capture | Normal outcome |
| `too_large` | Over `max_bytes` after one re-compression pass | Lower quality |
| `mobile_bridge_timeout` | Native layer unresponsive (30 s) | Offer retry |

`share` user-cancel is **not an error** — it resolves `{shared: false}`.

## Run locally

```bash
anna-app validate   # needs CLI ≥ 0.1.51 (schema bundle 0.22.0)
anna-app dev        # dev.mocks make the whole flow runnable without a phone
```

The mocks return canned success shapes copied from the real wire contract.
Edit `manifest.json → dev.mocks` to exercise the `{shared: false}`,
`cancelled` or `too_large` paths.

## On-device acceptance walkthrough

1. Install + open from the anna-mobile Launcher → the **MOBILE ENTRY** badge
   must show (dual entry works).
2. Take photo → preview renders with `width×height · bytes`, EXIF stripped.
3. Open capture detail → the child window must show **MOBILE ENTRY**
   (mobile_entry inheritance anchor, nexus ≥ 1.1.0-beta.144).
4. Share photo → system share sheet; dismissing it shows
   `shared: false` (not an error).
5. Haptics lab → all seven buttons are physically distinguishable.
6. Open the same app on the desktop dashboard → all three capability buttons
   render the `unsupported_container` degradation notice.

## Not demonstrated (deliberately)

`push_token` (deferred — no notification pipeline yet), theme propagation
(no SDK protocol yet), in-app purchases (banned pending IAP design).
