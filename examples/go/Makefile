# ============================================================
# Executa Go Plugin — 多平台构建
# ============================================================
# 用法:
#   make            # 构建当前平台
#   make all        # 构建所有平台
#   make test       # 构建 + 协议测试
#   make clean      # 清理
#   make package    # 打包所有平台为 tar.gz / zip
# ============================================================

PLUGIN_NAME := example-go-tool
VERSION     := 1.0.0

# 标准平台（与 Anna 的 KNOWN_PLATFORMS 对应）
PLATFORMS := \
	darwin-arm64 \
	darwin-x86_64 \
	linux-x86_64 \
	linux-aarch64 \
	windows-x86_64 \
	windows-arm64

# Go OS/ARCH 映射
platform_os   = $(word 1,$(subst -, ,$(1)))
platform_arch = $(word 2,$(subst -, ,$(1)))
go_os   = $(call platform_os,$(1))
go_arch = $(subst x86_64,amd64,$(subst aarch64,arm64,$(subst armv7l,arm,$(call platform_arch,$(1)))))

# Windows 后缀
exe_suffix = $(if $(findstring windows,$(1)),.exe,)

.PHONY: build all test clean package

# 默认: 构建当前平台
build:
	@mkdir -p dist
	go build -ldflags="-s -w" -o dist/$(PLUGIN_NAME) .
	@echo "✅ Built dist/$(PLUGIN_NAME)"

# 构建所有平台
all:
	@mkdir -p dist
	@for plat in $(PLATFORMS); do \
		os=$$(echo $$plat | cut -d- -f1); \
		raw_arch=$$(echo $$plat | cut -d- -f2); \
		goarch=$$(echo $$raw_arch | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/' | sed 's/armv7l/arm/'); \
		suffix=""; \
		if [ "$$os" = "windows" ]; then suffix=".exe"; fi; \
		echo "  Building $$plat (GOOS=$$os GOARCH=$$goarch)..."; \
		GOOS=$$os GOARCH=$$goarch go build -ldflags="-s -w" \
			-o "dist/$(PLUGIN_NAME)-$$plat$$suffix" . || exit 1; \
	done
	@echo ""
	@echo "✅ All platforms built:"
	@ls -lh dist/

# 协议测试
test: build
	@echo ""
	@echo "── 协议测试 ──────────────────────────────────"
	@echo '{"jsonrpc":"2.0","method":"describe","id":1}' | ./dist/$(PLUGIN_NAME) 2>/dev/null | \
		python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['name']=='$(PLUGIN_NAME)'; print('  ✅ describe 通过')"
	@echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"system_info","arguments":{}},"id":2}' | ./dist/$(PLUGIN_NAME) 2>/dev/null | \
		python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['success']==True; print('  ✅ invoke 通过')"
	@echo '{"jsonrpc":"2.0","method":"health","id":3}' | ./dist/$(PLUGIN_NAME) 2>/dev/null | \
		python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['status']=='healthy'; print('  ✅ health 通过')"
	@echo '{"jsonrpc":"2.0","method":"bad","id":4}' | ./dist/$(PLUGIN_NAME) 2>/dev/null | \
		python3 -c "import sys,json; d=json.load(sys.stdin); assert 'error' in d; print('  ✅ error handling 通过')"

# 打包（tar.gz for unix, zip for windows）
package: all
	@mkdir -p dist/packages
	@for plat in $(PLATFORMS); do \
		suffix=""; \
		if echo $$plat | grep -q windows; then suffix=".exe"; fi; \
		binary="dist/$(PLUGIN_NAME)-$$plat$$suffix"; \
		if echo $$plat | grep -q windows; then \
			(cd dist && zip -j "packages/$(PLUGIN_NAME)-$$plat.zip" "$$(basename $$binary)"); \
		else \
			(cd dist && tar czf "packages/$(PLUGIN_NAME)-$$plat.tar.gz" "$$(basename $$binary)"); \
		fi; \
	done
	@echo ""
	@echo "✅ Packages:"
	@ls -lh dist/packages/

clean:
	rm -rf dist/
