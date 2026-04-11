#!/bin/bash
# ============================================================
# Executa Plugin Binary Build Script (Node.js)
# ============================================================
# Compiles a Node.js Executa plugin into a standalone binary.
#
# Usage:
#   ./build_binary.sh              # Build for current platform using pkg (default)
#   ./build_binary.sh --sea        # Use Node.js SEA (Single Executable Application)
#   ./build_binary.sh --all        # Build for all platforms (pkg only)
#   ./build_binary.sh --test       # Run protocol tests after build
#
# Output:
#   dist/example-node-tool         # Single-file executable
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PLUGIN_NAME="example-node-tool"
ENTRY_POINT="example_plugin.js"
USE_SEA=false
BUILD_ALL=false
RUN_TEST=false

# ── Argument parsing ──────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --sea)     USE_SEA=true ;;
        --all)     BUILD_ALL=true ;;
        --test)    RUN_TEST=true ;;
        --help|-h)
            echo "Usage: $0 [--sea] [--all] [--test]"
            echo "  --sea    Use Node.js SEA instead of pkg"
            echo "  --all    Build for all platforms (pkg only)"
            echo "  --test   Run protocol test after build"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $arg${NC}"
            exit 1
            ;;
    esac
done

# ── Build info ────────────────────────────────────────────────
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  Executa Plugin Binary Builder (Node.js)${NC}"
echo -e "${CYAN}============================================================${NC}"
echo -e "  Plugin:   ${PLUGIN_NAME}"
echo -e "  Entry:    ${ENTRY_POINT}"
echo -e "  Platform: $(uname -s) $(uname -m)"
echo -e "  Node.js:  $(node --version)"

if [[ "$USE_SEA" == "true" ]]; then
    echo -e "  Method:   Node.js SEA (Single Executable Application)"
else
    echo -e "  Method:   pkg"
fi
echo ""

# ── Clean ─────────────────────────────────────────────────────
echo -e "${GREEN}[1/4] Cleaning old build artifacts...${NC}"
rm -rf dist/ sea-prep/ *.blob

# ── Compile ───────────────────────────────────────────────────
echo -e "${GREEN}[2/4] Compiling binaries...${NC}"
mkdir -p dist

if [[ "$USE_SEA" == "true" ]]; then
    # ── Node.js SEA ──
    NODE_VERSION=$(node -e "console.log(process.version.slice(1).split('.')[0])")
    if [[ "$NODE_VERSION" -lt 20 ]]; then
        echo -e "${RED}Node.js SEA requires Node.js 20+, current: $(node --version)${NC}"
        echo -e "${YELLOW}Falling back to pkg...${NC}"
        USE_SEA=false
    fi
fi

if [[ "$USE_SEA" == "true" ]]; then
    # Node.js SEA build process
    mkdir -p sea-prep

    # 1. Generate SEA config
    cat > sea-prep/sea-config.json <<EOF
{
  "main": "../${ENTRY_POINT}",
  "output": "sea-prep/sea.blob",
  "disableExperimentalSEAWarning": true
}
EOF

    # 2. Generate blob
    node --experimental-sea-config sea-prep/sea-config.json

    # 3. Copy node executable
    NODE_PATH=$(which node)
    cp "$NODE_PATH" "dist/${PLUGIN_NAME}"

    # 4. macOS: Remove signature (required before injection)
    if [[ "$(uname -s)" == "Darwin" ]]; then
        codesign --remove-signature "dist/${PLUGIN_NAME}" 2>/dev/null || true
    fi

    # 5. Inject blob
    npx --yes postject "dist/${PLUGIN_NAME}" NODE_SEA_BLOB sea-prep/sea.blob \
        --sentinel-fuse NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2

    # 6. macOS: Re-sign
    if [[ "$(uname -s)" == "Darwin" ]]; then
        codesign --force --sign - "dist/${PLUGIN_NAME}" 2>/dev/null || true
    fi

    rm -rf sea-prep
else
    # pkg build
    if ! command -v pkg &>/dev/null && ! npx pkg --version &>/dev/null 2>&1; then
        echo -e "${YELLOW}  Installing pkg...${NC}"
        npm install -g pkg 2>/dev/null || npx pkg --version
    fi

    if [[ "$BUILD_ALL" == "true" ]]; then
        # Multi-platform build
        echo -e "  Building macOS ARM64..."
        npx pkg "${ENTRY_POINT}" --target node18-macos-arm64 --output "dist/${PLUGIN_NAME}-darwin-arm64"
        echo -e "  Building macOS x86_64..."
        npx pkg "${ENTRY_POINT}" --target node18-macos-x64 --output "dist/${PLUGIN_NAME}-darwin-x86_64"
        echo -e "  Building Linux x86_64..."
        npx pkg "${ENTRY_POINT}" --target node18-linux-x64 --output "dist/${PLUGIN_NAME}-linux-x86_64"
        echo -e "  Building Linux ARM64..."
        npx pkg "${ENTRY_POINT}" --target node18-linux-arm64 --output "dist/${PLUGIN_NAME}-linux-aarch64"
        echo -e "  Building Windows x86_64..."
        npx pkg "${ENTRY_POINT}" --target node18-win-x64 --output "dist/${PLUGIN_NAME}-windows-x86_64.exe"
    else
        # Current platform build
        npx pkg "${ENTRY_POINT}" --output "dist/${PLUGIN_NAME}"
    fi
fi

# ── Verify artifacts ──────────────────────────────────────────
BINARY="dist/${PLUGIN_NAME}"

if [[ "$BUILD_ALL" == "true" ]]; then
    echo ""
    echo -e "${GREEN}[3/4] Multi-platform build complete!${NC}"
    ls -lh dist/
    BINARY="dist/${PLUGIN_NAME}-$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
    # Normalize
    [[ "$(uname -m)" == "x86_64" || "$(uname -m)" == "amd64" ]] && BINARY="dist/${PLUGIN_NAME}-$(uname -s | tr '[:upper:]' '[:lower:]')-x86_64"
    [[ "$(uname -m)" == "arm64" ]] && BINARY="dist/${PLUGIN_NAME}-$(uname -s | tr '[:upper:]' '[:lower:]')-arm64"
fi

if [[ -f "$BINARY" ]]; then
    SIZE=$(du -h "$BINARY" | cut -f1)
    echo ""
    echo -e "${GREEN}[3/4] Build succeeded!${NC}"
    echo -e "  Artifact: ${BINARY}"
    echo -e "  Size: ${SIZE}"
fi

# ── Protocol tests (optional) ─────────────────────────────────
echo -e "${GREEN}[4/4] Protocol Tests${NC}"

if [[ "$RUN_TEST" == "true" && -f "$BINARY" ]]; then
    echo ""
    echo -e "${CYAN}── Protocol Tests ────────────────────────────${NC}"

    PASS=0
    FAIL=0

    # Test: describe
    echo -e "  [describe] Testing self-describe manifest..."
    RESULT=$(echo '{"jsonrpc":"2.0","method":"describe","id":1}' | "$BINARY" 2>/dev/null)
    if echo "$RESULT" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{const r=JSON.parse(d);process.exit(r.result&&r.result.name==='example-node-tool'?0:1)})" 2>/dev/null; then
        echo -e "  ${GREEN}✅ describe passed${NC}"; ((PASS++))
    else
        echo -e "  ${RED}❌ describe failed${NC}"; ((FAIL++))
    fi

    # Test: invoke
    echo -e "  [invoke] Testing tool invocation..."
    RESULT=$(echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"base64_encode","arguments":{"text":"hello"}},"id":2}' | "$BINARY" 2>/dev/null)
    if echo "$RESULT" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{const r=JSON.parse(d);process.exit(r.result&&r.result.data.encoded==='aGVsbG8='?0:1)})" 2>/dev/null; then
        echo -e "  ${GREEN}✅ invoke passed${NC}"; ((PASS++))
    else
        echo -e "  ${RED}❌ invoke failed${NC}"; ((FAIL++))
    fi

    # Test: health
    echo -e "  [health] Testing health check..."
    RESULT=$(echo '{"jsonrpc":"2.0","method":"health","id":3}' | "$BINARY" 2>/dev/null)
    if echo "$RESULT" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{const r=JSON.parse(d);process.exit(r.result&&r.result.status==='healthy'?0:1)})" 2>/dev/null; then
        echo -e "  ${GREEN}✅ health passed${NC}"; ((PASS++))
    else
        echo -e "  ${RED}❌ health failed${NC}"; ((FAIL++))
    fi

    echo ""
    echo -e "  Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"
else
    echo -e "  Skipped (enable with --test flag)"
fi

echo ""
echo -e "${CYAN}── Next Steps ──────────────────────────────────${NC}"
echo -e "  Install locally to Executa:"
echo -e "    cp dist/${PLUGIN_NAME} ~/.anna/executa/bin/"
echo ""
