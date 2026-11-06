#!/usr/bin/env bash
# build.sh — package the lightweight binary-profile archives (forum #490 ④).
#
# Produces FIVE platform-keyed tar.gz files with byte-identical content
# (pure JS, no native code — server-side content addressing dedupes the
# upload). Node and Chrome are NOT included: the platform provides both.
#
# Usage:  ./build.sh            # builds into ./dist-anna/
set -euo pipefail

cd "$(dirname "$0")"

VERSION="$(node -p "require('./package.json').version")"
NAME="runtime-probe"
PLATFORMS=(linux-x86_64 linux-aarch64 darwin-arm64 darwin-x86_64 windows-x86_64)
STAGE="$(mktemp -d)"
OUT="dist-anna"
trap 'rm -rf "$STAGE"' EXIT

echo "→ staging ${NAME} v${VERSION} (platform Node/Chrome NOT bundled)"

# 1) install production deps into the stage (puppeteer-core only, no Chrome
#    download thanks to package.json "puppeteer": {"skipDownload": true}).
mkdir -p "$STAGE/pkg"
cp -R runtime_probe_plugin.js lib bin package.json "$STAGE/pkg/"
cp archive/manifest.json "$STAGE/pkg/manifest.json"
(cd "$STAGE/pkg" && npm install --omit=dev --no-audit --no-fund --loglevel=error)

# 2) prune node_modules fat: tests, docs, typings, source maps.
find "$STAGE/pkg/node_modules" \
  \( -name "*.md" -o -name "*.markdown" -o -name "*.map" -o -name "*.d.ts" \
     -o -name "LICENSE*" -o -name "NOTICE*" \) -type f -delete 2>/dev/null || true
find "$STAGE/pkg/node_modules" -type d \
  \( -name test -o -name tests -o -name __tests__ -o -name docs -o -name example -o -name examples \) \
  -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$STAGE/pkg/package-lock.json"
chmod +x "$STAGE/pkg/bin/runtime-probe"

# 3) one tar.gz per platform key (identical bytes — content-addressed dedupe).
mkdir -p "$OUT"
FIRST=""
for platform in "${PLATFORMS[@]}"; do
  TARBALL="$OUT/${NAME}-${VERSION}-${platform}.tar.gz"
  if [ -z "$FIRST" ]; then
    # Normalise metadata so re-runs are reproducible-ish.
    (cd "$STAGE/pkg" && tar czf "$OLDPWD/$TARBALL" .)
    FIRST="$TARBALL"
  else
    cp "$FIRST" "$TARBALL"
  fi
  shasum -a 256 "$TARBALL" | awk '{print $1}' > "$TARBALL.sha256"
done

# 4) size report — the whole point of forum #490.
PKG_BYTES=$(stat -f%z "$FIRST" 2>/dev/null || stat -c%s "$FIRST")
PKG_MIB=$(echo "scale=1; $PKG_BYTES / 1048576" | bc)
UNPACKED_KB=$(du -sk "$STAGE/pkg" | cut -f1)
UNPACKED_MIB=$(echo "scale=1; $UNPACKED_KB / 1024" | bc)
echo
echo "== Size comparison (forum #490 PPT engine baseline) =="
echo "   bundled-everything app :  316.3 MiB compressed /  849.9 MiB unpacked"
echo "   this demo (per archive): ${PKG_MIB} MiB compressed / ${UNPACKED_MIB} MiB unpacked"
echo
echo "✓ wrote $OUT/:"
ls -lh "$OUT" | sed 's/^/   /'
