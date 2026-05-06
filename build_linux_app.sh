#!/usr/bin/env bash
# Convenience wrapper for building the Linux tarball from the repository root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/installer/build_linux_app.sh" "$@"