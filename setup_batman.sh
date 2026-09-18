#!/usr/bin/env bash
# Backward-compatibility launcher for scripts/setup_batman.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "${SCRIPT_DIR}/scripts/setup_batman.sh" 2>/dev/null || true
exec "${SCRIPT_DIR}/scripts/setup_batman.sh" "$@"
