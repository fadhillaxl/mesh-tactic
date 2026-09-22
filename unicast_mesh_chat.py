#!/usr/bin/env python3
"""
Tactical SDR Point-to-Point (Unicast) & Multi-hop Relay Mesh Chat
Designed for Raspberry Pi, Armbian SBC, and Mac with Pluto+ SDR.

Compatible with:
1. Direct Pluto+ SDR hardware (built-in 915.000 MHz 2-FSK PHY).
2. GNU Radio / gr-lora_sdr via UDP Socket PDU.

Header Format (12 Bytes Header + 2 Bytes CRC16):
[Magic 2B: 0xD354] [Flags 1B] [TTL 1B] [Src ID 2B] [Dst ID 2B] [Msg ID 2B] [Len 2B] [Payload N B] [CRC16 2B]
"""

import os
import sys
import time
import struct
import socket
import threading
import argparse
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import numpy as np

# Ensure src/ is in sys.path
REPO_ROOT = Path(__file__).resolve().parent
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tsm.modem.constants import (
    CARRIER_FREQ,
    SAMPLE_RATE,
    FREQ_DEV,
    TX_AMPLITUDE,
    SYNC_WORD,
    RX_BUF_SIZE,
    TX_BUF_SIZE,
    DECIMATION,
    SPS_DEC,
)
from tsm.modem.dsp import (
    build_sync_template,
    modulate_2fsk,
    demodulate_2fsk,
)

try:
    import iio
except ImportError:
    iio = None

# ==============================================================================
# PROTOCOL DEFINITION & CONSTANTS
# ==============================================================================

MAGIC_BYTES = 0xD354   # 2 Bytes Sync / Magic Marker (0xD354)
BROADCAST_ID = 0xFFFF  # 0xFFFF represents global broadcast to all nodes

# Packet Type Flags (Bitmask)
FLAG_UNICAST   = 0x01  # Direct point-to-point message
FLAG_BROADCAST = 0x02  # Global broadcast message
FLAG_ACK       = 0x04  # Acknowledgment packet
FLAG_RELAYED   = 0x08  # Multi-hop forwarded packet

# Header Binary Format (Network Byte Order / Big-Endian):
# ! H (Magic: 2B) B (Flags: 1B) B (TTL: 1B) H (Src: 2B) H (Dst: 2B) H (MsgID: 2B) H (Len: 2B)
HEADER_FORMAT = "!HBBHHHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 12 Bytes
CRC_SIZE = 2                                  # 2 Bytes CRC-16
MAX_PAYLOAD_SIZE = 220                        # Max text payload bytes


def crc16_ccitt(data: bytes) -> int:
    """Computes 16-bit CRC-CCITT (Polynomial: 0x1021, Initial: 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ==============================================================================
# PACKET MODEL & ENCAPSULATION
# ==============================================================================

class MeshPacket:
    """
    Tactical Mesh Network Packet with Binary Framing:
    +------------+----------+---------+------------+------------+------------+------------+---------------+----------+
    | Magic (2B) | Flag(1B) | TTL(1B) | Src ID(2B) | Dst ID(2B) | Msg ID(2B) | Length(2B) | Payload (N B) | CRC (2B) |
    +------------+----------+---------+------------+------------+------------+------------+---------------+----------+
    """

    def __init__(
        self,
        src_id: int,
        dst_id: int,
        msg_id: int,
        payload: str,
        flags: int = FLAG_UNICAST,
        ttl: int = 3,
    ):
        self.src_id = src_id & 0xFFFF
        self.dst_id = dst_id & 0xFFFF
        self.msg_id = msg_id & 0xFFFF
        self.flags = flags & 0xFF
        self.ttl = ttl & 0xFF
        self.payload = payload

    def pack(self) -> bytes:
        """Serializes the packet into binary format with CRC16 checksum."""
        payload_bytes = self.payload.encode("utf-8")[:MAX_PAYLOAD_SIZE]
        payload_len = len(payload_bytes)

        header = struct.pack(
            HEADER_FORMAT,
            MAGIC_BYTES,
            self.flags,
            self.ttl,
            self.src_id,
            self.dst_id,
            self.msg_id,
            payload_len,
        )

        body = header + payload_bytes
        crc = crc16_ccitt(body)
        return body + struct.pack("!H", crc)

    @classmethod
    def unpack(cls, raw: bytes) -> Optional["MeshPacket"]:
        """Parses and validates raw binary frame. Returns MeshPacket or None if corrupt."""
        if len(raw) < HEADER_SIZE + CRC_SIZE:
            return None

        expected_crc = struct.unpack("!H", raw[-CRC_SIZE:])[0]
        calculated_crc = crc16_ccitt(raw[:-CRC_SIZE])
        if expected_crc != calculated_crc:
            return None

        magic, flags, ttl, src_id, dst_id, msg_id, payload_len = struct.unpack(
            HEADER_FORMAT, raw[:HEADER_SIZE]
        )

        if magic != MAGIC_BYTES:
            return None

        payload_bytes = raw[HEADER_SIZE : HEADER_SIZE + payload_len]
        try:
            payload = payload_bytes.decode("utf-8", errors="replace")
        except Exception:
            payload = "<binary/malformed>"

        return cls(
            src_id=src_id,
            dst_id=dst_id,
            msg_id=msg_id,
            payload=payload,
            flags=flags,
            ttl=ttl,
        )


# ==============================================================================
# UNICAST CHAT ENGINE (DIRECT PLUTO SDR & SOCKET PDU MODES)
# ==============================================================================

class UnicastSDRChat:
    def __init__(
        self,
        node_id: int,
        target_id: int = BROADCAST_ID,
        uri: Optional[str] = None,
        use_socket: bool = False,
        tx_port: int = 52001,
        rx_port: int = 52002,
        remote_host: str = "127.0.0.1",
        rx_gain: float = 65.0,
        tx_atten: float = 0.0,
        enable_relay: bool = True,
    ):
        self.node_id = node_id & 0xFFFF
        self.target_id = target_id & 0xFFFF
        self.uri = uri
        self.use_socket = use_socket
        self.tx_port = tx_port
        self.rx_port = rx_port
        self.remote_host = remote_host
        self.rx_gain = rx_gain
        self.tx_atten = tx_atten
        self.enable_relay = enable_relay
        self.running = False
        self.seq_counter = 0
        self.seen_messages: Dict[Tuple[int, int], float] = {}
        self.lock = threading.Lock()
        self.last_tx_time = 0.0

        # Precompute sync template for 2-FSK PHY
        self.sync_upsampled, self.sync_len = build_sync_template()

        # Initialize Transport Backend
        if self.use_socket:
            self._init_socket()
        else:
            self._init_pluto_sdr()

    def _init_socket(self):
        """Initializes UDP Socket interface for GNU Radio gr-lora_sdr Socket PDU."""
        print(f"[*] Transport: UDP Socket PDU (RX Port {self.rx_port} | TX Port {self.tx_port})")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.rx_port))
        self.sdr_mode = "socket"

    def _init_pluto_sdr(self):
        """Initializes direct ADALM-PLUTO SDR hardware."""
        if iio is None:
            raise RuntimeError("libiio python binding is not available. Please install python3-libiio or use --socket.")

        print(f"[*] Transport: Direct ADALM-PLUTO SDR at: {self.uri} ...")
        self.ctx = iio.Context(self.uri)
        self.phy = self.ctx.find_device("ad9361-phy")
        self.rx_dev = self.ctx.find_device("cf-ad9361-lpc")
        self.tx_dev = self.ctx.find_device("cf-ad9361-dds-core-lpc")

        if not self.phy or not self.rx_dev or not self.tx_dev:
            raise RuntimeError("Required AD9361 devices not found on SDR context.")

        # Configure LO Frequencies
        self.phy.find_channel("altvoltage1", True).attrs["frequency"].value = str(CARRIER_FREQ)
        self.phy.find_channel("altvoltage0", True).attrs["frequency"].value = str(CARRIER_FREQ)

        # Configure Baseband Rate & Analog Filters
        self.phy.find_channel("voltage0", False).attrs["sampling_frequency"].value = str(SAMPLE_RATE)
        self.phy.find_channel("voltage0", True).attrs["sampling_frequency"].value = str(SAMPLE_RATE)
        self.phy.find_channel("voltage0", False).attrs["rf_bandwidth"].value = "1000000"
        self.phy.find_channel("voltage0", True).attrs["rf_bandwidth"].value = "1000000"

        # Configure Gains
        self.phy.find_channel("voltage0", False).attrs["gain_control_mode"].value = "manual"
        self.phy.find_channel("voltage0", False).attrs["hardwaregain"].value = f"{self.rx_gain:.1f}"
        self.phy.find_channel("voltage0", True).attrs["hardwaregain"].value = f"{self.tx_atten:.1f}"

        # Enable I & Q Channels
        for ch in self.rx_dev.channels:
            ch.enabled = ch.id in ("voltage0", "voltage1")
        for ch in self.tx_dev.channels:
            ch.enabled = ch.id in ("voltage0", "voltage1")

        # Disable internal DDS tones
        for ch in self.tx_dev.channels:
            if "raw" in ch.attrs:
                ch.attrs["raw"].value = "0"

        self.rx_buf = iio.Buffer(self.rx_dev, RX_BUF_SIZE, False)
        self.tx_buf = iio.Buffer(self.tx_dev, TX_BUF_SIZE, False)
        print(f"[*] Radio Hardware Ready: {CARRIER_FREQ/1e6:.3f} MHz | Rate: {SAMPLE_RATE/1e6:.2f} MSps")
        self.sdr_mode = "pluto"

    def _next_msg_id(self) -> int:
        with self.lock:
            self.seq_counter = (self.seq_counter + 1) & 0xFFFF
            return self.seq_counter

    def _transmit_raw(self, wire_bytes: bytes):
        """Transmits raw binary frame over active transport (SDR or Socket)."""
        if self.sdr_mode == "socket":
            self.sock.sendto(wire_bytes, (self.remote_host, self.tx_port))
        else:
            iq = modulate_2fsk(wire_bytes)
            num_samples = len(iq)
            if num_samples > TX_BUF_SIZE:
                print(f"\n[WARN] Packet too long ({num_samples} samples > {TX_BUF_SIZE})", file=sys.stderr)
                return

            padded = np.zeros((TX_BUF_SIZE, 2), dtype=np.int16)
            padded[:num_samples] = iq
            raw_dma = bytearray(padded.tobytes())

            with self.lock:
                self.last_tx_time = time.time()
                for _ in range(3):
                    self.tx_buf.write(raw_dma)
                    self.tx_buf.push()
                    time.sleep((num_samples / SAMPLE_RATE) + 0.05)

    def send_message(self, text: str, dst_id: Optional[int] = None):
        """Packages text into binary MeshPacket and transmits over the link."""
        target = self.target_id if dst_id is None else dst_id
        flags = FLAG_BROADCAST if target == BROADCAST_ID else FLAG_UNICAST
        msg_id = self._next_msg_id()

        packet = MeshPacket(
            src_id=self.node_id,
            dst_id=target,
            msg_id=msg_id,
            payload=text,
            flags=flags,
            ttl=3,
        )

        wire_bytes = packet.pack()
        self.seen_messages[(self.node_id, msg_id)] = time.time()
        self._transmit_raw(wire_bytes)

        now_str = time.strftime("%H:%M:%S")
        target_str = "BROADCAST" if target == BROADCAST_ID else f"Node 0x{target:04X}"
        print(f"\r\033[K[{now_str}] \033[93m[TX -> {target_str}]\033[0m: {text}", flush=True)

    def _forward_relay(self, packet: MeshPacket):
        """Multi-hop Relay: Decrements TTL and re-broadcasts packet over RF."""
        if not self.enable_relay or packet.ttl <= 1:
            return

        packet.ttl -= 1
        packet.flags |= FLAG_RELAYED
        time.sleep(0.04)  # Small backoff delay to avoid collision
        self._transmit_raw(packet.pack())
        print(f"\r\033[K\033[90m[*] [RELAY] Forwarded Msg #{packet.msg_id:04X} to Node 0x{packet.dst_id:04X} (TTL left: {packet.ttl})\033[0m", flush=True)
        print(self._get_prompt(), end="", flush=True)

    def _process_incoming_packet(self, packet: MeshPacket, corr: Optional[float] = None):
        """Applies Unicast vs Broadcast reception filtering logic."""
        now = time.time()
        key = (packet.src_id, packet.msg_id)
        if key in self.seen_messages and (now - self.seen_messages[key]) < 5.0:
            return
        self.seen_messages[key] = now

        # Housekeep cache
        if len(self.seen_messages) > 100:
            self.seen_messages = {k: v for k, v in self.seen_messages.items() if now - v < 10.0}

        # Ignore self transmissions
        if packet.src_id == self.node_id:
            return

        now_str = time.strftime("%H:%M:%S")
        corr_info = f" | {corr:.2f}" if corr is not None else ""

        # ======================================================================
        # RECEPTION FILTERING LOGIC
        # ======================================================================
        if packet.dst_id == self.node_id:
            # 1. DIRECT UNICAST FOR MY NODE
            print(
                f"\r\033[K[{now_str}] \033[92m[RX UNICAST from Node 0x{packet.src_id:04X}{corr_info}]\033[0m: {packet.payload}",
                flush=True,
            )
            print(self._get_prompt(), end="", flush=True)

        elif packet.dst_id == BROADCAST_ID:
            # 2. GLOBAL BROADCAST
            print(
                f"\r\033[K[{now_str}] \033[96m[RX BROADCAST from Node 0x{packet.src_id:04X}{corr_info}]\033[0m: {packet.payload}",
                flush=True,
            )
            print(self._get_prompt(), end="", flush=True)
            self._forward_relay(packet)

        else:
            # 3. PACKET FOR ANOTHER NODE (NOT ME)
            # Silently drop from terminal screen, but forward if multi-hop relay is enabled
            self._forward_relay(packet)

    def _rx_worker_socket(self):
        """RX worker for UDP Socket PDU mode."""
        while self.running:
            try:
                raw_bytes, _ = self.sock.recvfrom(2048)
                if not raw_bytes:
                    continue
                packet = MeshPacket.unpack(raw_bytes)
                if packet:
                    self._process_incoming_packet(packet)
            except Exception:
                if self.running:
                    time.sleep(0.05)

    def _rx_worker_pluto(self):
        """Continuous RX worker capturing baseband I/Q from Pluto SDR."""
        prev_tail = np.zeros((RX_BUF_SIZE // 2, 2), dtype=np.int16)

        while self.running:
            try:
                # Discard self-spillover during active transmission
                if time.time() - self.last_tx_time < 0.10:
                    self.rx_buf.refill()
                    continue

                self.rx_buf.refill()
                raw_bytes = self.rx_buf.read()
                raw_samples = np.frombuffer(raw_bytes, dtype=np.int16).reshape(-1, 2)

                combined = np.vstack((prev_tail, raw_samples))
                prev_tail = raw_samples[-(RX_BUF_SIZE // 2) :]

                result = demodulate_2fsk(combined, self.sync_upsampled, self.sync_len, threshold=0.38)
                if result is not None:
                    payload_bytes, corr = result
                    packet = MeshPacket.unpack(payload_bytes)
                    if packet is not None:
                        self._process_incoming_packet(packet, corr=corr)

            except Exception:
                if self.running:
                    time.sleep(0.05)

    def _get_prompt(self) -> str:
        tgt_str = "ALL" if self.target_id == BROADCAST_ID else f"0x{self.target_id:04X}"
        return f"\033[1;34m[Node 0x{self.node_id:04X} -> {tgt_str}]\033[0m >>> "

    def run_cli(self):
        self.running = True
        worker = self._rx_worker_socket if self.sdr_mode == "socket" else self._rx_worker_pluto
        threading.Thread(target=worker, daemon=True).start()

        print("=" * 68)
        print(f" TACTICAL SDR POINT-TO-POINT (UNICAST) CHAT")
        print(f" My Node ID:       \033[92m0x{self.node_id:04X} ({self.node_id})\033[0m")
        print(f" Default Target:   \033[93m{'BROADCAST' if self.target_id == BROADCAST_ID else hex(self.target_id)}\033[0m")
        print(f" Multi-Hop Relay:  \033[95m{'ENABLED' if self.enable_relay else 'DISABLED'}\033[0m")
        if self.sdr_mode == "socket":
            print(f" Transport Mode:   UDP Socket PDU (RX Port {self.rx_port} | TX Port {self.tx_port})")
        else:
            print(f" Transport Mode:   Direct Pluto+ SDR at {self.uri} (915.000 MHz)")
        print("=" * 68)
        print("Commands:")
        print("  /to <node_id>   : Switch target unicast destination (e.g. '/to 2' or '/to 0x0002')")
        print("  /all            : Switch back to broadcast mode (0xFFFF)")
        print("  /relay on|off   : Enable or disable multi-hop packet forwarding")
        print("  /exit           : Quit application\n")

        try:
            while self.running:
                try:
                    if sys.stdin.isatty():
                        msg = input(self._get_prompt()).strip()
                    else:
                        line = sys.stdin.readline()
                        if not line:
                            time.sleep(0.2)
                            continue
                        msg = line.strip()
                except (EOFError, KeyboardInterrupt):
                    break

                if not msg:
                    continue

                if msg.lower() in ("/exit", "/quit", "exit", "quit"):
                    break
                elif msg.startswith("/to"):
                    parts = msg.split(maxsplit=1)
                    if len(parts) > 1:
                        raw_id = parts[1].strip()
                        try:
                            new_dst = int(raw_id, 16) if raw_id.startswith("0x") or raw_id.startswith("0X") else int(raw_id)
                            self.target_id = new_dst & 0xFFFF
                            print(f"[*] Target destination updated to: \033[93m0x{self.target_id:04X}\033[0m")
                        except ValueError:
                            print(f"[ERROR] Invalid Node ID '{raw_id}'. Use decimal (e.g. 2) or hex (e.g. 0x0002).")
                    else:
                        print(f"[*] Current target: 0x{self.target_id:04X}")
                    continue
                elif msg.lower() in ("/all", "/broadcast"):
                    self.target_id = BROADCAST_ID
                    print("[*] Target destination set to: \033[96mBROADCAST (0xFFFF)\033[0m")
                    continue
                elif msg.startswith("/relay"):
                    parts = msg.split(maxsplit=1)
                    if len(parts) > 1:
                        arg = parts[1].strip().lower()
                        self.enable_relay = arg in ("on", "1", "true", "yes")
                    print(f"[*] Multi-Hop Relay is now: {'ENABLED' if self.enable_relay else 'DISABLED'}")
                    continue

                self.send_message(msg)

        finally:
            self.running = False
            if self.sdr_mode == "socket":
                self.sock.close()
            print("\n[*] Tactical Unicast Chat terminated. 73!")


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================

def get_default_uri() -> str:
    nodename = os.uname().nodename.lower()
    sysname = os.uname().sysname.lower()
    if "2w" in nodename or "zero" in nodename or "aml" in nodename:
        return "ip:192.168.99.240"
    elif "darwin" in sysname:
        return "ip:192.168.2.10"
    return "usb:1.3.5"


def main():
    parser = argparse.ArgumentParser(description="Tactical SDR Point-to-Point Unicast & Multi-Hop Chat")
    parser.add_argument("--id", type=lambda x: int(x, 0), default=None, help="My Node ID (e.g. 1, 2, 0x0001, 0x0002)")
    parser.add_argument("--node", default="", help="Node alias ('mac'=1, 'pi5'=2, 'aml'=3)")
    parser.add_argument("--uri", default="", help="Pluto SDR URI (e.g. usb:1.3.5 or ip:192.168.99.240)")
    parser.add_argument("--to", type=lambda x: int(x, 0), default=BROADCAST_ID, help="Target Node ID (default: 0xFFFF Broadcast)")
    parser.add_argument("--socket", action="store_true", help="Use UDP Socket PDU (for GNU Radio gr-lora_sdr) instead of direct Pluto SDR")
    parser.add_argument("--tx-port", type=int, default=52001, help="UDP Egress port to SDR modulator (default: 52001)")
    parser.add_argument("--rx-port", type=int, default=52002, help="UDP Ingest port from SDR demodulator (default: 52002)")
    parser.add_argument("--host", default="127.0.0.1", help="Target SDR host IP (default: 127.0.0.1)")
    parser.add_argument("--rx-gain", type=float, default=65.0, help="RX Gain dB (0-73)")
    parser.add_argument("--tx-atten", type=float, default=0.0, help="TX Attenuation dB (-89 to 0)")
    parser.add_argument("--no-relay", action="store_true", help="Disable multi-hop packet forwarding")
    args = parser.parse_args()

    node_str = (args.node or "").lower()
    uri = args.uri
    node_id = args.id

    if uri and ("usbmodem" in uri or "/dev/tty" in uri):
        uri = "ip:192.168.2.10"

    # Auto-detect profile from node argument or hostname
    if node_str:
        if "mac" in node_str:
            node_id = node_id or 0x0001
            uri = uri or "ip:192.168.2.10"
        elif "pi5" in node_str or node_str in ("1", "node1"):
            node_id = node_id or 0x0002
            uri = uri or "usb:1.3.5"
        elif "aml" in node_str or "2w" in node_str or node_str in ("2", "node2"):
            node_id = node_id or 0x0003
            uri = uri or "ip:192.168.99.240"
    else:
        # Fallback detection from OS hostname
        nodename = os.uname().nodename.lower()
        if "pi5" in nodename:
            node_id = node_id or 0x0002
        elif "aml" in nodename or "2w" in nodename:
            node_id = node_id or 0x0003
        elif "darwin" in sys.platform.lower() or "mac" in nodename:
            node_id = node_id or 0x0001
        else:
            node_id = node_id or 0x0001

    if not uri and not args.socket:
        uri = get_default_uri()

    chat = UnicastSDRChat(
        node_id=node_id,
        target_id=args.to,
        uri=uri,
        use_socket=args.socket,
        tx_port=args.tx_port,
        rx_port=args.rx_port,
        remote_host=args.host,
        rx_gain=args.rx_gain,
        tx_atten=args.tx_atten,
        enable_relay=not args.no_relay,
    )
    chat.run_cli()


if __name__ == "__main__":
    main()
