#!/bin/bash
# ============================================================
# Executa Plugin Binary 构建脚本（Node.js）
# ============================================================
# 将 Node.js Executa 插件编译为独立二进制
#
# 用法:
#   ./build_binary.sh              # 使用 pkg 构建当前平台（默认）
#   ./build_binary.sh --sea        # 使用 Node.js SEA (Single Executable Application)
#   ./build_binary.sh --all        # 构建所有平台（仅 pkg）
#   ./build_binary.sh --test       # 构建后运行协议测试
#
# 产物:
#   dist/example-node-tool         # 单文件可执行程序
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色
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

# ── 参数解析 ──────────────────────────────────────────────────
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

# ── 构建信息 ──────────────────────────────────────────────────
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

# ── 清理 ──────────────────────────────────────────────────────
echo -e "${GREEN}[1/4] 清理旧构建产物...${NC}"
rm -rf dist/ sea-prep/ *.blob

# ── 编译 ──────────────────────────────────────────────────────
echo -e "${GREEN}[2/4] 编译二进制...${NC}"
mkdir -p dist

if [[ "$USE_SEA" == "true" ]]; then
    # ── Node.js SEA ──
    NODE_VERSION=$(node -e "console.log(process.version.slice(1).split('.')[0])")
    if [[ "$NODE_VERSION" -lt 20 ]]; then
        echo -e "${RED}Node.js SEA 需要 Node.js 20+，当前: $(node --version)${NC}"
        echo -e "${YELLOW}回退到 pkg...${NC}"
        USE_SEA=false
    fi
fi

if [[ "$USE_SEA" == "true" ]]; then
    # Node.js SEA 构建流程
    mkdir -p sea-prep

    # 1. 生成 SEA 配置
    cat > sea-prep/sea-config.json <<EOF
{
  "main": "../${ENTRY_POINT}",
  "output": "sea-prep/sea.blob",
  "disableExperimentalSEAWarning": true
}
EOF

    # 2. 生成 blob
    node --experimental-sea-config sea-prep/sea-config.json

    # 3. 复制 node 可执行文件
    NODE_PATH=$(which node)
    cp "$NODE_PATH" "dist/${PLUGIN_NAME}"

    # 4. macOS: 移除签名（注入前需要）
    if [[ "$(uname -s)" == "Darwin" ]]; then
        codesign --remove-signature "dist/${PLUGIN_NAME}" 2>/dev/null || true
    fi

    # 5. 注入 blob
    npx --yes postject "dist/${PLUGIN_NAME}" NODE_SEA_BLOB sea-prep/sea.blob \
        --sentinel-fuse NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2

    # 6. macOS: 重新签名
    if [[ "$(uname -s)" == "Darwin" ]]; then
        codesign --force --sign - "dist/${PLUGIN_NAME}" 2>/dev/null || true
    fi

    rm -rf sea-prep
else
    # pkg 构建
    if ! command -v pkg &>/dev/null && ! npx pkg --version &>/dev/null 2>&1; then
        echo -e "${YELLOW}  安装 pkg...${NC}"
        npm install -g pkg 2>/dev/null || npx pkg --version
    fi

    if [[ "$BUILD_ALL" == "true" ]]; then
        # 多平台构建
        echo -e "  构建 macOS ARM64..."
        npx pkg "${ENTRY_POINT}" --target node18-macos-arm64 --output "dist/${PLUGIN_NAME}-darwin-arm64"
        echo -e "  构建 macOS x86_64..."
        npx pkg "${ENTRY_POINT}" --target node18-macos-x64 --output "dist/${PLUGIN_NAME}-darwin-x86_64"
        echo -e "  构建 Linux x86_64..."
        npx pkg "${ENTRY_POINT}" --target node18-linux-x64 --output "dist/${PLUGIN_NAME}-linux-x86_64"
        echo -e "  构建 Linux ARM64..."
        npx pkg "${ENTRY_POINT}" --target node18-linux-arm64 --output "dist/${PLUGIN_NAME}-linux-aarch64"
        echo -e "  构建 Windows x86_64..."
        npx pkg "${ENTRY_POINT}" --target node18-win-x64 --output "dist/${PLUGIN_NAME}-windows-x86_64.exe"
    else
        # 当前平台构建
        npx pkg "${ENTRY_POINT}" --output "dist/${PLUGIN_NAME}"
    fi
fi

# ── 验证产物 ──────────────────────────────────────────────────
BINARY="dist/${PLUGIN_NAME}"

if [[ "$BUILD_ALL" == "true" ]]; then
    echo ""
    echo -e "${GREEN}[3/4] 多平台构建完成！${NC}"
    ls -lh dist/
    BINARY="dist/${PLUGIN_NAME}-$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
    # 标准化
    [[ "$(uname -m)" == "x86_64" || "$(uname -m)" == "amd64" ]] && BINARY="dist/${PLUGIN_NAME}-$(uname -s | tr '[:upper:]' '[:lower:]')-x86_64"
    [[ "$(uname -m)" == "arm64" ]] && BINARY="dist/${PLUGIN_NAME}-$(uname -s | tr '[:upper:]' '[:lower:]')-arm64"
fi

if [[ -f "$BINARY" ]]; then
    SIZE=$(du -h "$BINARY" | cut -f1)
    echo ""
    echo -e "${GREEN}[3/4] 构建成功！${NC}"
    echo -e "  产物: ${BINARY}"
    echo -e "  大小: ${SIZE}"
fi

# ── 协议测试（可选）────────────────────────────────────────────
echo -e "${GREEN}[4/4] 协议测试${NC}"

if [[ "$RUN_TEST" == "true" && -f "$BINARY" ]]; then
    echo ""
    echo -e "${CYAN}── 协议测试 ──────────────────────────────────${NC}"

    PASS=0
    FAIL=0

    # Test: describe
    echo -e "  [describe] 测试自描述清单..."
    RESULT=$(echo '{"jsonrpc":"2.0","method":"describe","id":1}' | "$BINARY" 2>/dev/null)
    if echo "$RESULT" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{const r=JSON.parse(d);process.exit(r.result&&r.result.name==='example-node-tool'?0:1)})" 2>/dev/null; then
        echo -e "  ${GREEN}✅ describe 通过${NC}"; ((PASS++))
    else
        echo -e "  ${RED}❌ describe 失败${NC}"; ((FAIL++))
    fi

    # Test: invoke
    echo -e "  [invoke] 测试工具调用..."
    RESULT=$(echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"base64_encode","arguments":{"text":"hello"}},"id":2}' | "$BINARY" 2>/dev/null)
    if echo "$RESULT" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{const r=JSON.parse(d);process.exit(r.result&&r.result.data.encoded==='aGVsbG8='?0:1)})" 2>/dev/null; then
        echo -e "  ${GREEN}✅ invoke 通过${NC}"; ((PASS++))
    else
        echo -e "  ${RED}❌ invoke 失败${NC}"; ((FAIL++))
    fi

    # Test: health
    echo -e "  [health] 测试健康检查..."
    RESULT=$(echo '{"jsonrpc":"2.0","method":"health","id":3}' | "$BINARY" 2>/dev/null)
    if echo "$RESULT" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{const r=JSON.parse(d);process.exit(r.result&&r.result.status==='healthy'?0:1)})" 2>/dev/null; then
        echo -e "  ${GREEN}✅ health 通过${NC}"; ((PASS++))
    else
        echo -e "  ${RED}❌ health 失败${NC}"; ((FAIL++))
    fi

    echo ""
    echo -e "  结果: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"
else
    echo -e "  跳过（使用 --test 参数启用）"
fi

echo ""
echo -e "${CYAN}── 下一步 ────────────────────────────────────${NC}"
echo -e "  本地安装到 Executa:"
echo -e "    cp dist/${PLUGIN_NAME} ~/.anna/executa/bin/"
echo ""
