#!/usr/bin/env bash
# ==============================================================================
# run_node.sh - Tactical SDR Mesh (TSM-Net SG) Single-Command Node Runner
# Starts kernel MANET (batman-adv), TAP device, and Mesh Orchestrator.
#
# Usage:
#   sudo ./run_node.sh [NODE_IP] [--dry-run]
# Examples:
#   sudo ./run_node.sh 10.10.0.1/24           # Normal SDR mode on Node 1
#   sudo ./run_node.sh 10.10.0.2/24           # Normal SDR mode on Node 2
#   sudo ./run_node.sh 10.10.0.1/24 --dry-run # Loopback test mode (no SDR needed)
# ==============================================================================

set -eo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "[ERROR] Please run with root privileges: sudo $0 $@" >&2
    exit 1
fi

NODE_IP="10.10.0.1/24"
DRY_RUN=""

for arg in "$@"; do
    if [[ "$arg" == "--dry-run" ]]; then
        DRY_RUN="--dry-run"
    elif [[ "$arg" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]]; then
        NODE_IP="$arg"
    fi
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.yaml"

echo "=================================================================="
echo " Starting Tactical SDR Mesh Node (TSM-Net SG)"
echo " Node IP:    ${NODE_IP}"
echo " Config:     ${CONFIG_FILE}"
echo " Mode:       $([ -n "$DRY_RUN" ] && echo 'DRY-RUN (Loopback Test)' || echo 'LIVE SDR (Pluto+ at 915 MHz)')"
echo "=================================================================="

# 1. Run Kernel MANET Setup
echo -e "\n[STEP 1/3] Initializing Linux Kernel batman-adv & TAP interface..."
"${SCRIPT_DIR}/setup_batman.sh" "${NODE_IP}"

# Process cleanup handler on Ctrl+C / exit
cleanup() {
    echo -e "\n[SHUTDOWN] Terminating tactical mesh processes..."
    if [ -n "${BRIDGE_PID:-}" ] && kill -0 "${BRIDGE_PID}" 2>/dev/null; then
        kill -SIGTERM "${BRIDGE_PID}" 2>/dev/null || true
        wait "${BRIDGE_PID}" 2>/dev/null || true
    fi
    echo "[SHUTDOWN] Cleanup complete. Exiting."
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# 2. Check & Start GNU Radio Physical Bridge (if not in dry-run mode)
BRIDGE_PID=""
if [ -z "$DRY_RUN" ]; then
    if python3 -c "import gnuradio" 2>/dev/null; then
        echo -e "\n[STEP 2/3] Launching GNU Radio Pluto+ Bridge in background..."
        python3 "${SCRIPT_DIR}/gr_lora_pluto_bridge.py" --config "${CONFIG_FILE}" &
        BRIDGE_PID=$!
        sleep 2
    else
        echo -e "\n[NOTICE] 'gnuradio' not found in system Python."
        echo "Running Orchestrator in loopback verification mode."
        echo "To compile the full GNU Radio flowgraph, install: sudo apt install gnuradio"
        DRY_RUN="--dry-run"
    fi
fi

# 3. Start the Tactical Mesh Orchestrator
echo -e "\n[STEP 3/3] Starting Tactical Mesh Orchestrator (Press Ctrl+C to stop)..."
python3 "${SCRIPT_DIR}/tsm_mesh_orchestrator.py" --config "${CONFIG_FILE}" ${DRY_RUN}
