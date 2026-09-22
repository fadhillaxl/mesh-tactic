#!/usr/bin/env bash
# ==============================================================================
# setup_batman.sh - Tactical SDR Mesh (TSM-Net SG) Kernel Networking Setup
# Configures batman-adv, creates virtual TAP interface, and clamps MTU for SDR.
# ==============================================================================

set -euo pipefail

# Ensure running with root privileges
if [ "$EUID" -ne 0 ]; then
  echo "[ERROR] Please run as root (e.g., sudo $0)" >&2
  exit 1
fi

NODE_IP="${1:-10.10.0.1/24}"
TAP_DEV="tap-radio"
BAT_DEV="bat0"
RADIO_MTU=240
BAT_MTU=208

echo "============================================================"
echo " Initializing Tactical SDR Mesh Network (TSM-Net SG)"
echo " Node IP:     ${NODE_IP}"
echo " TAP Device:  ${TAP_DEV} (MTU: ${RADIO_MTU})"
echo " BATMAN Dev:  ${BAT_DEV} (MTU: ${BAT_MTU})"
echo "============================================================"

# 1. Load the batman-adv kernel module
echo "[1/6] Loading batman-adv kernel module..."
modprobe batman-adv

# 2. Configure B.A.T.M.A.N. routing algorithm & SDR orig_interval
if command -v batctl &> /dev/null; then
    batctl ra BATMAN_IV || true
    # 3000ms interval avoids flooding half-duplex 50 kbps SDR channels with OGMs
    batctl it 3000 2>/dev/null || true
else
    echo "[ERROR] 'batctl' utility not found. Install it via: sudo apt install batctl" >&2
    exit 1
fi

# 3. Clean up any existing stale interfaces
echo "[2/6] Cleaning up any previous interfaces..."
ip link set dev "${BAT_DEV}" down 2>/dev/null || true
ip link set dev "${TAP_DEV}" down 2>/dev/null || true
ip tuntap del dev "${TAP_DEV}" mode tap 2>/dev/null || true

# 4. Create persistent Layer-2 TAP device for SDR ingestion
echo "[3/6] Creating persistent TAP interface '${TAP_DEV}'..."
ip tuntap add dev "${TAP_DEV}" mode tap

# Set MTU on the radio TAP interface to fit SDR physical payload
ip link set dev "${TAP_DEV}" mtu "${RADIO_MTU}"
ip link set dev "${TAP_DEV}" up

# 5. Enslave the TAP device into batman-adv
echo "[4/6] Enslaving '${TAP_DEV}' into '${BAT_DEV}'..."
batctl if add "${TAP_DEV}"

# 6. Bring up bat0 and assign mesh IP
echo "[5/6] Activating '${BAT_DEV}' with IP ${NODE_IP}..."
ip link set dev "${BAT_DEV}" mtu "${BAT_MTU}"
ip addr flush dev "${BAT_DEV}" || true
ip addr add "${NODE_IP}" dev "${BAT_DEV}"
ip link set dev "${BAT_DEV}" up

# Enable IP forwarding and disable ICMP redirects for mesh routing
echo "[6/6] Applying kernel routing optimizations..."
sysctl -w net.ipv4.ip_forward=1 > /dev/null
sysctl -w net.ipv4.conf.all.send_redirects=0 > /dev/null
sysctl -w net.ipv4.conf.all.accept_redirects=0 > /dev/null

echo "============================================================"
echo "[SUCCESS] batman-adv interface '${BAT_DEV}' is UP and running!"
echo "Current B.A.T.M.A.N. status:"
batctl if
echo "============================================================"
