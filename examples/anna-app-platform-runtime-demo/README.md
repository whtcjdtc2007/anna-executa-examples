# Platform Runtime Demo (`anna-app-platform-runtime-demo`)

The official companion app for the **platform shared-runtime contract**
(forum #490 / `executa-shared-runtime-node-chrome.md`): probe what your Agent
provides, preview `runtime.requires` install prechecks, render HTML → PNG/PDF
through the **platform Chrome**, and ship a ≈5 MiB Executa instead of 316 MiB.

```
anna-app-platform-runtime-demo/
├── app.json                      # slug platform-runtime-demo; bundled_executas.runtime-probe
├── manifest.json                 # schema 4; required_executas bundled:runtime-probe; csp img-src data:
├── package.json                  # postinstall → executas/runtime-probe-node npm install
├── bundle/                       # UI: Probe / Precheck / Render / Ship-it-light
└── executas/runtime-probe-node/
    ├── executa.json              # type node; runtime.requires; profiles local(active)+binary
    ├── runtime_probe_plugin.js   # stdio JSON-RPC: describe / invoke / health
    ├── lib/discovery.js          # contract → legacy-scan → null (+ advice)
    ├── lib/requires.js           # same semver subset the Agent prechecks (node --test)
    ├── lib/render.js             # puppeteer-core + ANNA_CHROME_EXECUTABLE
    ├── bin/runtime-probe(.cmd)   # lightweight shim: exec "$ANNA_NODE_EXECUTABLE" … (exit 78 when absent)
    ├── archive/manifest.json     # archive-root manifest (copied by build.sh)
    └── build.sh                  # 5 platform tar.gz, no Node/Chrome inside, prints size report
```

## Run it

```bash
npm install          # also installs puppeteer-core for the executa (no Chrome download)
npm run dev          # anna-app dev
npm test             # node --test — requires.js matrix (mirrors the Agent's)
npm run build:executa  # builds the 5 lightweight binary archives
```

## The discovery contract in 30 seconds

The Agent injects presence-based env vars into every Executa process — a
variable is only set when the runtime is actually usable:

| | cloud-linux | desktop-macos | desktop-windows |
|---|---|---|---|
| `ANNA_CHROME_EXECUTABLE` | `/ms-playwright/chromium-<rev>/chrome-linux/chrome` | `~/Library/Caches/ms-playwright/chromium-<rev>/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing` | `%LOCALAPPDATA%\ms-playwright\chromium-<rev>\chrome-win64\chrome.exe` |
| `ANNA_NODE_EXECUTABLE` | `/opt/anna/node/bin/node` | `~/.anna/runtimes/node-22/bin/node` | `%USERPROFILE%\.anna\runtimes\node-22\node.exe` |

(The `chromium-<rev>` names above are what the **Agent** resolves for you —
never guess them yourself; `legacy-scan` in `lib/discovery.js` exists only to
keep this demo useful on pre-contract Agents and is flagged with a warning.)

## Degradation matrix (acceptance checklist)

| Agent state | Probe | Precheck | Render | lightweight binary package |
|---|---|---|---|---|
| Current fleet (Phase 0), cloud | ⚠ legacy-scan; Chrome usable; Node none | node ✗ chrome ✓ | ✓ (via legacy path) | ✗ shim exit 78 |
| Current fleet, desktop (Playwright auto-installed) | same | same | ✓ | ✗ |
| Phase 1 Agent | ✓ contract; Node none | node ✗ chrome ✓ | ✓ | ✗ |
| Phase 2 Agent | ✓ contract; Node 22 | ✓ ✓ | ✓ | ✓ (install precheck passes) |
| Phase 2 Agent, browser uninstalled by user | contract without chrome | chrome ✗ | `chrome_unavailable` | install-time `runtime_missing` |

## Size comparison (why this matters)

| | compressed | unpacked |
|---|---|---|
| bundle-everything PPT engine (forum #490) | 316.3 MiB | 849.9 MiB |
| this Executa (platform runtimes) | ≈ 5 MiB | ≈ 20 MiB |

How: `puppeteer-core` + `executablePath` (never the full `puppeteer` package —
set `"puppeteer": {"skipDownload": true}`), a 2-line shell shim entrypoint
`exec "$ANNA_NODE_EXECUTABLE" …`, and `runtime.requires` in `executa.json` so
pre-Phase-2 Agents reject the install with a structured
`reason_code: runtime_missing` instead of crashing at invoke time.

Docs: *Platform runtimes* in the Developer Hub (`executa-platform-runtimes`).
