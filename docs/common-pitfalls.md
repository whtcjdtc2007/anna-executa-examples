# Common Pitfalls

A short list of the bugs that show up most often when authors build Executa plugins. If your plugin "installs" but the Anna UI shows it as **Stopped** (or it doesn't appear at all), this page is the first place to look.

---

## 1. Plugin process exits after one request

**Symptom**

- `describe` works once when you test by hand: `echo '{"jsonrpc":"2.0","method":"describe","id":1}' | ./my-plugin` returns a manifest.
- The Agent UI shows the plugin card as **Stopped**, even immediately after install.
- Each tool invocation pays a noticeable cold-start delay.

**Cause**

The Executa protocol is **long-running**: the Agent spawns one process per plugin and reuses it for every `describe`/`invoke`/`health` call. A plugin that returns from `main()` (or calls `sys.exit()` / `process.exit()` / `os.Exit()`) after handling a single request is broken — the next request triggers a restart, and the UI never sees a live process.

**Fix**

Always loop on stdin until EOF. The Agent closes stdin when it wants you to shut down.

```python
# Python
import json, sys
for line in sys.stdin:                   # ← loop until EOF
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    resp = handle(req)
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()                   # ← required
```

```javascript
// Node.js
const readline = require("readline");
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const req = JSON.parse(line);
  process.stdout.write(JSON.stringify(handle(req)) + "\n");
});
// Don't call process.exit(); let the runtime exit naturally on stdin close.
```

```go
// Go
scanner := bufio.NewScanner(os.Stdin)
scanner.Buffer(make([]byte, 0, 1024*1024), 1024*1024)
for scanner.Scan() {                     // ← loop until EOF
    line := strings.TrimSpace(scanner.Text())
    if line == "" { continue }
    // ... handle and Fprintln(os.Stdout, ...) ...
}
```

**Quick local check**

```bash
./my-plugin <<< '{"jsonrpc":"2.0","method":"describe","id":1}' &
PID=$!
sleep 2
if kill -0 $PID 2>/dev/null; then
  echo "OK — still running"
  kill $PID
else
  echo "BUG — plugin exited after one request"
fi
```

The `python/build_binary.sh --test` script runs this check automatically.

---

## 2. Three names don't match (`tool_id` vs `describe.name` vs `manifest.json` `name`)

**Symptom**

- The plugin appears under **Extra Agent Plugins** in the UI instead of next to the tool you installed.
- Or: the user-installed card shows **Stopped** while a duplicate appears as **Running** elsewhere.
- Or: `~/.anna/executa/bin/` contains a file with a generic name like `tool` or `plugin` instead of your tool ID.

**Cause**

Three identifiers must be **exactly** equal:

| Where | What |
|---|---|
| Anna `/executa` form (Tool ID field) | The `tool_id` minted with the 🪪 button, e.g. `tool-acme-my-tool-abcd1234` |
| Your binary | The `name` field in the manifest your plugin returns from `describe` |
| Archive root `manifest.json` | The `name` field in the JSON file you ship inside the `.tar.gz` / `.zip` |

The Agent UI joins user-installed tools to running plugins by string-matching these three. The archive `manifest.json` `name` also becomes the launcher symlink at `~/.anna/executa/bin/{name}`.

**Fix**

Pick the value once when you mint the `tool_id`, then paste it everywhere. For example:

```json5
// manifest.json (in archive)
{ "name": "tool-acme-my-tool-abcd1234", "version": "1.0.0", "runtime": { "binary": { "entrypoint": "bin/my-tool" } } }
```

```python
# inside the binary, what describe returns
MANIFEST = { "name": "tool-acme-my-tool-abcd1234", "display_name": "My Tool", ... }
```

The `display_name` field is for the human-readable label — it's safe to make that pretty (`"My Tool"`); only `name` has to match.

---

## 3. Banner / debug text on stdout

**Symptom**

- Agent log shows `Failed to parse JSON-RPC frame` or similar.
- `describe` times out from the Agent side even though `echo … | ./my-plugin` looks fine.

**Cause**

You printed something to stdout before (or between) JSON-RPC responses — a startup banner, a `print("ready")`, a stray `console.log`, etc. The Agent only treats lines that parse as JSON objects as protocol frames; banners may garble the line buffering.

**Fix**

All human-readable output goes to **stderr**:

```python
print("🔌 plugin started", file=sys.stderr)            # ✅
sys.stdout.write(json.dumps(response) + "\n")          # ✅ JSON-RPC only
```

```javascript
console.error("🔌 plugin started");                    // ✅
process.stdout.write(JSON.stringify(response) + "\n"); // ✅
```

---

## 4. Missing `manifest.json` in multi-file archives

**Symptom**

- After install, the Agent picks the wrong executable as the entrypoint (e.g. a helper binary instead of your main one).
- Auxiliary scripts (`bin/post-install.sh`, sub-CLIs) get `Permission denied` at runtime.
- `~/.anna/executa/bin/` ends up with a generic name derived from the URL.

**Cause**

Without `manifest.json`, the Agent walks a five-level fallback (asset `entrypoint` → `bin/{name}` → only-or-first executable) and ZIP archives lose Unix permission bits. Both produce silent footguns when your archive contains more than one executable.

**Fix**

Always ship `manifest.json` at the archive root, even for single-file binaries. See [`binary-distribution.md`](./binary-distribution.md) and the [multi-file Python example](../examples/multifile-binary/python-pyinstaller-onedir/).

```json
{
  "name": "tool-acme-my-tool-abcd1234",
  "version": "1.0.0",
  "runtime": {
    "binary": {
      "entrypoint": {
        "default":         "bin/my-tool",
        "windows-x86_64":  "bin/my-tool.exe"
      },
      "permissions": {
        "bin/my-tool":         "0o755",
        "bin/post-install.sh": "0o755"
      }
    }
  }
}
```

---

## 5. PyInstaller cold start exceeds the default 5 s `describe` timeout

**Symptom**

- First-ever invocation (or first call after a fresh install) fails with a `describe timeout`.
- Subsequent calls work fine.

**Cause**

PyInstaller `--onefile` extracts the bundle to a temp directory on first launch; on a 200 MB+ binary this can take 10–30 s, especially on macOS Apple Silicon under Rosetta or on slow filesystems.

**Fix**

Three options, in order of preference:

1. Switch to `--onedir` and ship as a multi-file archive. No extract on first launch. See [`examples/multifile-binary/python-pyinstaller-onedir`](../examples/multifile-binary/python-pyinstaller-onedir/).
2. Reduce the bundle size. `--exclude-module` for things you don't need, `--noconfirm`, and audit `--collect-all` flags.
3. Rely on the Agent's binary cold-start timeout (60 s) which kicks in automatically during the post-install scan. Subsequent calls use the standard 5 s.

---

## See also

- [Protocol Specification](./protocol-spec.md) — line-delimited JSON-RPC 2.0 over stdio
- [Binary Distribution](./binary-distribution.md) — single-file vs multi-file archive shapes
- [Multi-file Python example](../examples/multifile-binary/python-pyinstaller-onedir/) — reference layout
