#!/usr/bin/env bash
# ==============================================================================
# run_node.sh - Tactical SDR Mesh (TSM-Net SG) Single-Command Node Runner
# Starts kernel MANET (batman-adv), TAP device, and Mesh Orchestrator.
#
# Usage:
#   sudo ./scripts/run_node.sh [NODE_IP] [--dry-run]
# ==============================================================================

set -eo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "[ERROR] Please run with root privileges: sudo $0 $@" >&2
    exit 1
fi

NODE_IP="10.10.0.1/24"
DRY_RUN=""
PEER=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN="--dry-run"
            shift
            ;;
        --peer)
            PEER="$2"
            shift 2
            ;;
        --uri)
            SDR_URI="$2"
            shift 2
            ;;
        [0-9]*.[0-9]*.[0-9]*.[0-9]*/[0-9]*)
            NODE_IP="$1"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Set Python path to find tsm package
export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"

CONFIG_FILE="${REPO_ROOT}/config/config.yaml"
if [ ! -f "${CONFIG_FILE}" ]; then
    CONFIG_FILE="${REPO_ROOT}/config.yaml"
fi

echo "=================================================================="
echo " Starting Tactical SDR Mesh Node (TSM-Net SG)"
echo " Node IP:    ${NODE_IP}"
echo " Config:     ${CONFIG_FILE}"
echo " Root:       ${REPO_ROOT}"
echo " Mode:       $([ -n "$DRY_RUN" ] && echo 'DRY-RUN (Loopback Test)' || echo 'LIVE SDR (Pluto+ at 915 MHz)')"
echo "=================================================================="

# 1. Run Kernel MANET Setup
echo -e "\n[STEP 1/3] Initializing Linux Kernel batman-adv & TAP interface..."
chmod +x "${SCRIPT_DIR}/setup_batman.sh" 2>/dev/null || true
bash "${SCRIPT_DIR}/setup_batman.sh" "${NODE_IP}"

# Process cleanup handler on Ctrl+C / exit
cleanup() {
    echo -e "\n[SHUTDOWN] Terminating tactical mesh processes..."
    if [ -n "${MODEM_PID:-}" ] && kill -0 "${MODEM_PID}" 2>/dev/null; then
        kill -SIGTERM "${MODEM_PID}" 2>/dev/null || true
        wait "${MODEM_PID}" 2>/dev/null || true
    fi
    echo "[SHUTDOWN] Cleanup complete. Exiting."
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# 2. Start Continuous Digital Baseband I/Q RF Modem
MODEM_PID=""
if [ -z "$DRY_RUN" ] && [ -z "$PEER" ]; then
    echo -e "\n[STEP 2/3] Launching Continuous 2-FSK Digital Baseband I/Q Modem (tsm.modem.sdr_driver)..."
    URI_OPT=""
    if [ -n "${SDR_URI:-}" ]; then
        URI_OPT="--uri ${SDR_URI}"
    fi
    python3 -m tsm.modem.sdr_driver --config "${CONFIG_FILE}" ${URI_OPT} &
    MODEM_PID=$!
    sleep 3
elif [ -n "$PEER" ]; then
    echo -e "\n[STEP 2/3] Direct Peer Mode active (Target: ${PEER}). Skipping local SDR modem..."
fi

# 3. Start the Tactical Mesh Orchestrator
echo -e "\n[STEP 3/3] Starting Tactical Mesh Orchestrator (tsm.network.orchestrator)..."
PEER_OPT=""
if [ -n "$PEER" ]; then
    PEER_OPT="--peer ${PEER}"
fi
python3 -m tsm.network.orchestrator --config "${CONFIG_FILE}" ${DRY_RUN} ${PEER_OPT}
