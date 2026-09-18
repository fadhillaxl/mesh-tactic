#!/usr/bin/env bash
# Backward-compatibility launcher for scripts/run_node.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "${SCRIPT_DIR}/scripts/run_node.sh" 2>/dev/null || true
exec "${SCRIPT_DIR}/scripts/run_node.sh" "$@"
