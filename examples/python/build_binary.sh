#!/bin/bash
# ============================================================
# Executa Plugin Binary Build Script (Python)
# ============================================================
# Builds Python Executa plugins into standalone binaries.
#
# Layout: the script lives at the root of `examples/python/`. Each
# example is a self-contained subdirectory with its own pyproject.toml
# and at least one *.py plugin file (e.g. basic-tool/, credential-tool/,
# google-oauth-tool/, sampling-summarizer/).
#
# Usage (from examples/python/ — builds ALL examples):
#   ./build_binary.sh                  # PyInstaller --onefile (default)
#   ./build_binary.sh --nuitka         # Nuitka compilation
#   ./build_binary.sh --test           # Run protocol tests after build
#   ./build_binary.sh basic-tool       # Build a single subdirectory
#   ./build_binary.sh basic-tool credential-tool --test
#
# Usage (from inside a subdirectory — builds just that one):
#   cd basic-tool && ../build_binary.sh
#   cd basic-tool && ../build_binary.sh --test
#
# Auto-discovery rules (per subdirectory):
#   Scans all *.py in the directory, skipping __*.py / setup.py /
#   conftest.py / test_*.py.
#   Extracts MANIFEST["name"] from each .py as the binary name (falls
#   back to filename if not found).
#
# Output:
#   <subdir>/dist/<plugin-name>        # One single-file executable per plugin
# ============================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Multi-subdir dispatch ─────────────────────────────────────
# If invoked from the root (or any non-plugin dir) without an explicit
# subdirectory, iterate every plugin subdirectory and recurse into each.
# A "plugin subdirectory" has both a pyproject.toml and at least one *.py.

is_plugin_dir() {
    local d="$1"
    [[ -f "$d/pyproject.toml" ]] || return 1
    compgen -G "$d/*.py" >/dev/null
}

# Split args into "subdirs to build" vs "flags to forward".
SUBDIR_ARGS=()
FORWARD_ARGS=()
for arg in "$@"; do
    if [[ -d "$ROOT_DIR/$arg" ]] && is_plugin_dir "$ROOT_DIR/$arg"; then
        SUBDIR_ARGS+=("$arg")
    else
        FORWARD_ARGS+=("$arg")
    fi
done

# Decide whether we are in "dispatch" mode or "build current dir" mode.
if is_plugin_dir "$PWD"; then
    : # Run as-is in the current directory below.
elif [[ ${#SUBDIR_ARGS[@]} -gt 0 ]]; then
    rc=0
    for sd in "${SUBDIR_ARGS[@]}"; do
        echo
        echo "══════════════════════════════════════════════════════"
        echo "  → $sd"
        echo "══════════════════════════════════════════════════════"
        ( cd "$ROOT_DIR/$sd" && bash "$ROOT_DIR/build_binary.sh" "${FORWARD_ARGS[@]}" ) || rc=$?
    done
    exit $rc
else
    # Iterate every plugin subdirectory of ROOT_DIR.
    found=0
    rc=0
    for d in "$ROOT_DIR"/*/; do
        d="${d%/}"
        if is_plugin_dir "$d"; then
            found=1
            echo
            echo "══════════════════════════════════════════════════════"
            echo "  → ${d##*/}"
            echo "══════════════════════════════════════════════════════"
            ( cd "$d" && bash "$ROOT_DIR/build_binary.sh" "${FORWARD_ARGS[@]}" ) || rc=$?
        fi
    done
    if [[ $found -eq 0 ]]; then
        echo "No plugin subdirectories (containing both pyproject.toml and *.py) found under $ROOT_DIR" >&2
        exit 1
    fi
    exit $rc
fi

# Below this line we are inside a single plugin subdirectory; remaining
# args are flags only (subdir args were consumed by the dispatcher).
set -- "${FORWARD_ARGS[@]}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

USE_NUITKA=false
RUN_TEST=false
EXPLICIT_FILES=()

# ── Argument parsing ──────────────────────────────────────────
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

# ── Discover plugin files ─────────────────────────────────────
discover_plugins() {
    for f in *.py; do
        [[ ! -f "$f" ]] && continue
        # Skip non-plugin files
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
    echo -e "${RED}❌ No .py plugin files found${NC}"
    exit 1
fi

# ── Extract MANIFEST name from .py file (used as binary name) ─
extract_plugin_name() {
    local py_file="$1"
    # Try to extract MANIFEST["name"] from Python source
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
        # Fallback: strip .py extension, replace underscores with hyphens
        echo "${py_file%.py}" | tr '_' '-'
    fi
}

# ── Ensure compiler is available ───────────────────────────────
ensure_compiler() {
    if [[ "$USE_NUITKA" == "true" ]]; then
        if ! command -v nuitka &>/dev/null && ! python3 -m nuitka --version &>/dev/null 2>&1; then
            echo -e "${YELLOW}  Nuitka not installed, installing...${NC}"
            pip install nuitka ordered-set
        fi
    else
        if ! command -v pyinstaller &>/dev/null; then
            echo -e "${YELLOW}  PyInstaller not installed, installing...${NC}"
            pip install pyinstaller
        fi
    fi
}

# ── Build a single plugin ─────────────────────────────────────
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
        echo -e "  ${RED}❌ Build failed: ${binary} not found${NC}"
        return 1
    fi

    # macOS: ad-hoc signing
    if [[ "$(uname -s)" == "Darwin" ]]; then
        codesign --force --sign - "$binary" 2>/dev/null || true
    fi

    local size
    size=$(du -h "$binary" | cut -f1)
    echo -e "  ${GREEN}✅ ${plugin_name}${NC}  (${size}, $(file "$binary" | sed "s|^${binary}: ||" | cut -c1-60))"
    return 0
}

# ── Protocol tests (generic: describe / health / error) ───────
test_one() {
    local binary="$1"
    local plugin_name="$2"
    local pass=0
    local fail=0

    echo -e "  ${CYAN}── Testing ${plugin_name} ──${NC}"

    # Test 1: describe — returns a manifest containing name
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

    # Test 2: health — returns status=healthy
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

    # Test 3: invoke — calls the first tool (gets tool name and required params from describe)
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
        # Build minimal params: fill "test" for each required string param, 1 for integer
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
                invoke_test_ok=true  # Already attempted, don't report again
            fi
        fi
    fi

    if [[ "$invoke_test_ok" == "false" ]]; then
        echo -e "    ${YELLOW}⏭  invoke skipped (unable to auto-infer parameters)${NC}"
    fi

    # Test 4: unknown method — should return error
    result=$(echo '{"jsonrpc":"2.0","method":"nonexistent","id":4}' | "$binary" 2>/dev/null)
    if echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'error' in d" 2>/dev/null; then
        echo -e "    ${GREEN}✅ error handling${NC}"; ((pass++))
    else
        echo -e "    ${RED}❌ error handling: ${result:0:120}${NC}"; ((fail++))
    fi

    # Test 5: long-running stdio loop — process must stay alive after one describe
    # ─────────────────────────────────────────────────────────────────────────────
    # The Anna Agent reuses the same plugin process for many requests; a plugin
    # that exits after one response shows up as "Stopped" in the UI forever.
    # We feed `describe`, wait for the response, then check the process is still
    # alive 1.5 s later. EOF only happens when we close stdin (mkfifo trick).
    if python3 - "$binary" <<'PYTEST' 2>/dev/null; then
import os, sys, json, time, subprocess
binary = sys.argv[1]
proc = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
proc.stdin.write(b'{"jsonrpc":"2.0","method":"describe","id":99}\n')
proc.stdin.flush()
# Read one line of response
line = proc.stdout.readline()
assert line and b'"result"' in line, f"no describe response: {line!r}"
# Process MUST still be alive — we have not closed stdin yet
time.sleep(1.5)
if proc.poll() is not None:
    proc.wait(timeout=1)
    raise SystemExit(f"plugin exited after one describe (exit_code={proc.returncode})")
# Clean shutdown via EOF
proc.stdin.close()
try:
    proc.wait(timeout=3)
except subprocess.TimeoutExpired:
    proc.kill()
PYTEST
        echo -e "    ${GREEN}✅ long-running stdio loop${NC}"; ((pass++))
    else
        echo -e "    ${RED}❌ long-running stdio loop: process exited after one request${NC}"
        echo -e "       ${YELLOW}fix: wrap request handling in 'for line in sys.stdin:' and never sys.exit()${NC}"
        ((fail++))
    fi

    echo -e "    Results: ${GREEN}${pass} passed${NC}, ${RED}${fail} failed${NC}"
    return "$fail"
}

# ══════════════════════════════════════════════════════════════
# Main flow
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

# List files to compile
for pf in "${PLUGIN_FILES[@]}"; do
    pn=$(extract_plugin_name "$pf")
    echo -e "    • ${pf} → ${BOLD}${pn}${NC}"
done
echo ""

# Step 1: Clean
echo -e "${GREEN}[1/3] Cleaning old build artifacts...${NC}"
rm -rf build/ __pycache__/ *.spec.bak
mkdir -p dist

# Step 2: Ensure compiler
ensure_compiler

# Step 3: Compile one by one
echo -e "${GREEN}[2/3] Compiling binaries...${NC}"
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
echo -e "${GREEN}[3/3] Compilation complete: ${#BUILT[@]} succeeded, ${BUILD_FAIL} failed${NC}"

# Step 4: Protocol tests
if [[ "$RUN_TEST" == "true" && ${#BUILT[@]} -gt 0 ]]; then
    echo ""
    echo -e "${CYAN}══ Protocol Tests ══════════════════════════════${NC}"
    TOTAL_FAIL=0
    for pn in "${BUILT[@]}"; do
        test_one "dist/${pn}" "$pn" || ((TOTAL_FAIL++))
    done
    echo ""
    if [[ $TOTAL_FAIL -gt 0 ]]; then
        echo -e "${RED}⚠ ${TOTAL_FAIL} plugin test(s) had failures${NC}"
        exit 1
    else
        echo -e "${GREEN}✅ All plugin tests passed${NC}"
    fi
elif [[ "$RUN_TEST" == "false" && ${#BUILT[@]} -gt 0 ]]; then
    echo -e "  Tests skipped (enable with --test flag)"
fi

# Summary
if [[ ${#BUILT[@]} -gt 0 ]]; then
    echo ""
    echo -e "${CYAN}── Build Artifacts ─────────────────────────────${NC}"
    for pn in "${BUILT[@]}"; do
        local_size=$(du -h "dist/${pn}" | cut -f1)
        echo -e "  dist/${pn}  (${local_size})"
    done
    echo ""
    echo -e "${CYAN}── Next Steps ──────────────────────────────────${NC}"
    echo -e "  Local install:"
    echo -e "    cp dist/* ~/.anna/executa/bin/"
    echo ""
    echo -e "  Package and upload:"
    PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
    for pn in "${BUILT[@]}"; do
        echo -e "    cd dist && tar czf ${pn}-${PLATFORM}.tar.gz ${pn}"
    done
    echo ""
fi

[[ $BUILD_FAIL -gt 0 ]] && exit 1
exit 0
