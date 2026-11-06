# 平台运行时演示 (`anna-app-platform-runtime-demo`)

**平台共享运行时契约**（forum #490 / `executa-shared-runtime-node-chrome.md`）
的官方配套 App：探测你的 Agent 提供了什么运行时、预演 `runtime.requires`
安装预检、用**平台 Chrome** 把 HTML 渲染成 PNG/PDF，并演示如何把 Executa
从 316 MiB 瘦身到约 5 MiB。

## 运行

```bash
npm install            # 同时为 executa 安装 puppeteer-core（不会下载 Chrome）
npm run dev            # anna-app dev
npm test               # node --test —— requires.js 与 Agent 预检同一套用例
npm run build:executa  # 构建 5 个平台的轻量 binary 归档
```

## 发现契约（30 秒速览）

Agent 向每个 Executa 子进程注入**存在即可用**的环境变量：

| | cloud-linux | desktop-macos | desktop-windows |
|---|---|---|---|
| `ANNA_CHROME_EXECUTABLE` | `/ms-playwright/chromium-<rev>/chrome-linux/chrome` | `~/Library/Caches/ms-playwright/chromium-<rev>/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing` | `%LOCALAPPDATA%\ms-playwright\chromium-<rev>\chrome-win64\chrome.exe` |
| `ANNA_NODE_EXECUTABLE` | `/opt/anna/node/bin/node` | `~/.anna/runtimes/node-22/bin/node` | `%USERPROFILE%\.anna\runtimes\node-22\node.exe` |

（上表中 `chromium-<rev>` 目录名由 **Agent** 为你解析——永远不要自己猜；
`lib/discovery.js` 的 `legacy-scan` 只为了让本 demo 在未升级的 Agent 上
也能工作，并带黄色警告提示升级。）

## 降级矩阵（亦是验收用例）

| Agent 状态 | Probe | Precheck | Render | 轻量 binary 包 |
|---|---|---|---|---|
| 现网（Phase 0）云端 | ⚠ legacy-scan，Chrome 可用；Node none | node ✗ chrome ✓ | ✓（经 legacy 路径） | ✗ shim exit 78 |
| 现网桌面（已自动装 Playwright） | 同上 | 同上 | ✓ | ✗ |
| Phase 1 Agent | ✓ contract；Node none | node ✗ chrome ✓ | ✓ | ✗ |
| Phase 2 Agent | ✓ contract；Node 22 | ✓ ✓ | ✓ | ✓（安装期预检通过） |
| Phase 2 Agent，用户卸载了浏览器 | contract 无 chrome | chrome ✗ | `chrome_unavailable` | 安装期 `runtime_missing` |

## 体积对比

| | 压缩 | 解压 |
|---|---|---|
| 全量自带的 PPT 引擎（forum #490） | 316.3 MiB | 849.9 MiB |
| 本 Executa（使用平台运行时） | ≈ 5 MiB | ≈ 20 MiB |

做法：`puppeteer-core` + `executablePath`（绝不用全量 `puppeteer` 包——
设置 `"puppeteer": {"skipDownload": true}`）、两行 shell 壳入口
`exec "$ANNA_NODE_EXECUTABLE" …`、以及 `executa.json` 里的
`runtime.requires`，让 Phase 2 之前的 Agent 在安装期以结构化的
`reason_code: runtime_missing` 拒绝，而不是 invoke 时崩溃。

文档：Developer Hub 的 *Platform runtimes* 页（`executa-platform-runtimes`）。
