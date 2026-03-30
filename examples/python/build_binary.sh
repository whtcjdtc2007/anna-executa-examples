#!/bin/bash
# ============================================================
# Executa Plugin Binary 构建脚本（Python）
# ============================================================
# 将 Python Executa 插件编译为独立二进制可执行文件
#
# 用法:
#   ./build_binary.sh                  # PyInstaller --onefile (默认)
#   ./build_binary.sh --nuitka         # Nuitka 编译
#   ./build_binary.sh --test           # 构建后运行协议测试
#   ./build_binary.sh --nuitka --test  # Nuitka 编译 + 测试
#
# 产物:
#   dist/example-text-tool             # 单文件可执行程序
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

PLUGIN_NAME="example-text-tool"
ENTRY_POINT="example_plugin.py"
USE_NUITKA=false
RUN_TEST=false

# ── 参数解析 ──────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --nuitka)  USE_NUITKA=true ;;
        --test)    RUN_TEST=true ;;
        --help|-h)
            echo "Usage: $0 [--nuitka] [--test]"
            echo "  --nuitka   Use Nuitka compiler instead of PyInstaller"
            echo "  --test     Run protocol test after build"
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
echo -e "${CYAN}  Executa Plugin Binary Builder (Python)${NC}"
echo -e "${CYAN}============================================================${NC}"
echo -e "  Plugin:   ${PLUGIN_NAME}"
echo -e "  Entry:    ${ENTRY_POINT}"
echo -e "  Platform: $(uname -s) $(uname -m)"
echo -e "  Python:   $(python3 --version 2>&1)"

if [[ "$USE_NUITKA" == "true" ]]; then
    echo -e "  Compiler: Nuitka"
else
    echo -e "  Compiler: PyInstaller"
fi
echo ""

# ── 清理旧构建 ───────────────────────────────────────────────
echo -e "${GREEN}[1/4] 清理旧构建产物...${NC}"
rm -rf build/ dist/ __pycache__/ *.spec.bak

# ── 编译 ──────────────────────────────────────────────────────
echo -e "${GREEN}[2/4] 编译二进制...${NC}"

if [[ "$USE_NUITKA" == "true" ]]; then
    if ! command -v nuitka &>/dev/null && ! python3 -m nuitka --version &>/dev/null; then
        echo -e "${YELLOW}  Nuitka 未安装，正在安装...${NC}"
        pip install nuitka ordered-set
    fi

    python3 -m nuitka \
        --standalone \
        --onefile \
        --output-filename="${PLUGIN_NAME}" \
        --output-dir=dist \
        --remove-output \
        --assume-yes-for-downloads \
        --python-flag=no_site \
        --python-flag=no_warnings \
        "${ENTRY_POINT}"
else
    if ! command -v pyinstaller &>/dev/null; then
        echo -e "${YELLOW}  PyInstaller 未安装，正在安装...${NC}"
        pip install pyinstaller
    fi

    pyinstaller \
        --onefile \
        --name "${PLUGIN_NAME}" \
        --clean \
        --noconfirm \
        --log-level WARN \
        --strip \
        --noupx \
        "${ENTRY_POINT}"
fi

# ── 验证产物 ──────────────────────────────────────────────────
BINARY="dist/${PLUGIN_NAME}"

if [[ ! -f "$BINARY" ]]; then
    echo -e "${RED}❌ 构建失败：未找到 ${BINARY}${NC}"
    exit 1
fi

SIZE=$(du -h "$BINARY" | cut -f1)
echo ""
echo -e "${GREEN}[3/4] 构建成功！${NC}"
echo -e "  产物: ${BINARY}"
echo -e "  大小: ${SIZE}"
echo -e "  类型: $(file "$BINARY" | sed "s|^${BINARY}: ||")"

# macOS: ad-hoc 签名
if [[ "$(uname -s)" == "Darwin" ]]; then
    echo -e "  签名: ad-hoc codesign..."
    codesign --force --sign - "$BINARY" 2>/dev/null || true
fi

# ── 协议测试（可选）────────────────────────────────────────────
echo -e "${GREEN}[4/4] 协议测试${NC}"

if [[ "$RUN_TEST" == "true" ]]; then
    echo ""
    echo -e "${CYAN}── 协议测试 ──────────────────────────────────${NC}"

    PASS=0
    FAIL=0

    # Test 1: describe
    echo -e "  [describe] 测试自描述清单..."
    RESULT=$(echo '{"jsonrpc":"2.0","method":"describe","id":1}' | "$BINARY" 2>/dev/null)
    if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['name']=='example-text-tool'" 2>/dev/null; then
        echo -e "  ${GREEN}✅ describe 通过${NC}"; ((PASS++))
    else
        echo -e "  ${RED}❌ describe 失败: ${RESULT}${NC}"; ((FAIL++))
    fi

    # Test 2: invoke word_count
    echo -e "  [invoke] 测试工具调用..."
    RESULT=$(echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"word_count","arguments":{"text":"hello world"}},"id":2}' | "$BINARY" 2>/dev/null)
    if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['data']['words']==2" 2>/dev/null; then
        echo -e "  ${GREEN}✅ invoke 通过${NC}"; ((PASS++))
    else
        echo -e "  ${RED}❌ invoke 失败: ${RESULT}${NC}"; ((FAIL++))
    fi

    # Test 3: health
    echo -e "  [health] 测试健康检查..."
    RESULT=$(echo '{"jsonrpc":"2.0","method":"health","id":3}' | "$BINARY" 2>/dev/null)
    if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['status']=='healthy'" 2>/dev/null; then
        echo -e "  ${GREEN}✅ health 通过${NC}"; ((PASS++))
    else
        echo -e "  ${RED}❌ health 失败: ${RESULT}${NC}"; ((FAIL++))
    fi

    # Test 4: unknown method
    echo -e "  [error] 测试错误处理..."
    RESULT=$(echo '{"jsonrpc":"2.0","method":"nonexistent","id":4}' | "$BINARY" 2>/dev/null)
    if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'error' in d" 2>/dev/null; then
        echo -e "  ${GREEN}✅ error handling 通过${NC}"; ((PASS++))
    else
        echo -e "  ${RED}❌ error handling 失败: ${RESULT}${NC}"; ((FAIL++))
    fi

    echo ""
    echo -e "  结果: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"

    if [[ $FAIL -gt 0 ]]; then
        exit 1
    fi
else
    echo -e "  跳过（使用 --test 参数启用）"
fi

# ── 安装提示 ──────────────────────────────────────────────────
echo ""
echo -e "${CYAN}── 下一步 ────────────────────────────────────${NC}"
echo -e "  本地安装到 Executa:"
echo -e "    cp ${BINARY} ~/.anna/executa/bin/"
echo ""
echo -e "  打包上传:"
echo -e "    cd dist && tar czf ${PLUGIN_NAME}-$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m).tar.gz ${PLUGIN_NAME}"
echo ""
