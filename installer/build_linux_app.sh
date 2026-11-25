#!/usr/bin/env bash
# =============================================================================
# build_linux_app.sh - Build self-contained Row-Bot Linux tarball
#
# Produces dist/Row-Bot-X.Y.Z-Linux-ARCH.tar.gz using python-build-standalone.
# The artifact installs into XDG user paths via install.sh
# ($XDG_DATA_HOME/row-bot/releases/<version>, `current` and state.json; see
# src/row_bot/install_layout.py) and launches in browser/no-tray mode by default
# so Linux desktop system dependencies stay optional. installer/linux holds the
# release's bin/row-bot, install.sh and uninstall.sh.
# =============================================================================

set -euo pipefail
# Nothing run during the build writes timestamp .pyc files into the package;
# the deterministic ones are compiled once the package is complete.
export PYTHONDONTWRITEBYTECODE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_VERSION="$(python3 - "$PROJECT_DIR/src/row_bot/version.py" <<'PY'
import sys
from pathlib import Path
ns = {}
exec(Path(sys.argv[1]).read_text(encoding='utf-8'), ns)
print(ns.get('__version__', '0.0.0'))
PY
)"
VERSION="${1:-$DEFAULT_VERSION}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12.13}"
PBS_RELEASE="${PBS_RELEASE:-20260303}"
BUILD_DIR="$SCRIPT_DIR/build/linux"
DIST_DIR="$PROJECT_DIR/dist"

# Linux browser bundling policy. Default is 0 because system dependencies for
# Chromium are distro-specific; CI and local release builds can force 1.
BUNDLE_PLAYWRIGHT="${BUNDLE_PLAYWRIGHT:-0}"

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|amd64) PBS_ARCH="x86_64"; PACKAGE_ARCH="x86_64" ;;
    aarch64|arm64) PBS_ARCH="aarch64"; PACKAGE_ARCH="aarch64" ;;
    *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

PBS_FILENAME="cpython-${PYTHON_VERSION}+${PBS_RELEASE}-${PBS_ARCH}-unknown-linux-gnu-install_only.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${PBS_FILENAME}"
PACKAGE_NAME="Row-Bot-${VERSION}-Linux-${PACKAGE_ARCH}"
PACKAGE_ROOT="$BUILD_DIR/$PACKAGE_NAME"
APP_SRC="$PACKAGE_ROOT/app"
PYTHON_PREFIX="$PACKAGE_ROOT/python"
TARBALL="$DIST_DIR/${PACKAGE_NAME}.tar.gz"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok() { echo -e "${GREEN}[  OK]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail() { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${BOLD} Build Row-Bot Linux package (v${VERSION})${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""
info "Architecture: $ARCH ($PACKAGE_ARCH)"
info "Python:       $PYTHON_VERSION (PBS $PBS_RELEASE)"

# Node is a pinned build tool; none of its runtime is copied into the application.
CLIENT_BUILD="$PROJECT_DIR/frontend/dist"
CLIENT_NODE="$(command -v node)" || fail "Node 24.15.0 is required to stage the client"
[ "$("$CLIENT_NODE" --version)" = "v24.15.0" ] || fail "Client packaging requires Node 24.15.0"
for client_file in index.html asset-manifest.json .vite/manifest.json; do
    [ -f "$CLIENT_BUILD/$client_file" ] || fail "Client build missing; build and package the locked frontend first"
done

rm -rf "$PACKAGE_ROOT"
mkdir -p "$BUILD_DIR" "$DIST_DIR" "$APP_SRC" "$PACKAGE_ROOT/bin" \
         "$PACKAGE_ROOT/share/applications" "$PACKAGE_ROOT/share/icons/hicolor/256x256/apps"

PBS_TAR="$BUILD_DIR/$PBS_FILENAME"
if [ ! -f "$PBS_TAR" ]; then
    info "[1/6] Downloading Python $PYTHON_VERSION standalone ($PBS_ARCH)..."
    curl -L --fail -o "$PBS_TAR" "$PBS_URL"
    ok "Downloaded"
else
    info "[1/6] Using cached Python tarball"
fi

info "Extracting Python..."
rm -rf "$PYTHON_PREFIX"
mkdir -p "$PYTHON_PREFIX"
tar -xzf "$PBS_TAR" -C "$PYTHON_PREFIX" --strip-components=1
"$PYTHON_PREFIX/bin/python3" --version
ok "Python extracted"

if [ ! -f "$PROJECT_DIR/uv.lock" ]; then
    fail "uv.lock not found. Regenerate dependencies before building."
fi

info "[2/6] Installing locked Python packages from requirements.txt..."
"$PYTHON_PREFIX/bin/python3" -m pip install --upgrade pip setuptools wheel --quiet 2>&1 | tail -1 || true
"$PYTHON_PREFIX/bin/python3" -m pip install --no-compile --no-deps -r "$PROJECT_DIR/requirements.txt" --quiet 2>&1 | tail -5
"$PYTHON_PREFIX/bin/python3" "$PROJECT_DIR/scripts/verify_runtime_dependencies.py" all
"$PYTHON_PREFIX/bin/python3" "$PROJECT_DIR/scripts/verify_client_assets.py" --root "$CLIENT_BUILD"
ok "Python packages installed"

if [ "$PACKAGE_ARCH" = "x86_64" ]; then
    info "Checking native CPU baselines for older x86_64 compatibility..."
    "$PYTHON_PREFIX/bin/python3" "$PROJECT_DIR/scripts/check_linux_native_baseline.py" --arch "$PACKAGE_ARCH"
    ok "Native CPU baselines are package-compatible"
fi

if [ "$BUNDLE_PLAYWRIGHT" = "1" ]; then
    info "Installing Playwright Chromium into package..."
    export PLAYWRIGHT_BROWSERS_PATH="$PYTHON_PREFIX/playwright-browsers"
    "$PYTHON_PREFIX/bin/python3" -m playwright install chromium 2>&1 | tail -3 || \
        warn "Playwright Chromium install failed; Browser Automation requires an explicit Install or Repair action"
else
    info "Skipping bundled Playwright Chromium; Browser Automation will use a supported browser channel or an explicitly installed managed runtime"
fi

info "[3/6] Copying source code..."
MANIFEST_PY="$PROJECT_DIR/scripts/app_payload_manifest.py"
if [ ! -f "$MANIFEST_PY" ]; then
    fail "App payload manifest not found: $MANIFEST_PY"
fi

copy_payload_file() {
    local rel="$1"
    local src="$PROJECT_DIR/$rel"
    local dest="$APP_SRC/$rel"
    if [ ! -f "$src" ]; then
        fail "Manifest file is missing: $rel"
    fi
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
}

while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    copy_payload_file "$rel"
done < <(python3 "$MANIFEST_PY" --project-root "$PROJECT_DIR" --category root_python_files)

while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    copy_payload_file "$rel"
done < <(python3 "$MANIFEST_PY" --project-root "$PROJECT_DIR" --category root_files)

while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    copy_payload_file "$rel"
done < <(python3 "$MANIFEST_PY" --project-root "$PROJECT_DIR" --category runtime_script_files)

# Historical contract now owned by scripts/app_payload_manifest.py:
# for pkg in src/row_bot
while IFS= read -r pkg; do
    [ -n "$pkg" ] || continue
    if [ -d "$PROJECT_DIR/$pkg" ]; then
        mkdir -p "$(dirname "$APP_SRC/$pkg")"
        rsync -a \
              --exclude='__pycache__' --exclude='*.pyc' \
              --exclude='node_modules' --exclude='.pytest_cache' \
              --exclude='/static/client-v2/***' \
              --exclude='tests' --exclude='test' --exclude='test-results' \
              --exclude='*.test.js' --exclude='*.spec.js' \
              --filter='- *.bak' --filter='- *.bak[0-9]*' \
              "$PROJECT_DIR/$pkg/" "$APP_SRC/$pkg/"
    else
        fail "Manifest directory is missing: $pkg"
    fi
done < <(python3 "$MANIFEST_PY" --project-root "$PROJECT_DIR" --category payload_dirs)

# The source copy excludes old generated assets; this destination must be fresh.
CLIENT_STAGE="$APP_SRC/src/row_bot/static/client-v2"
"$CLIENT_NODE" "$PROJECT_DIR/frontend/scripts/asset-manifest.mjs" "$CLIENT_BUILD" --package-dir "$CLIENT_STAGE"
"$PYTHON_PREFIX/bin/python3" "$PROJECT_DIR/scripts/verify_client_assets.py" \
    --root "$CLIENT_STAGE" --compare "$CLIENT_BUILD" --strict

# Historical contract now owned by scripts/app_payload_manifest.py:
# for dir in static sounds bundled_skills tool_guides;
while IFS= read -r dir; do
    [ -n "$dir" ] || continue
    if [ -d "$PROJECT_DIR/$dir" ]; then
        mkdir -p "$(dirname "$APP_SRC/$dir")"
        rsync -a --exclude='*.pyc' "$PROJECT_DIR/$dir/" "$APP_SRC/$dir/"
    else
        fail "Manifest asset directory is missing: $dir"
    fi
done < <(python3 "$MANIFEST_PY" --project-root "$PROJECT_DIR" --category asset_dirs)

LINUX_ICON_SOURCE=""
while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    if [ -f "$PROJECT_DIR/$rel" ]; then
        LINUX_ICON_SOURCE="$rel"
        break
    fi
done < <(python3 "$MANIFEST_PY" --project-root "$PROJECT_DIR" --category linux_icon_candidates)
if [ -n "$LINUX_ICON_SOURCE" ]; then
    cp "$PROJECT_DIR/$LINUX_ICON_SOURCE" \
       "$PACKAGE_ROOT/share/icons/hicolor/256x256/apps/row-bot.png"
fi
ok "Source code copied"

info "[4/6] Creating launchers and install metadata..."
# The launcher wrapper, install.sh and uninstall.sh ship in every release.
install -m 0755 "$SCRIPT_DIR/linux/row-bot" "$PACKAGE_ROOT/bin/row-bot"
install -m 0755 "$SCRIPT_DIR/linux/install.sh" "$PACKAGE_ROOT/install.sh"
install -m 0755 "$SCRIPT_DIR/linux/uninstall.sh" "$PACKAGE_ROOT/uninstall.sh"

cat > "$PACKAGE_ROOT/share/applications/ai.row-bot.RowBot.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Row-Bot
Comment=Local-first AI assistant
Exec=row-bot
Icon=row-bot
Terminal=false
Categories=Utility;Office;
StartupNotify=true
DESKTOP

cat > "$PACKAGE_ROOT/install_info.json" <<JSON
{
  "name": "Row-Bot",
  "version": "$VERSION",
  "platform": "linux",
  "arch": "$PACKAGE_ARCH",
  "install_kind": "xdg-user-tarball"
}
JSON

ok "Launchers created"

info "[5/6] Cleaning package..."
find "$PACKAGE_ROOT" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$PACKAGE_ROOT" -name '*.pyc' -delete 2>/dev/null || true
find "$APP_SRC" \( -name '.pytest_cache' -o -name 'test-results' -o -name 'node_modules' \) -prune -exec rm -rf {} + 2>/dev/null || true
# Libraries' test suites, headers, link libraries, unused Google API documents,
# console-script launchers and the standard library's tests. The rules are
# shared with the Windows and macOS builds: src/row_bot/payload_trim.json.
"$PYTHON_PREFIX/bin/python3" -I -S "$PROJECT_DIR/src/row_bot/payload_trim.py" trim --python-root "$PYTHON_PREFIX"
if [ -d "$APP_SRC/docs" ]; then
    fail "Linux package payload unexpectedly contains docs/"
fi
if find "$APP_SRC" -path "$APP_SRC/scripts/verify_runtime_dependencies.py" -prune -o \
       \( -path '*/tests/*' -o -name 'test_*.py' -o -name '*_test.py' -o -name '*_harness.py' -o -name 'pytest.ini' \) -print -quit | grep -q .; then
    fail "Linux package payload contains test or harness artifacts"
fi
if find "$APP_SRC/scripts" -type f ! -name 'verify_runtime_dependencies.py' -print -quit | grep -q .; then
    fail "Linux package payload contains non-runtime scripts"
fi
find "$PACKAGE_ROOT" -name '*.so' -print0 | while IFS= read -r -d '' lib; do
    strip "$lib" 2>/dev/null || true
done
ok "Package cleaned"

# Release folders are shared through hard links by later updates, so they never
# change after install: ship .pyc that need no rewrite (unchecked-hash) and do
# not depend on the build folder (-s); bin/row-bot sets PYTHONDONTWRITEBYTECODE.
# The app gets bytecode, and so does every runtime module the runtime check
# imports (it runs again here, on the trimmed runtime).
info "Compiling deterministic bytecode..."
ROW_BOT_INSTALL_ROOT="$PACKAGE_ROOT" "$PYTHON_PREFIX/bin/python3" -I -S "$PROJECT_DIR/src/row_bot/payload_trim.py" bytecode \
    --python-root "$PYTHON_PREFIX" --app-root "$APP_SRC" --version-root "$PACKAGE_ROOT"
ok "Bytecode compiled"

info "Verifying assembled Linux runtime dependencies..."
ROW_BOT_INSTALL_ROOT="$PACKAGE_ROOT" PYTHONNOUSERSITE=1 \
    "$PYTHON_PREFIX/bin/python3" "$APP_SRC/scripts/verify_runtime_dependencies.py" all
ok "Assembled Linux runtime dependencies verified"
"$PYTHON_PREFIX/bin/python3" "$PROJECT_DIR/scripts/verify_client_assets.py" \
    --root "$CLIENT_STAGE" --compare "$CLIENT_BUILD" --strict

"$PYTHON_PREFIX/bin/python3" -I -S "$PROJECT_DIR/src/row_bot/install_layout.py" version-json \
    --version "$VERSION" --python "$PYTHON_VERSION" --lock "$PROJECT_DIR/uv.lock" --out "$PACKAGE_ROOT/version.json"

info "[6/6] Creating tarball..."
rm -f "$TARBALL"
tar -C "$BUILD_DIR" -czf "$TARBALL" "$PACKAGE_NAME"
SIZE="$(du -sh "$TARBALL" | cut -f1)"
ok "Created $TARBALL ($SIZE)"

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN} Linux build complete${NC}"
echo -e "${GREEN} $TARBALL${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
