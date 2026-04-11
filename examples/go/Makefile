# ============================================================
# Executa Go Plugin — Multi-platform Build
# ============================================================
# Usage:
#   make            # Build for current platform
#   make all        # Build for all platforms
#   make test       # Build + protocol tests
#   make clean      # Clean
#   make package    # Package all platforms as tar.gz / zip
# ============================================================

PLUGIN_NAME := example-go-tool
VERSION     := 1.0.0

# Standard platforms (corresponding to Anna's KNOWN_PLATFORMS)
PLATFORMS := \
	darwin-arm64 \
	darwin-x86_64 \
	linux-x86_64 \
	linux-aarch64 \
	windows-x86_64 \
	windows-arm64

# Go OS/ARCH mapping
platform_os   = $(word 1,$(subst -, ,$(1)))
platform_arch = $(word 2,$(subst -, ,$(1)))
go_os   = $(call platform_os,$(1))
go_arch = $(subst x86_64,amd64,$(subst aarch64,arm64,$(subst armv7l,arm,$(call platform_arch,$(1)))))

# Windows suffix
exe_suffix = $(if $(findstring windows,$(1)),.exe,)

.PHONY: build all test clean package

# Default: build for current platform
build:
	@mkdir -p dist
	go build -ldflags="-s -w" -o dist/$(PLUGIN_NAME) .
	@echo "✅ Built dist/$(PLUGIN_NAME)"

# Build for all platforms
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

# Protocol tests
test: build
	@echo ""
	@echo "── Protocol Tests ──────────────────────────────────"
	@echo '{"jsonrpc":"2.0","method":"describe","id":1}' | ./dist/$(PLUGIN_NAME) 2>/dev/null | \
		python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['name']=='$(PLUGIN_NAME)'; print('  ✅ describe passed')"
	@echo '{"jsonrpc":"2.0","method":"invoke","params":{"tool":"system_info","arguments":{}},"id":2}' | ./dist/$(PLUGIN_NAME) 2>/dev/null | \
		python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['success']==True; print('  ✅ invoke passed')"
	@echo '{"jsonrpc":"2.0","method":"health","id":3}' | ./dist/$(PLUGIN_NAME) 2>/dev/null | \
		python3 -c "import sys,json; d=json.load(sys.stdin); assert d['result']['status']=='healthy'; print('  ✅ health passed')"
	@echo '{"jsonrpc":"2.0","method":"bad","id":4}' | ./dist/$(PLUGIN_NAME) 2>/dev/null | \
		python3 -c "import sys,json; d=json.load(sys.stdin); assert 'error' in d; print('  ✅ error handling passed')"

# Package (tar.gz for unix, zip for windows)
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
