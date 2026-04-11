#!/bin/bash
# ============================================================
# Executa Plugin Binary Build Script (Go)
# ============================================================
# Usage:
#   ./build.sh                  # Build for current platform
#   ./build.sh --all            # Build for all standard platforms
#   ./build.sh --test           # Build + protocol tests
#   ./build.sh --package        # Build + package
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PLUGIN_NAME="example-go-tool"
BUILD_ALL=false
RUN_TEST=false
PACKAGE=false

for arg in "$@"; do
    case "$arg" in
        --all)     BUILD_ALL=true ;;
        --test)    RUN_TEST=true ;;
        --package) PACKAGE=true; BUILD_ALL=true ;;
        --help|-h)
            echo "Usage: $0 [--all] [--test] [--package]"
            exit 0
            ;;
    esac
done

echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  Executa Plugin Binary Builder (Go)${NC}"
echo -e "${CYAN}============================================================${NC}"
echo -e "  Plugin:   ${PLUGIN_NAME}"
echo -e "  Platform: $(uname -s) $(uname -m)"
echo -e "  Go:       $(go version 2>/dev/null || echo 'not installed')"
echo ""

rm -rf dist/
mkdir -p dist

if [[ "$BUILD_ALL" == "true" ]]; then
    # Anna KNOWN_PLATFORMS
    declare -A PLATFORMS=(
        ["darwin-arm64"]="darwin arm64"
        ["darwin-x86_64"]="darwin amd64"
        ["linux-x86_64"]="linux amd64"
        ["linux-aarch64"]="linux arm64"
        ["linux-armv7l"]="linux arm"
        ["windows-x86_64"]="windows amd64"
        ["windows-arm64"]="windows arm64"
    )

    for plat in "${!PLATFORMS[@]}"; do
        read -r goos goarch <<< "${PLATFORMS[$plat]}"
        suffix=""
        [[ "$goos" == "windows" ]] && suffix=".exe"
        echo -e "  Building ${plat}..."
        GOOS=$goos GOARCH=$goarch go build -ldflags="-s -w" -o "dist/${PLUGIN_NAME}-${plat}${suffix}" .
    done
    echo ""
    echo -e "${GREEN}All platforms built!${NC}"
    ls -lh dist/
else
    go build -ldflags="-s -w" -o "dist/${PLUGIN_NAME}" .
    SIZE=$(du -h "dist/${PLUGIN_NAME}" | cut -f1)
    echo -e "${GREEN}Build succeeded!${NC} dist/${PLUGIN_NAME} (${SIZE})"
fi

# ── Package ────────────────────────────────────────────────────
if [[ "$PACKAGE" == "true" ]]; then
    echo ""
    echo -e "${GREEN}Packaging...${NC}"
    mkdir -p dist/packages
    for f in dist/${PLUGIN_NAME}-*; do
        base=$(basename "$f")
        plat="${base#${PLUGIN_NAME}-}"
        plat="${plat%.exe}"
        if [[ "$f" == *.exe ]]; then
            (cd dist && zip -j "packages/${PLUGIN_NAME}-${plat}.zip" "$base")
        else
            (cd dist && tar czf "packages/${PLUGIN_NAME}-${plat}.tar.gz" "$base")
        fi
    done
    echo ""
    ls -lh dist/packages/
fi

# ── Tests ─────────────────────────────────────────────────────
if [[ "$RUN_TEST" == "true" ]]; then
    BINARY="dist/${PLUGIN_NAME}"
    [[ ! -f "$BINARY" ]] && BINARY=$(ls dist/${PLUGIN_NAME}-* 2>/dev/null | head -1)

    if [[ -f "$BINARY" && -x "$BINARY" ]]; then
        echo ""
        echo -e "${CYAN}── Protocol Tests ────────────────────────────${NC}"

        echo -e "  [describe]..."
        RESULT=$(echo '{"jsonrpc":"2.0","method":"describe","id":1}' | "$BINARY" 2>/dev/null)
        if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['name']=='example-go-tool'" 2>/dev/null; then
            echo -e "  ${GREEN}✅ describe passed${NC}"
        else
            echo -e "  ${RED}❌ describe failed${NC}"
        fi

        echo -e "  [invoke]..."
        RESULT=$(echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"system_info","arguments":{}},"id":2}' | "$BINARY" 2>/dev/null)
        if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['success']==True" 2>/dev/null; then
            echo -e "  ${GREEN}✅ invoke passed${NC}"
        else
            echo -e "  ${RED}❌ invoke failed${NC}"
        fi

        echo -e "  [health]..."
        RESULT=$(echo '{"jsonrpc":"2.0","method":"health","id":3}' | "$BINARY" 2>/dev/null)
        if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['status']=='healthy'" 2>/dev/null; then
            echo -e "  ${GREEN}✅ health passed${NC}"
        else
            echo -e "  ${RED}❌ health failed${NC}"
        fi
    else
        echo -e "${YELLOW}No executable binary found${NC}"
    fi
fi

echo ""
echo -e "${CYAN}── Next Steps ──────────────────────────────────${NC}"
echo -e "  cp dist/${PLUGIN_NAME} ~/.anna/executa/bin/"
echo ""
