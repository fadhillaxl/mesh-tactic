#!/usr/bin/env bash
# ==============================================================================
# run_grpc_server.sh - Tactical SDR Mesh (TSM-Net SG) Unified Server Launcher
# Launches gRPC API Server (Port 50051) and Cyber-HUD Web UI (Port 8080)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"

GRPC_PORT="${GRPC_PORT:-50051}"
HTTP_PORT="${HTTP_PORT:-8080}"
# Parse optional arguments to override defaults
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --uri|--sdr-uri)
            SDR_URI="$2"
            shift 2
            ;;
        --grpc-port)
            GRPC_PORT="$2"
            shift 2
            ;;
        --http-port)
            HTTP_PORT="$2"
            shift 2
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done

echo "=================================================================="
echo " Starting Tactical SDR Mesh Unified Micro-Server (TSM-Net SG)"
echo " Root:       ${REPO_ROOT}"
echo " gRPC Port:  ${GRPC_PORT}"
echo " HTTP Port:  ${HTTP_PORT} (Web Dashboard & Spectrum Stream)"
echo " SDR URI:    ${SDR_URI}"
echo "=================================================================="

if [ ${#ARGS[@]} -gt 0 ]; then
    exec python3 -m tsm.api.server --grpc-port "${GRPC_PORT}" --http-port "${HTTP_PORT}" --sdr-uri "${SDR_URI}" "${ARGS[@]}"
else
    exec python3 -m tsm.api.server --grpc-port "${GRPC_PORT}" --http-port "${HTTP_PORT}" --sdr-uri "${SDR_URI}"
fi
