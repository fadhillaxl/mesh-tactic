#!/usr/bin/env python3
"""
Point-to-Point (Unicast) & Tactical Mesh Chat for SBC (Raspberry Pi) & Pluto+ SDR.
Compatible with gr-lora_sdr, GNU Radio Socket PDU, and custom SDR PHY pipelines.

Features:
1. Binary Custom Network Header (Magic, Flags, TTL, Src, Dst, MsgID, Length, CRC16).
2. Unicast Filtering: Displays only packets destined for My_Node_ID or Broadcast.
3. Multi-hop Relay: Automatically decrements TTL and re-transmits packets intended for other nodes.
4. Flexible Transport: Standard Python UDP socket (compatible with GNU Radio UDP/Socket PDU)
   and optional ZeroMQ PUB/SUB support.
5. Interactive CLI: Switch destination with '/to <id>', broadcast with '/all', toggle '/relay'.
"""

import os
import sys
import time
import struct
import socket
import threading
import argparse
from typing import Optional, Tuple, Dict, Any

# ==============================================================================
# PROTOCOL DEFINITION & CONSTANTS
# ==============================================================================

MAGIC_BYTES = 0xD354  # 2 Bytes Sync / Magic Marker (0xD354)
BROADCAST_ID = 0xFFFF # 0xFFFF represents global broadcast to all nodes

# Packet Type Flags (Bitmask)
FLAG_UNICAST   = 0x01 # Direct point-to-point message
FLAG_BROADCAST = 0x02 # Global broadcast message
FLAG_ACK       = 0x04 # Acknowledgment packet
FLAG_RELAYED   = 0x08 # Multi-hop forwarded packet

# Header Binary Format (Network Byte Order / Big-Endian):
# ! H (Magic: 2B) B (Flags: 1B) B (TTL: 1B) H (Src: 2B) H (Dst: 2B) H (MsgID: 2B) H (Len: 2B)
HEADER_FORMAT = "!HBBHHHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 12 Bytes
CRC_SIZE = 2                                  # 2 Bytes CRC-16
MAX_PAYLOAD_SIZE = 240                        # Max text payload bytes (LoRa / SDR friendly)


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
    +---------------+------------+----------+-------------+------------------+----------------+----------------+----------------------+--------------------+
    | Magic (2B)    | Flags (1B) | TTL (1B) | Src ID (2B) | Dst ID (2B)      | Msg ID (2B)    | Length (2B)    | Payload Data (N B)   | CRC-16 (2B)        |
    +---------------+------------+----------+-------------+------------------+----------------+----------------+----------------------+--------------------+
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

        # Build 12-byte header
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
        """
        Parses and validates raw binary frame.
        Returns MeshPacket instance or None if corrupted / invalid.
        """
        if len(raw) < HEADER_SIZE + CRC_SIZE:
            return None

        # Verify CRC
        expected_crc = struct.unpack("!H", raw[-CRC_SIZE:])[0]
        calculated_crc = crc16_ccitt(raw[:-CRC_SIZE])
        if expected_crc != calculated_crc:
            return None

        # Unpack Header
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

        packet = cls(
            src_id=src_id,
            dst_id=dst_id,
            msg_id=msg_id,
            payload=payload,
            flags=flags,
            ttl=ttl,
        )
        return packet


# ==============================================================================
# UNICAST SDR MESH CHAT ENGINE
# ==============================================================================

class UnicastSDRChat:
    """
    Point-to-Point Unicast & Multi-hop Relay SDR Chat Client.
    Communicates with GNU Radio (gr-lora_sdr) or Pluto SDR via UDP socket.
    """

    def __init__(
        self,
        node_id: int,
        tx_port: int = 52001,
        rx_port: int = 52002,
        remote_host: str = "127.0.0.1",
        enable_relay: bool = True,
    ):
        self.node_id = node_id & 0xFFFF
        self.target_id = BROADCAST_ID # Default to broadcast unless user sets destination
        self.tx_port = tx_port
        self.rx_port = rx_port
        self.remote_host = remote_host
        self.enable_relay = enable_relay
        self.running = False
        self.seq_counter = 0
        self.seen_messages: Dict[Tuple[int, int], float] = {} # Deduplication cache: (src_id, msg_id) -> timestamp
        self.lock = threading.Lock()

        # Setup UDP socket for SDR TX / RX interface (GNU Radio Socket PDU / gr-lora_sdr)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.rx_port))

    def _next_msg_id(self) -> int:
        with self.lock:
            self.seq_counter = (self.seq_counter + 1) & 0xFFFF
            return self.seq_counter

    def send_message(self, text: str, dst_id: Optional[int] = None):
        """Encapsulates text into binary MeshPacket and transmits over socket."""
        target = self.target_id if dst_id is None else dst_id
        flags = FLAG_BROADCAST if target == BROADCAST_ID else FLAG_UNICAST
        msg_id = self._next_msg_id()

        packet = MeshPacket(
            src_id=self.node_id,
            dst_id=target,
            msg_id=msg_id,
            payload=text,
            flags=flags,
            ttl=3, # 3 hops default
        )

        wire_data = packet.pack()
        self.sock.sendto(wire_data, (self.remote_host, self.tx_port))

        # Record into deduplication cache so we don't process our own echoed transmissions
        self.seen_messages[(self.node_id, msg_id)] = time.time()

        now_str = time.strftime("%H:%M:%S")
        target_str = "BROADCAST" if target == BROADCAST_ID else f"Node {target:04X}"
        print(f"\r\033[K[{now_str}] \033[93m[TX -> {target_str}]\033[0m: {text}", flush=True)

    def _forward_relay(self, packet: MeshPacket):
        """Multi-hop Relay: Decrements TTL and re-broadcasts frame to downstream nodes."""
        if not self.enable_relay or packet.ttl <= 1:
            return

        packet.ttl -= 1
        packet.flags |= FLAG_RELAYED
        wire_data = packet.pack()

        # Slight jitter to minimize collision in CSMA/ALOHA over-the-air link
        time.sleep(0.04)
        self.sock.sendto(wire_data, (self.remote_host, self.tx_port))
        print(f"\r\033[K\033[90m[*] [RELAY] Forwarded Msg #{packet.msg_id:04X} from Node {packet.src_id:04X} -> Node {packet.dst_id:04X} (TTL: {packet.ttl})\033[0m", flush=True)
        print(self._get_prompt(), end="", flush=True)

    def _rx_listener(self):
        """Continuous background thread listening for SDR demodulated frames."""
        while self.running:
            try:
                raw_bytes, addr = self.sock.recvfrom(2048)
                if not raw_bytes:
                    continue

                packet = MeshPacket.unpack(raw_bytes)
                if packet is None:
                    continue # Corrupted frame (CRC failed or bad magic)

                # Deduplication: drop retransmissions received within 5 seconds
                now = time.time()
                cache_key = (packet.src_id, packet.msg_id)
                if cache_key in self.seen_messages and (now - self.seen_messages[cache_key]) < 5.0:
                    continue
                self.seen_messages[cache_key] = now

                # Clean old deduplication entries (> 10s)
                if len(self.seen_messages) > 100:
                    self.seen_messages = {k: v for k, v in self.seen_messages.items() if now - v < 10.0}

                # Ignore packets sent by ourselves
                if packet.src_id == self.node_id:
                    continue

                now_str = time.strftime("%H:%M:%S")

                # ==============================================================
                # RECEPTION LOGIC: UNICAST vs BROADCAST vs FORWARD
                # ==============================================================
                if packet.dst_id == self.node_id:
                    # 1. DIRECT UNICAST FOR ME
                    print(
                        f"\r\033[K[{now_str}] \033[92m[RX UNICAST from Node {packet.src_id:04X}]\033[0m: {packet.payload}",
                        flush=True,
                    )
                    print(self._get_prompt(), end="", flush=True)

                elif packet.dst_id == BROADCAST_ID:
                    # 2. GLOBAL BROADCAST
                    print(
                        f"\r\033[K[{now_str}] \033[96m[RX BROADCAST from Node {packet.src_id:04X}]\033[0m: {packet.payload}",
                        flush=True,
                    )
                    print(self._get_prompt(), end="", flush=True)
                    # Also relay broadcast packets if multi-hop enabled
                    self._forward_relay(packet)

                else:
                    # 3. PACKET FOR ANOTHER NODE (NOT ME)
                    # Silently ignore for terminal display, but forward if relay enabled
                    self._forward_relay(packet)

            except Exception as e:
                if self.running:
                    time.sleep(0.05)

    def _get_prompt(self) -> str:
        tgt_str = "ALL" if self.target_id == BROADCAST_ID else f"0x{self.target_id:04X}"
        return f"\033[1;34m[Node 0x{self.node_id:04X} -> {tgt_str}]\033[0m >>> "

    def run_cli(self):
        """Starts interactive terminal CLI."""
        self.running = True
        rx_thread = threading.Thread(target=self._rx_listener, daemon=True)
        rx_thread.start()

        print("=" * 68)
        print(" TACTICAL SDR POINT-TO-POINT (UNICAST) CHAT")
        print(f" My Node ID:       \033[92m0x{self.node_id:04X} ({self.node_id})\033[0m")
        print(f" Default Target:   \033[93m{'BROADCAST' if self.target_id == BROADCAST_ID else hex(self.target_id)}\033[0m")
        print(f" Multi-Hop Relay:  \033[95m{'ENABLED' if self.enable_relay else 'DISABLED'}\033[0m")
        print(f" SDR IPC Socket:   Ingest (RX) Port {self.rx_port} | Egress (TX) Port {self.tx_port}")
        print("=" * 68)
        print("Commands:")
        print("  /to <node_id>   : Switch target unicast destination (e.g. '/to 2' or '/to 0x0002')")
        print("  /all            : Switch back to broadcast mode")
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

                # Handle Slash Commands
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
            self.sock.close()
            print("\n[*] Tactical Unicast Chat terminated. 73!")


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================

def parse_args():
    parser = argparse.ArgumentParser(description="Tactical SDR Point-to-Point Unicast Chat")
    parser.add_argument("--id", type=lambda x: int(x, 0), default=None, help="My Node ID (e.g. 1, 2, 0x0001)")
    parser.add_argument("--node", default="", help="Node alias ('mac'=1, 'pi5'=2, 'aml'=3)")
    parser.add_argument("--to", type=lambda x: int(x, 0), default=BROADCAST_ID, help="Target Node ID (default: 0xFFFF Broadcast)")
    parser.add_argument("--tx-port", type=int, default=52001, help="UDP Egress port to SDR modulator (default: 52001)")
    parser.add_argument("--rx-port", type=int, default=52002, help="UDP Ingest port from SDR demodulator (default: 52002)")
    parser.add_argument("--host", default="127.0.0.1", help="Target SDR host IP (default: 127.0.0.1)")
    parser.add_argument("--no-relay", action="store_true", help="Disable multi-hop packet forwarding")
    return parser.parse_args()


def main():
    args = parse_args()

    # Automatic Node ID resolution from alias or hostname if not specified
    node_id = args.id
    if node_id is None:
        alias = (args.node or os.uname().nodename).lower()
        if "mac" in alias or "darwin" in sys.platform:
            node_id = 0x0001
        elif "pi5" in alias:
            node_id = 0x0002
        elif "aml" in alias or "2w" in alias:
            node_id = 0x0003
        else:
            node_id = 0x0001

    chat = UnicastSDRChat(
        node_id=node_id,
        tx_port=args.tx_port,
        rx_port=args.rx_port,
        remote_host=args.host,
        enable_relay=not args.no_relay,
    )
    if args.to != BROADCAST_ID:
        chat.target_id = args.to

    chat.run_cli()


if __name__ == "__main__":
    main()
