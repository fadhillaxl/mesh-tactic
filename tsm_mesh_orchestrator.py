#!/usr/bin/env python3
"""
tsm_mesh_orchestrator.py - Tactical SDR Mesh (TSM-Net SG)
Linux Network & Radio Orchestrator (batman-adv TAP <-> LoRa SDR Bridge)

Engineered under the DietrichGebert/ponytail philosophy:
- Uses native Linux Kernel TAP device (/dev/net/tun) directly via stdlib `fcntl` & `os`.
- No third-party TUN/TAP wrapper dependencies (no pytun / python-pytun).
- Lightweight framing with CRC-16 verification and fragment handling.
- Event-driven non-blocking I/O multiplexing via stdlib `select`.
"""

import os
import sys
import fcntl
import struct
import socket
import select
import signal
import time
import re
import argparse
from typing import Optional, Tuple, Dict, Any

# Linux Kernel TUN/TAP constants (from linux/if_tun.h)
TUNSETIFF = 0x400454CA
IFF_TAP   = 0x0002
IFF_NO_PI = 0x1000

# Tactical Framing Constants
FRAME_MAGIC = 0xD354        # 2 bytes preamble magic
MAX_LORA_PAYLOAD = 240      # Max frame payload to strictly respect 255-byte LoRa PHY limit


def load_config(path: str) -> Dict[str, Any]:
    """Load YAML config with automatic stdlib fallback if PyYAML is not installed."""
    try:
        import yaml
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except ImportError:
        cfg: Dict[str, Any] = {}
        curr_section = None
        with open(path, "r") as f:
            for line in f:
                line = line.split("#")[0].rstrip()
                if not line:
                    continue
                sec_match = re.match(r"^([a-zA-Z0-9_-]+):\s*$", line)
                if sec_match:
                    curr_section = sec_match.group(1)
                    cfg[curr_section] = {}
                    continue
                val_match = re.match(r"^\s+([a-zA-Z0-9_-]+):\s*(.+)$", line)
                if val_match and curr_section:
                    k = val_match.group(1)
                    v_str = val_match.group(2).strip()
                    if v_str.startswith('"') and v_str.endswith('"'):
                        v: Any = v_str[1:-1]
                    elif v_str.lower() in ("true", "yes"):
                        v = True
                    elif v_str.lower() in ("false", "no"):
                        v = False
                    else:
                        try:
                            v = int(v_str) if "." not in v_str else float(v_str)
                        except ValueError:
                            v = v_str
                    cfg[curr_section][k] = v
        return cfg


def crc16_ccitt(data: bytes) -> int:
    """Calculate 16-bit CRC-CCITT (polynomial 0x1021, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


class TacticalFraming:
    """
    Lightweight tactical encapsulation protocol:
    [MAGIC: 2B] [SEQ: 2B] [FRAG_INFO: 1B] [LEN: 1B] [PAYLOAD: N bytes] [CRC16: 2B]
    Header overhead: 6 bytes | Checksum: 2 bytes
    """

    def __init__(self):
        self._seq = 0

    def pack(self, payload: bytes, frag_idx: int = 0, total_frags: int = 1) -> bytes:
        if len(payload) > MAX_LORA_PAYLOAD:
            raise ValueError(f"Payload size {len(payload)} exceeds max LoRa frame chunk ({MAX_LORA_PAYLOAD} bytes)")

        self._seq = (self._seq + 1) & 0xFFFF
        frag_byte = ((frag_idx & 0x0F) << 4) | (total_frags & 0x0F)
        length_byte = len(payload)

        header = struct.pack(">HHBB", FRAME_MAGIC, self._seq, frag_byte, length_byte)
        body = header + payload
        crc = crc16_ccitt(body)
        return body + struct.pack(">H", crc)

    @staticmethod
    def unpack(frame: bytes) -> Optional[Tuple[int, int, int, bytes]]:
        """
        Unpack and validate incoming frame.
        Returns: (seq, frag_idx, total_frags, payload) or None if invalid.
        """
        if len(frame) < 8:
            return None

        # Verify CRC16
        body, received_crc = frame[:-2], frame[-2:]
        expected_crc = crc16_ccitt(body)
        if struct.unpack(">H", received_crc)[0] != expected_crc:
            return None

        # Parse Header
        magic, seq, frag_byte, length = struct.unpack(">HHBB", body[:6])
        if magic != FRAME_MAGIC:
            return None

        payload = body[6: 6 + length]
        frag_idx = (frag_byte >> 4) & 0x0F
        total_frags = frag_byte & 0x0F

        return seq, frag_idx, total_frags, payload


class TacticalMeshOrchestrator:
    """
    Orchestrates ingestion between Linux kernel TAP device and SDR UDP Socket PDUs.
    """

    def __init__(self, config_path: str, dry_run: bool = False, sim_fd: Optional[int] = None, peer: Optional[str] = None):
        self.cfg = load_config(config_path)

        self.dry_run = dry_run
        self.peer = peer
        self.tap_name = self.cfg["network"].get("tap_device", "tap-radio")
        self.ipc_tx_port = int(self.cfg["network"].get("ipc_tx_port", 52001))
        self.ipc_rx_port = int(self.cfg["network"].get("ipc_rx_port", 52002))
        self.mtu = int(self.cfg["network"].get("mtu", 180))

        self.framing = TacticalFraming()
        self.running = False

        # Statistics
        self.tx_count = 0
        self.rx_count = 0
        self.crc_errs = 0

        # Initialize TAP Interface (or use sim_fd if provided)
        if sim_fd is not None:
            self.tap_fd = sim_fd
            print(f"[ORCHESTRATOR] Running in SIMULATION mode with mock TAP fd={sim_fd}")
        else:
            self.tap_fd = self._open_tap_interface(self.tap_name)
            print(f"[ORCHESTRATOR] Attached to TAP device: '{self.tap_name}' (MTU: {self.mtu})")

        # Initialize UDP Sockets (communication with GNU Radio Bridge or Peer Node)
        self.sock_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_rx.bind(("0.0.0.0", self.ipc_rx_port))
        self.sock_rx.setblocking(False)

        if self.peer:
            print(f"[ORCHESTRATOR] Mode: DIRECT PEER MESH (Tunneling to {self.peer}:{self.ipc_rx_port})")
        else:
            print(f"[ORCHESTRATOR] SDR IPC Tx -> 127.0.0.1:{self.ipc_tx_port} | Rx <- 0.0.0.0:{self.ipc_rx_port}")

    def _open_tap_interface(self, dev_name: str) -> int:
        """Opens/creates the Linux persistent TAP interface using native fcntl."""
        if not os.path.exists("/dev/net/tun"):
            raise FileNotFoundError("Kernel TUN/TAP driver (/dev/net/tun) not found. Run 'sudo modprobe tun'")

        try:
            fd = os.open("/dev/net/tun", os.O_RDWR | os.O_NONBLOCK)
            # struct ifreq: 16-byte interface name + 2-byte flags
            ifr = struct.pack("16sH", dev_name.encode("utf-8")[:15], IFF_TAP | IFF_NO_PI)
            fcntl.ioctl(fd, TUNSETIFF, ifr)
            return fd
        except PermissionError:
            print("[FATAL] Permission denied accessing /dev/net/tun. Must run with sudo / root privileges!", file=sys.stderr)
            sys.exit(1)

    def run(self):
        """Main event loop multiplexing TAP frames and SDR RF packets."""
        self.running = True
        print("[ORCHESTRATOR] Tactical Mesh Orchestrator active. Bridging batman-adv <-> LoRa SDR...")

        last_stat_time = time.time()

        try:
            while self.running:
                # High-efficiency non-blocking event multiplexing
                readable, _, _ = select.select([self.tap_fd, self.sock_rx], [], [], 0.5)

                for fd in readable:
                    # ----------------------------------------------------------
                    # PATH 1: OUTGOING (Host / batman-adv -> SDR Transmitter)
                    # ----------------------------------------------------------
                    if fd == self.tap_fd:
                        try:
                            # Read raw Layer 2 Ethernet frame from batman-adv
                            raw_frame = os.read(self.tap_fd, 2048)
                            if raw_frame:
                                # Pack into tactical framing and forward to peer or GNU Radio
                                radio_packet = self.framing.pack(raw_frame)
                                if self.peer:
                                    self.sock_tx.sendto(radio_packet, (self.peer, self.ipc_rx_port))
                                elif self.dry_run:
                                    # Dry-run loopback directly into Rx socket for testing
                                    self.sock_tx.sendto(radio_packet, ("127.0.0.1", self.ipc_rx_port))
                                else:
                                    self.sock_tx.sendto(radio_packet, ("127.0.0.1", self.ipc_tx_port))
                                self.tx_count += 1
                        except BlockingIOError:
                            pass
                        except Exception as e:
                            print(f"[WARN] TAP Read Error: {e}", file=sys.stderr)

                    # ----------------------------------------------------------
                    # PATH 2: INCOMING (SDR Receiver -> Host / batman-adv)
                    # ----------------------------------------------------------
                    elif fd == self.sock_rx:
                        try:
                            # Receive decoded LoRa PDU from GNU Radio or peer node
                            data, _ = self.sock_rx.recvfrom(2048)
                            if data:
                                result = self.framing.unpack(data)
                                if result:
                                    seq, frag_idx, total_frags, eth_frame = result
                                    # Inject valid Layer 2 Ethernet frame directly into kernel TAP
                                    os.write(self.tap_fd, eth_frame)
                                    self.rx_count += 1
                                else:
                                    self.crc_errs += 1
                        except BlockingIOError:
                            pass
                        except Exception as e:
                            print(f"[WARN] SDR Ingest Error: {e}", file=sys.stderr)

                # Periodic Heartbeat & Link Telemetry
                now = time.time()
                if now - last_stat_time >= 5.0:
                    print(f"[STATUS] TX Frames: {self.tx_count} | RX Frames: {self.rx_count} | CRC Drops: {self.crc_errs}")
                    last_stat_time = now

        except KeyboardInterrupt:
            print("\n[ORCHESTRATOR] Termination signal received.")
        finally:
            self.shutdown()

    def shutdown(self):
        """Clean shutdown of file descriptors and sockets."""
        self.running = False
        print("[ORCHESTRATOR] Closing TAP file descriptor and sockets...")
        try:
            os.close(self.tap_fd)
        except Exception:
            pass
        self.sock_tx.close()
        self.sock_rx.close()
        print("[ORCHESTRATOR] Clean shutdown complete.")


def main():
    parser = argparse.ArgumentParser(description="Tactical SDR Mesh Orchestrator (TSM-Net SG)")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--peer", default=None, help="Remote peer IP address for direct node-to-node mesh tunneling")
    parser.add_argument("--dry-run", action="store_true", help="Simulate RF link via local loopback socket")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"[ERROR] Config file '{args.config}' not found.", file=sys.stderr)
        sys.exit(1)

    orchestrator = TacticalMeshOrchestrator(args.config, dry_run=args.dry_run, peer=args.peer)

    def sig_handler(sig, frame):
        orchestrator.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    orchestrator.run()


if __name__ == "__main__":
    main()
