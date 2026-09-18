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
SDR_URI="${SDR_URI:-usb:1.3.5}"

echo "=================================================================="
echo " Starting Tactical SDR Mesh Unified Micro-Server (TSM-Net SG)"
echo " Root:       ${REPO_ROOT}"
echo " gRPC Port:  ${GRPC_PORT}"
echo " HTTP Port:  ${HTTP_PORT} (Web Dashboard & Spectrum Stream)"
echo " SDR URI:    ${SDR_URI}"
echo "=================================================================="

exec python3 -m tsm.api.server --grpc-port "${GRPC_PORT}" --http-port "${HTTP_PORT}" --sdr-uri "${SDR_URI}" "$@"
