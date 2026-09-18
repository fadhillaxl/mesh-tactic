#!/usr/bin/env bash
# Backward-compatibility launcher for scripts/run_grpc_server.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "${SCRIPT_DIR}/scripts/run_grpc_server.sh" 2>/dev/null || true
exec "${SCRIPT_DIR}/scripts/run_grpc_server.sh" "$@"
