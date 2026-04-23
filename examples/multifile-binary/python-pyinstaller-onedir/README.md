# Multi-file Binary Plugin — Python + PyInstaller --onedir

This example shows how to ship an Executa binary plugin **with** sibling
files: bundled `.so` / `.dylib`, sub-tools, configuration data, etc.
Anna Agent v2 supports this via a structured install layout and runtime
environment injection — your binary can find its bundled friends.

> Looking for a plain single-file binary instead?
> See `../python/` for a PyInstaller `--onefile` example.

## On-disk layout your binary sees at runtime

When the Agent installs your archive it lays it out like this:

    ~/.anna/executa/
      bin/example-multifile-tool          → tools/{tool_id}/current/bin/example-multifile-tool
      tools/{tool_id}/
        v1.0.0/                            ← every install gets its own version dir
          bin/example-multifile-tool       ← entrypoint
          lib/                              ← .so / .dylib live here
          data/greeting.txt                 ← bundled data
          manifest.json
          INSTALL.json                      ← install metadata (auto-written)
        current  → v1.0.0                  ← atomic blue-green upgrade pointer

The Agent injects these env vars before invoking your entrypoint:

| Variable               | Value                                          |
|------------------------|------------------------------------------------|
| `EXECUTA_HOME`         | absolute path to `tools/{tool_id}/current/`    |
| `EXECUTA_DATA`         | `${EXECUTA_HOME}/data` (when it exists)        |
| `LD_LIBRARY_PATH`      | prepended `${EXECUTA_HOME}/lib` (Linux)        |
| `DYLD_LIBRARY_PATH`    | prepended `${EXECUTA_HOME}/lib` (macOS)        |
| `PATH`                 | prepended `${EXECUTA_HOME}/share/bin` if any   |
| working directory      | `EXECUTA_HOME`                                  |

## Required: declare the entrypoint in `manifest.json`

A multi-file archive has no obvious "main" file, so you must tell the
Agent which one to launch:

```json
{
  "runtime": {
    "binary": {
      "entrypoint": "bin/example-multifile-tool",
      "lib_dirs": ["lib"],
      "data_dirs": ["data"]
    }
  }
}
```

If `manifest.json` is missing, the Nexus `binary_urls.{platform}.entrypoint`
field is used instead. As a last resort the Agent picks the only / first
executable in the archive (and warns).

## Build

```bash
pip install pyinstaller
./build.sh
```

This produces `dist-anna/example-multifile-tool-${platform}.tar.gz`
along with its sha256 and size, ready for your `binary_urls` config.

## Register in Nexus

In the Anna Nexus UI for your Executa, expand **Multi-platform Binary
Download URLs**, click **Add Platform**, fill in the URL, then click the
**▾** next to it to expose the advanced fields and paste the sha256 +
entrypoint:

```json
{
  "darwin-arm64": {
    "url": "https://your-cdn.example.com/example-multifile-tool-darwin-arm64.tar.gz",
    "sha256": "<from build.sh>",
    "size": 12345678,
    "entrypoint": "bin/example-multifile-tool",
    "format": "tar.gz"
  },
  "linux-x86_64": { "url": "https://...", "sha256": "..." }
}
```

## Verify locally

```bash
python plugin.py <<< '{"jsonrpc":"2.0","id":1,"method":"describe"}'
python plugin.py <<< '{"jsonrpc":"2.0","id":2,"method":"invoke","params":{"name":"describe_layout"}}'
```

The `describe_layout` tool returns the runtime view of `EXECUTA_HOME`,
`EXECUTA_DATA`, library-path env vars, and whether `lib/` and `data/`
are visible — handy for debugging packaging issues.

## Install on your Agent without uploading anywhere — `distribution_type: local`

For dev iteration you don't need to push to GitHub Releases first. The
**Local** distribution type runs the **exact same v2 install pipeline**
as Binary — extract → `tools/{tool_id}/v{version}/` → atomic `current`
symlink → `bin/{name}` shim — and reads the archive straight off your
Agent host's filesystem.

After `./build.sh`, register the tool in the Anna Nexus UI like this:

| Field             | Value                                                                       |
|-------------------|-----------------------------------------------------------------------------|
| Distribution Type | `local`                                                                     |
| Local Archive Path | `/abs/path/to/dist-anna/example-multifile-tool-${platform}.tar.gz`         |
| Executable Name   | `example-multifile-tool` (optional; derived from archive name otherwise)    |
| Version           | `dev` (or any string — falls into `tools/{tool_id}/vdev/`)                  |

Click **Install** on the Agent and the same multi-file layout described
above is materialised — `lib/`, `data/`, `bin/`, env-var injection,
atomic upgrades, GC, the lot. There is **no difference at runtime**
between an archive installed via `local` vs `binary`; only the source
of the archive (local path vs HTTPS URL) differs.

> [!NOTE]
> sha256 / size verification is skipped for `local` (the archive is on
> your own machine). Switch to `binary` once you're ready to publish.

## Atomic upgrades & rollback

Every install lands in its own `tools/{tool_id}/v{version}/` directory.
The Agent rewrites the `current` symlink as the very last step, so an
in-progress invocation keeps reading the old version. Older versions are
GC'd according to `EXECUTA_KEEP_VERSIONS` (default: keep 2).

If a v2 install ever fails, the Agent retains the previous `current`
target — re-running `install` is safe and idempotent.
