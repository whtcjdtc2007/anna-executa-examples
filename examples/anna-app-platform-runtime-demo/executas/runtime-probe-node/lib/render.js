/**
 * render.js — HTML → PNG / PDF via the PLATFORM Chrome (puppeteer-core).
 *
 * Demonstrates the core trick of forum #490: a zero-browser-bytes Executa
 * driving the Agent's preinstalled Chrome for Testing through
 * `ANNA_CHROME_EXECUTABLE`. Outputs land under
 * `$ANNA_WORKSPACE_DIR/platform-runtime-demo/`.
 *
 * Error codes (returned as `error_code`):
 *   chrome_unavailable — no usable Chrome (contract or legacy-scan)
 *   puppeteer_missing  — puppeteer-core not installed (run npm install)
 *   html_too_large     — html input exceeds 512 KiB
 */
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { discover } = require("./discovery.js");

const HTML_MAX_BYTES = 512 * 1024;
const PNG_INLINE_MAX_BYTES = 1024 * 1024;
const RENDER_TIMEOUT_MS = 30_000;

// Cloud microVMs: non-root w/o user namespaces + tiny /dev/shm — both flags
// are required there and harmless on desktop.
const CHROME_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"];

function outputDir() {
  const root = process.env.ANNA_WORKSPACE_DIR
    || path.join(os.homedir(), "anna-workspace");
  const dir = path.join(root, "platform-runtime-demo");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function defaultHtml(title) {
  const t = title || "Platform runtime demo";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    body{margin:0;display:grid;place-items:center;height:100vh;
         font:600 42px/1.3 -apple-system,'Segoe UI',sans-serif;
         background:linear-gradient(135deg,#1e2a4a,#0f1626);color:#eef2ff}
    .card{padding:48px 64px;border-radius:24px;background:rgba(255,255,255,.06);
          border:1px solid rgba(255,255,255,.14);text-align:center}
    .sub{font-size:20px;font-weight:400;opacity:.7;margin-top:12px}
  </style></head><body><div class="card">${t}
    <div class="sub">rendered by the PLATFORM Chrome — zero browser bytes shipped</div>
  </div></body></html>`;
}

/**
 * @param {{html?:string, title?:string, format?:"png"|"pdf",
 *          width?:number, height?:number, full_page?:boolean}} args
 */
async function renderHtml(args) {
  const format = args.format === "pdf" ? "pdf" : "png";
  const width = Math.min(Math.max(Number(args.width) || 1280, 320), 3840);
  const height = Math.min(Math.max(Number(args.height) || 720, 240), 2160);

  const html = args.html || defaultHtml(args.title);
  if (Buffer.byteLength(html, "utf-8") > HTML_MAX_BYTES) {
    return {
      success: false,
      error_code: "html_too_large",
      error: `html exceeds ${HTML_MAX_BYTES} bytes`,
    };
  }

  const runtimes = discover();
  if (!runtimes.chrome || !runtimes.chrome.usable) {
    return {
      success: false,
      error_code: "chrome_unavailable",
      error:
        "No usable Chrome on this Agent (neither the ANNA_CHROME_EXECUTABLE "
        + "contract nor a legacy PLAYWRIGHT_BROWSERS_PATH scan found one).",
      advice: runtimes.advice,
    };
  }

  let puppeteer;
  try {
    puppeteer = require("puppeteer-core");
  } catch {
    return {
      success: false,
      error_code: "puppeteer_missing",
      error: "puppeteer-core is not installed — run `npm install` in the executa dir.",
    };
  }

  const started = Date.now();
  const reported = chromeReportedVersion(runtimes.chrome);
  let browser;
  try {
    browser = await puppeteer.launch({
      executablePath: runtimes.chrome.executable,
      headless: true,
      args: CHROME_ARGS,
      timeout: RENDER_TIMEOUT_MS,
    });
    const page = await browser.newPage();
    await page.setViewport({ width, height });
    await page.setContent(html, {
      waitUntil: "networkidle0",
      timeout: RENDER_TIMEOUT_MS,
    });

    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const file = path.join(outputDir(), `render-${stamp}.${format}`);
    let bytes;
    if (format === "pdf") {
      bytes = await page.pdf({
        path: file,
        printBackground: true,
        width: `${width}px`,
        height: `${height}px`,
      });
    } else {
      bytes = await page.screenshot({
        path: file,
        type: "png",
        fullPage: Boolean(args.full_page),
      });
    }

    const result = {
      success: true,
      format,
      mime: format === "pdf" ? "application/pdf" : "image/png",
      bytes: bytes.length,
      path: file,
      viewport: { width, height },
      elapsed_ms: Date.now() - started,
      chrome: {
        executable: runtimes.chrome.executable,
        version: runtimes.chrome.version,
        reported,
        source: runtimes.chrome.source,
      },
    };
    if (format === "png" && bytes.length <= PNG_INLINE_MAX_BYTES) {
      result.data_url = `data:image/png;base64,${Buffer.from(bytes).toString("base64")}`;
    }
    return result;
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
}

function chromeReportedVersion(chrome) {
  try {
    const { chromeVersionFromBinary } = require("./discovery.js");
    return chromeVersionFromBinary(chrome.executable);
  } catch {
    return null;
  }
}

module.exports = { renderHtml, HTML_MAX_BYTES, PNG_INLINE_MAX_BYTES };
