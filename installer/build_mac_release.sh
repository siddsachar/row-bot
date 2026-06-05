#!/usr/bin/env bash
# =============================================================================
# build_mac_release.sh â€” Package Row-Bot into a distributable macOS zip
#
# Creates:  installer/Row-Bot-<version>-macOS.zip
#
# The zip contains the full project directory with "Start Row-Bot.command"
# at the top level.  Users unzip, double-click the .command file, and
# everything is installed automatically.
#
# Usage:  ./installer/build_mac_release.sh
# =============================================================================

set -euo pipefail

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
OUTPUT_NAME="Row-Bot-${VERSION}-macOS"
OUTPUT_ZIP="${OUTPUT_DIR:-$SCRIPT_DIR}/${OUTPUT_NAME}.zip"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[  OK]${NC}  $*"; }

echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${BOLD} ð“‚€  Build Row-Bot macOS Release Zip${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""

# â”€â”€ Sanity checks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
if [ ! -f "$PROJECT_DIR/Start Row-Bot.command" ]; then
    echo -e "${RED}[FAIL]${NC}  Start Row-Bot.command not found at project root."
    exit 1
fi

if [ ! -f "$PROJECT_DIR/requirements.txt" ]; then
    echo -e "${RED}[FAIL]${NC}  requirements.txt not found."
    exit 1
fi

# â”€â”€ Ensure shell scripts are executable â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
chmod +x "$PROJECT_DIR/Start Row-Bot.command"
chmod +x "$PROJECT_DIR/installer/Row-Bot.app/Contents/MacOS/row-bot" 2>/dev/null || true
find "$PROJECT_DIR/installer" -name "*.sh" -exec chmod +x {} \;

# â”€â”€ Build zip â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
info "Building zip archive (excluding dev/runtime files)..."

# Create a staging directory named "Row-Bot" so the zip extracts to Row-Bot/
STAGING="$(mktemp -d)/Row-Bot"
rsync -a --exclude='.venv' \
         --exclude='.venv-linux' \
         --exclude='__pycache__' \
         --exclude='.git' \
         --exclude='.github' \
         --exclude='.local' \
         --exclude='dist' \
         --exclude='tests' \
         --exclude='pytest.ini' \
         --exclude='.pytest_cache' \
         --exclude='test-results' \
         --exclude='.tmp' \
         --exclude='.tmp_pytest' \
         --exclude='.testtmp' \
         --exclude='scripts' \
         --exclude='channels/whatsapp_bridge/node_modules' \
         --exclude='docs/*implementation-plan.md' \
         --exclude='docs/*overhaul-plan.md' \
         --exclude='installer/build' \
         --exclude='installer/*.zip' \
         --exclude='installer/*.exe' \
         --exclude='.DS_Store' \
         --exclude='test_*.py' \
         --exclude='*_test.py' \
         --exclude='*_harness.py' \
         --exclude='*.pyc' \
         --filter='- *.bak' \
         --filter='- *.bak[0-9]*' \
         --filter='- *.bak.*' \
         "$PROJECT_DIR/" "$STAGING/"

# Remove previous build
rm -f "$OUTPUT_ZIP"

cd "$(dirname "$STAGING")"
zip -r "$OUTPUT_ZIP" "Row-Bot"
rm -rf "$STAGING"

# â”€â”€ Summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
ZIP_SIZE=$(du -h "$OUTPUT_ZIP" | cut -f1)
ok "Created $OUTPUT_ZIP ($ZIP_SIZE)"

echo ""
echo "  Contents:"
echo "    â€¢ Start Row-Bot.command   (double-click to install & launch)"
echo "    â€¢ Source files, requirements.txt, tools/, channels/"
echo "    â€¢ installer/Row-Bot.app/  (template, copied to /Applications at install)"
echo ""
echo "  Upload this zip to GitHub Releases for distribution."
echo ""
