#!/bin/bash
# ============================================================
# Executa Plugin Binary 构建脚本（Python）
# ============================================================
# 自动发现并编译当前目录下所有 Python Executa 插件为独立二进制
#
# 用法:
#   ./build_binary.sh                  # PyInstaller --onefile (默认，编译所有)
#   ./build_binary.sh --nuitka         # Nuitka 编译
#   ./build_binary.sh --test           # 构建后运行协议测试
#   ./build_binary.sh --nuitka --test  # Nuitka 编译 + 测试
#   ./build_binary.sh example_plugin.py                # 只编译指定文件
#   ./build_binary.sh credential_plugin.py --test      # 编译指定文件 + 测试
#
# 自动发现规则:
#   扫描当前目录下所有 *.py，跳过 __*.py / setup.py / conftest.py / test_*.py
#   从每个 .py 中提取 MANIFEST["name"] 作为二进制名称（找不到则用文件名）
#
# 产物:
#   dist/<plugin-name>                 # 每个插件一个单文件可执行程序
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

USE_NUITKA=false
RUN_TEST=false
EXPLICIT_FILES=()

# ── 参数解析 ──────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --nuitka)  USE_NUITKA=true ;;
        --test)    RUN_TEST=true ;;
        --help|-h)
            echo "Usage: $0 [file1.py file2.py ...] [--nuitka] [--test]"
            echo ""
            echo "  file.py    Only compile specified file(s). Omit to compile all."
            echo "  --nuitka   Use Nuitka compiler instead of PyInstaller"
            echo "  --test     Run protocol test after each build"
            exit 0
            ;;
        *.py)
            if [[ -f "$arg" ]]; then
                EXPLICIT_FILES+=("$arg")
            else
                echo -e "${RED}File not found: $arg${NC}"
                exit 1
            fi
            ;;
        *)
            echo -e "${RED}Unknown option: $arg${NC}"
            exit 1
            ;;
    esac
done

# ── 发现插件文件 ──────────────────────────────────────────────
discover_plugins() {
    for f in *.py; do
        [[ ! -f "$f" ]] && continue
        # 跳过非插件文件
        case "$f" in
            __*|setup.py|conftest.py|test_*) continue ;;
        esac
        echo "$f"
    done
}

if [[ ${#EXPLICIT_FILES[@]} -gt 0 ]]; then
    PLUGIN_FILES=("${EXPLICIT_FILES[@]}")
else
    PLUGIN_FILES=()
    while IFS= read -r _f; do
        PLUGIN_FILES+=("$_f")
    done < <(discover_plugins)
fi

if [[ ${#PLUGIN_FILES[@]} -eq 0 ]]; then
    echo -e "${RED}❌ 未发现任何 .py 插件文件${NC}"
    exit 1
fi

# ── 从 .py 文件提取 MANIFEST name（用作二进制名称）────────────
extract_plugin_name() {
    local py_file="$1"
    # 尝试从 Python 中提取 MANIFEST["name"]
    local name
    name=$(python3 -c "
import ast, sys
try:
    tree = ast.parse(open('$py_file').read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'MANIFEST':
                    if isinstance(node.value, ast.Dict):
                        for k, v in zip(node.value.keys, node.value.values):
                            if isinstance(k, ast.Constant) and k.value == 'name':
                                if isinstance(v, ast.Constant):
                                    print(v.value)
                                    sys.exit(0)
except Exception:
    pass
" 2>/dev/null)

    if [[ -n "$name" ]]; then
        echo "$name"
    else
        # 回退：文件名去掉 .py，下划线换连字符
        echo "${py_file%.py}" | tr '_' '-'
    fi
}

# ── 确保编译器可用 ────────────────────────────────────────────
ensure_compiler() {
    if [[ "$USE_NUITKA" == "true" ]]; then
        if ! command -v nuitka &>/dev/null && ! python3 -m nuitka --version &>/dev/null 2>&1; then
            echo -e "${YELLOW}  Nuitka 未安装，正在安装...${NC}"
            pip install nuitka ordered-set
        fi
    else
        if ! command -v pyinstaller &>/dev/null; then
            echo -e "${YELLOW}  PyInstaller 未安装，正在安装...${NC}"
            pip install pyinstaller
        fi
    fi
}

# ── 编译单个插件 ──────────────────────────────────────────────
build_one() {
    local entry_point="$1"
    local plugin_name="$2"

    echo -e "  ${BOLD}${entry_point}${NC} → ${CYAN}dist/${plugin_name}${NC}"

    if [[ "$USE_NUITKA" == "true" ]]; then
        python3 -m nuitka \
            --standalone \
            --onefile \
            --output-filename="${plugin_name}" \
            --output-dir=dist \
            --remove-output \
            --assume-yes-for-downloads \
            --python-flag=no_site \
            --python-flag=no_warnings \
            "${entry_point}"
    else
        pyinstaller \
            --onefile \
            --name "${plugin_name}" \
            --clean \
            --noconfirm \
            --log-level WARN \
            --strip \
            --noupx \
            "${entry_point}"
    fi

    local binary="dist/${plugin_name}"
    if [[ ! -f "$binary" ]]; then
        echo -e "  ${RED}❌ 构建失败：未找到 ${binary}${NC}"
        return 1
    fi

    # macOS: ad-hoc 签名
    if [[ "$(uname -s)" == "Darwin" ]]; then
        codesign --force --sign - "$binary" 2>/dev/null || true
    fi

    local size
    size=$(du -h "$binary" | cut -f1)
    echo -e "  ${GREEN}✅ ${plugin_name}${NC}  (${size}, $(file "$binary" | sed "s|^${binary}: ||" | cut -c1-60))"
    return 0
}

# ── 协议测试（通用：describe / health / error）────────────────
test_one() {
    local binary="$1"
    local plugin_name="$2"
    local pass=0
    local fail=0

    echo -e "  ${CYAN}── 测试 ${plugin_name} ──${NC}"

    # Test 1: describe — 返回包含 name 的 manifest
    local result
    result=$(echo '{"jsonrpc":"2.0","method":"describe","id":1}' | "$binary" 2>/dev/null)
    if echo "$result" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'result' in d and 'name' in d['result'], 'missing result.name'
" 2>/dev/null; then
        echo -e "    ${GREEN}✅ describe${NC}"; ((pass++))
    else
        echo -e "    ${RED}❌ describe: ${result:0:120}${NC}"; ((fail++))
    fi

    # Test 2: health — 返回 status=healthy
    result=$(echo '{"jsonrpc":"2.0","method":"health","id":2}' | "$binary" 2>/dev/null)
    if echo "$result" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('result', {}).get('status') == 'healthy'
" 2>/dev/null; then
        echo -e "    ${GREEN}✅ health${NC}"; ((pass++))
    else
        echo -e "    ${RED}❌ health: ${result:0:120}${NC}"; ((fail++))
    fi

    # Test 3: invoke — 调用第一个工具（从 describe 获取工具名和必需参数）
    result=$(echo '{"jsonrpc":"2.0","method":"describe","id":99}' | "$binary" 2>/dev/null)
    local invoke_test_ok=false
    local tool_name tool_param invoke_result
    tool_name=$(echo "$result" | python3 -c "
import sys, json
d = json.load(sys.stdin)
tools = d.get('result', {}).get('tools', [])
if tools:
    print(tools[0]['name'])
" 2>/dev/null)

    if [[ -n "$tool_name" ]]; then
        # 构建最小参数：为每个 required string 参数填 "test"，integer 填 1
        local invoke_params
        invoke_params=$(echo "$result" | python3 -c "
import sys, json
d = json.load(sys.stdin)
tool = d['result']['tools'][0]
args = {}
for p in tool.get('parameters', []):
    if p.get('required', False):
        t = p.get('type', 'string')
        if t == 'integer': args[p['name']] = 1
        elif t == 'array':  args[p['name']] = ['test']
        else:               args[p['name']] = 'test'
print(json.dumps({'tool': tool['name'], 'arguments': args}))
" 2>/dev/null)

        if [[ -n "$invoke_params" ]]; then
            invoke_result=$(echo "{\"jsonrpc\":\"2.0\",\"method\":\"invoke\",\"params\":${invoke_params},\"id\":3}" | "$binary" 2>/dev/null)
            if echo "$invoke_result" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'result' in d and d['result'].get('success') is True, f'invoke failed: {d}'
" 2>/dev/null; then
                echo -e "    ${GREEN}✅ invoke ${tool_name}${NC}"; ((pass++))
                invoke_test_ok=true
            else
                echo -e "    ${RED}❌ invoke ${tool_name}: ${invoke_result:0:120}${NC}"; ((fail++))
                invoke_test_ok=true  # 已尝试，不重复报告
            fi
        fi
    fi

    if [[ "$invoke_test_ok" == "false" ]]; then
        echo -e "    ${YELLOW}⏭  invoke 跳过（无法自动推断参数）${NC}"
    fi

    # Test 4: unknown method — 应返回 error
    result=$(echo '{"jsonrpc":"2.0","method":"nonexistent","id":4}' | "$binary" 2>/dev/null)
    if echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'error' in d" 2>/dev/null; then
        echo -e "    ${GREEN}✅ error handling${NC}"; ((pass++))
    else
        echo -e "    ${RED}❌ error handling: ${result:0:120}${NC}"; ((fail++))
    fi

    echo -e "    结果: ${GREEN}${pass} passed${NC}, ${RED}${fail} failed${NC}"
    return "$fail"
}

# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

COMPILER_LABEL="PyInstaller"
[[ "$USE_NUITKA" == "true" ]] && COMPILER_LABEL="Nuitka"

echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  Executa Plugin Binary Builder (Python)${NC}"
echo -e "${CYAN}============================================================${NC}"
echo -e "  Platform: $(uname -s) $(uname -m)"
echo -e "  Python:   $(python3 --version 2>&1)"
echo -e "  Compiler: ${COMPILER_LABEL}"
echo -e "  Plugins:  ${#PLUGIN_FILES[@]} file(s)"
echo ""

# 列出待编译文件
for pf in "${PLUGIN_FILES[@]}"; do
    pn=$(extract_plugin_name "$pf")
    echo -e "    • ${pf} → ${BOLD}${pn}${NC}"
done
echo ""

# Step 1: 清理
echo -e "${GREEN}[1/3] 清理旧构建产物...${NC}"
rm -rf build/ __pycache__/ *.spec.bak
mkdir -p dist

# Step 2: 确保编译器
ensure_compiler

# Step 3: 逐个编译
echo -e "${GREEN}[2/3] 编译二进制...${NC}"
BUILT=()
BUILD_FAIL=0

for pf in "${PLUGIN_FILES[@]}"; do
    pn=$(extract_plugin_name "$pf")
    if build_one "$pf" "$pn"; then
        BUILT+=("$pn")
    else
        ((BUILD_FAIL++))
    fi
done

echo ""
echo -e "${GREEN}[3/3] 编译完成：${#BUILT[@]} 成功，${BUILD_FAIL} 失败${NC}"

# Step 4: 协议测试
if [[ "$RUN_TEST" == "true" && ${#BUILT[@]} -gt 0 ]]; then
    echo ""
    echo -e "${CYAN}══ 协议测试 ════════════════════════════════════${NC}"
    TOTAL_FAIL=0
    for pn in "${BUILT[@]}"; do
        test_one "dist/${pn}" "$pn" || ((TOTAL_FAIL++))
    done
    echo ""
    if [[ $TOTAL_FAIL -gt 0 ]]; then
        echo -e "${RED}⚠ ${TOTAL_FAIL} 个插件测试存在失败${NC}"
        exit 1
    else
        echo -e "${GREEN}✅ 所有插件测试通过${NC}"
    fi
elif [[ "$RUN_TEST" == "false" && ${#BUILT[@]} -gt 0 ]]; then
    echo -e "  测试跳过（使用 --test 参数启用）"
fi

# 汇总
if [[ ${#BUILT[@]} -gt 0 ]]; then
    echo ""
    echo -e "${CYAN}── 产物清单 ────────────────────────────────────${NC}"
    for pn in "${BUILT[@]}"; do
        local_size=$(du -h "dist/${pn}" | cut -f1)
        echo -e "  dist/${pn}  (${local_size})"
    done
    echo ""
    echo -e "${CYAN}── 下一步 ──────────────────────────────────────${NC}"
    echo -e "  本地安装:"
    echo -e "    cp dist/* ~/.anna/executa/bin/"
    echo ""
    echo -e "  打包上传:"
    PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
    for pn in "${BUILT[@]}"; do
        echo -e "    cd dist && tar czf ${pn}-${PLATFORM}.tar.gz ${pn}"
    done
    echo ""
fi

[[ $BUILD_FAIL -gt 0 ]] && exit 1
exit 0
