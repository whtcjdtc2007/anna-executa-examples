#!/usr/bin/env bash
# Build the multi-file binary example for the current platform.
#
# Output: ``dist-anna/example-multifile-tool-${PLATFORM}.tar.gz`` with
# the standard Anna multi-file binary layout::
#
#     bin/example-multifile-tool   ← PyInstaller launcher
#     lib/...                      ← bundled Python runtime + .so/.dylib
#     data/greeting.txt            ← bundled data
#     manifest.json                ← declares runtime.binary.entrypoint
#
# Upload the resulting tar.gz somewhere your Agent can fetch from
# (S3, GitHub Releases, your own CDN), then point Nexus at it via
# the ``binary_urls`` field on the Executa record::
#
#     {
#       "darwin-arm64": {
#         "url": "https://your-cdn.example.com/example-multifile-tool-darwin-arm64.tar.gz",
#         "sha256": "<from sha256sum>",
#         "entrypoint": "bin/example-multifile-tool"
#       }
#     }

set -euo pipefail
cd "$(dirname "$0")"

# ---- Optional: clean build artifacts and exit ----
if [ "${1:-}" = "-clean" ] || [ "${1:-}" = "--clean" ] || [ "${1:-}" = "clean" ]; then
    echo ">>> Cleaning build artifacts..."
    rm -rf build dist dist-anna data/greeting.txt
    # Best-effort: drop empty data/ if we created it
    [ -d data ] && rmdir data 2>/dev/null || true
    echo ">>> Clean complete."
    exit 0
fi

# ---- Detect platform key (must match Anna's get_platform_key()) ----
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
    x86_64|amd64) ARCH=x86_64 ;;
    arm64|aarch64) ARCH=arm64 ;;
esac
case "$OS" in
    darwin) PLATFORM="darwin-${ARCH}" ;;
    linux) PLATFORM="linux-${ARCH}" ;;
    *) echo "unsupported OS: $OS" >&2; exit 1 ;;
esac
echo ">>> Building for platform: $PLATFORM"

# ---- Sample bundled data ----
mkdir -p data
echo "hello from a bundled data file" > data/greeting.txt

# ---- Run PyInstaller --onedir ----
if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "pyinstaller not found; install with: pip install pyinstaller" >&2
    exit 1
fi
rm -rf build dist
pyinstaller --noconfirm pyinstaller.spec

# ---- Re-arrange PyInstaller output into Anna layout ----
STAGE="dist-anna/staging-${PLATFORM}"
rm -rf "$STAGE"
mkdir -p "$STAGE/bin" "$STAGE/lib" "$STAGE/data"

# PyInstaller --onedir layout (varies by version; PyInstaller >=6 uses
# `_internal/`):  dist/example-multifile-tool/{example-multifile-tool,
# _internal/...}
SRC="dist/example-multifile-tool"
if [ ! -d "$SRC" ]; then
    echo "PyInstaller did not produce $SRC" >&2
    exit 1
fi

# Move the launcher to bin/
if [ -f "$SRC/example-multifile-tool" ]; then
    mv "$SRC/example-multifile-tool" "$STAGE/bin/"
elif [ -f "$SRC/example-multifile-tool.exe" ]; then
    mv "$SRC/example-multifile-tool.exe" "$STAGE/bin/"
fi
chmod 0755 "$STAGE/bin/"*

# PyInstaller's onedir bootloader hard-codes the relative lookup
# ``dirname(argv[0])/_internal/`` for libpython, frozen modules, and
# Tcl/Tk data. So _internal/ MUST land next to the launcher in bin/ —
# moving it under lib/ would break the very first dlopen and produce
# `Failed to load Python shared library` on launch.
shopt -s dotglob nullglob
if [ -d "$SRC/_internal" ]; then
    mv "$SRC/_internal" "$STAGE/bin/_internal"
fi
# Anything else PyInstaller leaves behind (rare; some codecs/data) is
# moved into lib/ and surfaced through DYLD_LIBRARY_PATH/LD_LIBRARY_PATH
# by the Anna runtime — these are not loaded by the bootloader and are
# safe to relocate.
for f in "$SRC"/*; do
    mv "$f" "$STAGE/lib/"
done
shopt -u dotglob nullglob

# Bundled data
cp data/greeting.txt "$STAGE/data/"

# Manifest at archive root
cp manifest.json "$STAGE/"

# ---- Pack ----
mkdir -p dist-anna
TARBALL="dist-anna/example-multifile-tool-${PLATFORM}.tar.gz"
( cd "$STAGE" && tar czf "../../$TARBALL" . )
SHA=$(shasum -a 256 "$TARBALL" | awk '{print $1}')
SIZE=$(wc -c <"$TARBALL" | tr -d ' ')

echo
echo ">>> Built: $TARBALL"
echo ">>> SHA-256: $SHA"
echo ">>> Size:    $SIZE bytes"
echo
echo "Add this to your Executa binary_urls in Anna Nexus:"
echo
cat <<JSON
"$PLATFORM": {
  "url": "https://YOUR-CDN/example-multifile-tool-${PLATFORM}.tar.gz",
  "sha256": "$SHA",
  "size": $SIZE,
  "entrypoint": "bin/example-multifile-tool",
  "format": "tar.gz"
}
JSON
