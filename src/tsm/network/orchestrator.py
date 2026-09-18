"""
Tactical Mesh Orchestrator (batman-adv TAP <-> SDR Baseband Bridge).
Coordinates kernel network frames, tactical encapsulation, fragmenting, and loopback suppression.
"""

import os
import sys
import struct
import socket
import select
import signal
import time
import argparse
from typing import Dict, Optional, Tuple

from ..common.config import load_config, Config
from ..common.framing import TacticalFraming, MAX_CHUNK_PAYLOAD
from .tap_bridge import LinuxTapBridge


class TacticalMeshOrchestrator:
    """Orchestrates bidirectional bridging between Kernel TAP and Baseband Modem."""

    def __init__(self, config: Config, dry_run: bool = False, peer: Optional[str] = None):
        self.cfg = config
        self.dry_run = dry_run
        self.peer = peer
        self.framing = TacticalFraming()
        self.running = False

        # Metrics
        self.tx_frames = 0
        self.rx_frames = 0
        self.crc_drops = 0
        self.echo_drops = 0

        # Fragment reassembly tracking: {seq: {frag_idx: bytes, 'total': int, 'ts': float}}
        self.rx_fragments: Dict[int, Dict] = {}

        # 1. Initialize TAP Bridge (or mock in dry-run if not root)
        self.tap: Optional[LinuxTapBridge] = None
        self.own_mac: Optional[bytes] = None
        if not self.dry_run:
            self.tap = LinuxTapBridge(
                dev_name=self.cfg.network.tap_device,
                mtu=self.cfg.network.mtu,
            )
            self.own_mac = self.tap.get_mac_address()
            if self.own_mac:
                mac_formatted = ":".join(f"{b:02x}" for b in self.own_mac)
                print(f"[ORCH] Anti-Loopback Filter Active. Local TAP MAC: {mac_formatted}")
        else:
            print("[ORCH] Running in DRY-RUN mode (virtual software loopback).")

        # 2. Setup UDP IPC Sockets with Baseband Modem
        self.sock_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_rx.bind(("0.0.0.0", self.cfg.network.ipc_rx_port))
        self.sock_rx.setblocking(False)

        print(f"[ORCH] IPC Socket Egress -> 127.0.0.1:{self.cfg.network.ipc_tx_port}")
        print(f"[ORCH] IPC Socket Ingress <- 0.0.0.0:{self.cfg.network.ipc_rx_port}")

    def handle_tap_egress(self):
        """Reads raw Ethernet frame from TAP, encapsulates, and sends to Modem."""
        if not self.tap:
            return

        frame = self.tap.read()
        if not frame:
            return

        eth_type = struct.unpack("!H", frame[12:14])[0] if len(frame) >= 14 else 0
        print(f"[TAP-TX] Outgoing Frame: {len(frame)}B | EtherType: 0x{eth_type:04x}")

        chunks = self.framing.fragment(frame, MAX_CHUNK_PAYLOAD)
        total_chunks = len(chunks)

        for idx, chunk in enumerate(chunks):
            packet = self.framing.pack(chunk, frag_idx=idx, total_frags=total_chunks)
            if self.peer:
                self.sock_tx.sendto(packet, (self.peer, self.cfg.network.ipc_rx_port))
            elif self.dry_run:
                # Loopback directly to RX socket
                self.sock_rx.sendto(packet, ("127.0.0.1", self.cfg.network.ipc_rx_port))
            else:
                self.sock_tx.sendto(packet, ("127.0.0.1", self.cfg.network.ipc_tx_port))
            self.tx_frames += 1

    def handle_modem_ingress(self):
        """Receives demodulated frame from Modem, validates CRC, and injects into TAP."""
        try:
            raw, _ = self.sock_rx.recvfrom(2048)
        except BlockingIOError:
            return

        res = self.framing.unpack(raw)
        if res is None:
            self.crc_drops += 1
            return

        seq, frag_idx, total_frags, payload = res

        # Reassemble fragments
        if total_frags > 1:
            now = time.time()
            if seq not in self.rx_fragments:
                self.rx_fragments[seq] = {"frags": {}, "total": total_frags, "ts": now}
            self.rx_fragments[seq]["frags"][frag_idx] = payload

            if len(self.rx_fragments[seq]["frags"]) == total_frags:
                full_frame = b"".join(self.rx_fragments[seq]["frags"][i] for i in range(total_frags))
                del self.rx_fragments[seq]
                self._inject_frame(full_frame)
        else:
            self._inject_frame(payload)

    def _inject_frame(self, frame: bytes):
        """Filters local echo and injects frame into Kernel TAP."""
        if len(frame) >= 14:
            src_mac = frame[6:12]
            eth_type = struct.unpack("!H", frame[12:14])[0]

            # Anti-loopback suppression: drop self-transmissions
            if self.own_mac and src_mac == self.own_mac:
                self.echo_drops += 1
                return

            print(f"[TAP-RX] Incoming Frame Injected to Kernel: {len(frame)}B | EtherType: 0x{eth_type:04x}")

        self.rx_frames += 1
        if self.tap:
            try:
                self.tap.write(frame)
            except Exception as e:
                print(f"[ERROR] Failed to inject frame to TAP: {e}", file=sys.stderr)

    def cleanup_stale_fragments(self):
        now = time.time()
        stale_seqs = [s for s, data in self.rx_fragments.items() if now - data["ts"] > 5.0]
        for s in stale_seqs:
            del self.rx_fragments[s]

    def run(self):
        """Event loop multiplexing TAP descriptor and UDP sockets."""
        self.running = True

        def _sig_handler(sig, frame):
            print("\n[ORCH] Interrupted. Terminating gracefully...")
            self.running = False

        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

        print("[ORCH] Orchestrator event loop running. Press Ctrl+C to stop.")
        last_stat_time = time.time()

        descriptors = [self.sock_rx]
        if self.tap and self.tap.fd is not None:
            descriptors.append(self.tap.fd)

        try:
            while self.running:
                r_ready, _, _ = select.select(descriptors, [], [], 0.05)
                for fd in r_ready:
                    if fd == self.sock_rx:
                        self.handle_modem_ingress()
                    elif self.tap and fd == self.tap.fd:
                        self.handle_tap_egress()

                if self.dry_run:
                    time.sleep(0.01)

                now = time.time()
                if now - last_stat_time >= 15.0:
                    self.cleanup_stale_fragments()
                    print(f"[STATUS] TX Frames: {self.tx_frames} | RX Frames: {self.rx_frames} | CRC Drops: {self.crc_drops} | Echo Drops: {self.echo_drops}", flush=True)
                    last_stat_time = now

        finally:
            if self.tap:
                self.tap.close()
            self.sock_tx.close()
            self.sock_rx.close()
            print("[ORCH] Clean shutdown complete.")


def main():
    parser = argparse.ArgumentParser(description="TSM-Net SG Tactical Mesh Orchestrator")
    parser.add_argument("--config", default="config/config.yaml", help="Path to config.yaml")
    parser.add_argument("--peer", default=None, help="Remote peer IP address for direct node-to-node mesh tunneling")
    parser.add_argument("--dry-run", action="store_true", help="Run in software loopback dry-run mode")
    args = parser.parse_args()

    cfg = load_config(args.config)
    orch = TacticalMeshOrchestrator(cfg, dry_run=args.dry_run, peer=args.peer)
    orch.run()


if __name__ == "__main__":
    main()
