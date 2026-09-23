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
from tsm.modem.mac import (
    MACMode,
    TacticalMAC,
)
from tsm.config import AppConfig, CONFIG
from tsm.telemetry import (
    TrainAISTelemetry,
    DummyGPSSimulator,
    HEX_PREFIX,
)
from tsm.gateway import RailwayAISGateway

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
        mac_mode: str = "lbt",
        sim_enabled: bool = False,
        sim_interval: float = 5.0,
        is_gateway: bool = False,
        gateway_station_name: str = "Stasiun Central (Macbook Gateway)",
        gateway_log_file: str = "logs/gateway_telemetry.jsonl",
        gateway_log_enabled: bool = True,
        gateway_mqtt_enabled: bool = False,
        gateway_mqtt_broker: str = "127.0.0.1",
        gateway_mqtt_port: int = 1883,
        gateway_mqtt_topic: str = "railway/telemetry",
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
        self.sim_enabled = sim_enabled
        self.sim_interval = max(0.5, float(sim_interval))
        self.simulator = DummyGPSSimulator(train_id=self.node_id)
        self.sim_thread: Optional[threading.Thread] = None
        self.running = False
        self.seq_counter = 0
        self.seen_messages: Dict[Tuple[int, int], float] = {}
        self.lock = threading.Lock()
        self.last_tx_time = 0.0

        # Initialize Railway AIS Station Gateway
        self.is_gateway = is_gateway
        self.gateway: Optional[RailwayAISGateway] = None
        if self.is_gateway:
            self.gateway = RailwayAISGateway(
                node_id=self.node_id,
                station_name=gateway_station_name,
                log_enabled=gateway_log_enabled,
                log_file=gateway_log_file,
                mqtt_enabled=gateway_mqtt_enabled,
                mqtt_broker=gateway_mqtt_broker,
                mqtt_port=gateway_mqtt_port,
                mqtt_topic=gateway_mqtt_topic,
            )

        # Initialize Tactical MAC Layer (LBT / Slotted / S-TDMA)
        try:
            mode_enum = MACMode(mac_mode.lower())
        except ValueError:
            mode_enum = MACMode.LBT
        self.mac = TacticalMAC(node_id=self.node_id, mode=mode_enum)

        # Precompute sync template for 2-FSK PHY
        self.sync_upsampled, self.sync_len = build_sync_template()

        # Initialize Transport Backend
        if self.use_socket:
            self._init_socket()
        else:
            self._init_pluto_sdr()

    def _sim_worker(self):
        """Background worker periodically advancing dummy GPS and broadcasting hex telemetry."""
        while self.running and self.sim_enabled:
            time.sleep(self.sim_interval)
            if not self.running or not self.sim_enabled:
                break
            telemetry = self.simulator.step(dt=self.sim_interval)
            hex_payload = telemetry.to_hex_payload()
            self.send_message(hex_payload)

    def start_simulation(self, interval: Optional[float] = None):
        """Starts periodic dummy GPS & device health hex broadcasting."""
        if interval is not None:
            self.sim_interval = max(0.5, float(interval))
        self.sim_enabled = True
        if self.sim_thread is None or not self.sim_thread.is_alive():
            self.sim_thread = threading.Thread(target=self._sim_worker, daemon=True)
            self.sim_thread.start()
        print(f"[*] Simulation mode \033[92mSTARTED\033[0m: Auto-broadcasting dummy GPS + condition every {self.sim_interval:.1f}s")
        print(self._get_prompt(), end="", flush=True)

    def stop_simulation(self):
        """Stops periodic simulation."""
        self.sim_enabled = False
        print(f"[*] Simulation mode \033[91mSTOPPED\033[0m.")
        print(self._get_prompt(), end="", flush=True)

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

    def _transmit_raw(self, wire_bytes: bytes, repeats: int = 3):
        """Transmits raw binary frame over active transport (SDR or Socket)."""
        if self.sdr_mode == "socket":
            self.sock.sendto(wire_bytes, (self.remote_host, self.tx_port))
        else:
            # Medium Access Control: Check LBT / S-TDMA slot permission
            if not self.mac.acquire_tx_permission():
                print(
                    f"\r\033[K\033[93m[*] [MAC DROP] Channel busy / backoff expired. Dropped burst.\033[0m",
                    flush=True,
                )
                print(self._get_prompt(), end="", flush=True)
                return

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
                for _ in range(max(1, repeats)):
                    self.tx_buf.write(raw_dma)
                    self.tx_buf.push()
                    time.sleep((num_samples / SAMPLE_RATE) + 0.04)

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
        self._transmit_raw(wire_bytes, repeats=3)

        now_str = time.strftime("%H:%M:%S")
        target_str = "BROADCAST" if target == BROADCAST_ID else f"Node 0x{target:04X}"
        telem = TrainAISTelemetry.from_payload_string(text)
        if telem:
            print(
                f"\r\033[K[{now_str}] \033[93m[TX AIS TELEMETRY -> {target_str}]\033[0m\n"
                f"       Hex Raw: \033[90m{text}\033[0m\n"
                f"       {telem.format_display()}",
                flush=True,
            )
            print(self._get_prompt(), end="", flush=True)
        else:
            print(f"\r\033[K[{now_str}] \033[93m[TX -> {target_str}]\033[0m: {text}", flush=True)

    def _forward_relay(self, packet: MeshPacket):
        """Multi-hop Relay: Decrements TTL and re-broadcasts packet over RF asynchronously."""
        if not self.enable_relay or packet.ttl <= 1:
            return

        packet.ttl -= 1
        packet.flags |= FLAG_RELAYED

        def _relay_worker():
            # Random jitter backoff (40ms - 90ms) to prevent mutual RF packet collision
            time.sleep(0.04 + (self.node_id % 5) * 0.015)
            self._transmit_raw(packet.pack(), repeats=1)
            print(
                f"\r\033[K\033[90m[*] [RELAY] Forwarded Msg #{packet.msg_id:04X} to Node 0x{packet.dst_id:04X} (TTL left: {packet.ttl})\033[0m",
                flush=True,
            )
            print(self._get_prompt(), end="", flush=True)

        threading.Thread(target=_relay_worker, daemon=True).start()

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
        telem = TrainAISTelemetry.from_payload_string(packet.payload)
        gw_note = ""
        if telem and self.gateway:
            self.gateway.ingest(packet, telem, corr=corr)
            gw_note = f"\n       \033[94m[GATEWAY INGEST]\033[0m Logged -> {self.gateway.log_path.name}"

        # ======================================================================
        # RECEPTION FILTERING LOGIC
        # ======================================================================
        if packet.dst_id == self.node_id:
            # 1. DIRECT UNICAST FOR MY NODE
            if telem:
                print(
                    f"\r\033[K[{now_str}] \033[92m[RX UNICAST AIS TELEMETRY from Node 0x{packet.src_id:04X}{corr_info}]\033[0m\n"
                    f"       Hex Raw: \033[90m{packet.payload}\033[0m\n"
                    f"       {telem.format_display()}{gw_note}",
                    flush=True,
                )
            else:
                print(
                    f"\r\033[K[{now_str}] \033[92m[RX UNICAST from Node 0x{packet.src_id:04X}{corr_info}]\033[0m: {packet.payload}",
                    flush=True,
                )
            print(self._get_prompt(), end="", flush=True)

        elif packet.dst_id == BROADCAST_ID:
            # 2. GLOBAL BROADCAST
            if telem:
                print(
                    f"\r\033[K[{now_str}] \033[96m[RX BROADCAST AIS TELEMETRY from Node 0x{packet.src_id:04X}{corr_info}]\033[0m\n"
                    f"       Hex Raw: \033[90m{packet.payload}\033[0m\n"
                    f"       {telem.format_display()}{gw_note}",
                    flush=True,
                )
            else:
                print(
                    f"\r\033[K[{now_str}] \033[96m[RX BROADCAST from Node 0x{packet.src_id:04X}{corr_info}]\033[0m: {packet.payload}",
                    flush=True,
                )
            print(self._get_prompt(), end="", flush=True)
            self._forward_relay(packet)

        else:
            # 3. PACKET FOR ANOTHER NODE (NOT ME)
            # Show operator that packet was physically received over RF but addressed elsewhere
            if telem:
                print(
                    f"\r\033[K\033[90m[{now_str}] [OVERHEARD AIS TELEMETRY{corr_info}] Node 0x{packet.src_id:04X} -> Node 0x{packet.dst_id:04X} | {telem.format_display()}{gw_note}\033[0m",
                    flush=True,
                )
            else:
                print(
                    f"\r\033[K\033[90m[{now_str}] [OVERHEARD{corr_info}] Node 0x{packet.src_id:04X} -> Node 0x{packet.dst_id:04X} (Private unicast - not for this node)\033[0m",
                    flush=True,
                )
            print(self._get_prompt(), end="", flush=True)
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
                self.mac.feed_rx_samples(raw_samples)

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
        print(f" MAC Protocol:     \033[96m{self.mac.mode.value.upper()}\033[0m (Anti-Collision: LBT & S-TDMA Ready)")
        sim_stat = f"\033[92mACTIVE (every {self.sim_interval:.1f}s)\033[0m" if self.sim_enabled else "\033[90mINACTIVE\033[0m"
        print(f" GPS/Telem Sim:    {sim_stat}")
        if self.is_gateway and self.gateway:
            print(f" Gateway Station:  \033[94mACTIVE - {self.gateway.station_name}\033[0m")
            print(f" Telemetry Uplink: \033[92mLOG ONLY\033[0m ({self.gateway.log_path.name}) [MQTT: Standby]")
        else:
            print(f" Gateway Station:  \033[90mDISABLED (Standard Node)\033[0m")
        if self.sdr_mode == "socket":
            print(f" Transport Mode:   UDP Socket PDU (RX Port {self.rx_port} | TX Port {self.tx_port})")
        else:
            print(f" Transport Mode:   Direct Pluto+ SDR at {self.uri} (915.000 MHz)")
        print("=" * 68)
        print("Commands:")
        print("  /to <node_id>      : Switch target unicast destination (e.g. '/to 2' or '/to 0x0002')")
        print("  /all               : Switch back to broadcast mode (0xFFFF)")
        print("  /relay on|off      : Enable or disable multi-hop packet forwarding")
        print("  /mac [mode]        : Show or switch MAC mode (lbt, slotted, stdma, off)")
        print("  /sim [on|off|once] : Start/stop/step dummy GPS & health telemetry broadcast")
        print("  /sim brake [on|off]: Toggle train emergency brake condition")
        print("  /sim status        : Display current telemetry condition & hex string")
        if self.is_gateway:
            print("  /gw status         : Show gateway ingestion statistics & tracked trains")
            print("  /gw log [n]        : View last N lines of logged telemetry JSON")
        print("  /exit              : Quit application\n")

        if self.sim_enabled:
            if self.sim_thread is None or not self.sim_thread.is_alive():
                self.sim_thread = threading.Thread(target=self._sim_worker, daemon=True)
                self.sim_thread.start()

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
                        raw_id = parts[1].strip().lower()
                        aliases = {
                            "mac": 0x0001,
                            "pi5": 0x0002,
                            "raspi5": 0x0002,
                            "aml": 0x0003,
                            "2w": 0x0003,
                            "node1": 0x0001,
                            "node2": 0x0002,
                            "node3": 0x0003,
                            "all": BROADCAST_ID,
                            "broadcast": BROADCAST_ID,
                        }
                        if raw_id in aliases:
                            self.target_id = aliases[raw_id]
                            target_desc = "BROADCAST" if self.target_id == BROADCAST_ID else f"0x{self.target_id:04X} ({raw_id.upper()})"
                            print(f"[*] Target destination updated to: \033[93m{target_desc}\033[0m")
                        else:
                            try:
                                new_dst = int(raw_id, 16) if raw_id.startswith("0x") or raw_id.startswith("0X") else int(raw_id)
                                self.target_id = new_dst & 0xFFFF
                                print(f"[*] Target destination updated to: \033[93m0x{self.target_id:04X}\033[0m")
                            except ValueError:
                                print(f"[ERROR] Invalid Node ID '{raw_id}'. Use decimal (e.g. 2), hex (e.g. 0x0002), or alias ('mac', 'pi5', 'aml').")
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
                elif msg.startswith("/mac"):
                    parts = msg.split(maxsplit=1)
                    if len(parts) > 1:
                        mode_str = parts[1].strip().lower()
                        if mode_str in ("off", "none", "disable"):
                            self.mac.mode = MACMode.OFF
                        elif mode_str in ("lbt", "csma"):
                            self.mac.mode = MACMode.LBT
                        elif mode_str in ("slotted", "slot"):
                            self.mac.mode = MACMode.SLOTTED_LBT
                        elif mode_str in ("stdma", "tdma"):
                            self.mac.mode = MACMode.STDMA
                        else:
                            print(f"[ERROR] Unknown MAC mode '{mode_str}'. Available: lbt, slotted, stdma, off")
                    print(f"[*] MAC Status: {self.mac.get_status_summary()}")
                    continue
                elif msg.startswith("/sim"):
                    parts = msg.split()
                    subcmd = parts[1].lower() if len(parts) > 1 else "status"
                    if subcmd in ("on", "start", "enable"):
                        interval = float(parts[2]) if len(parts) > 2 else self.sim_interval
                        self.start_simulation(interval)
                    elif subcmd in ("off", "stop", "disable"):
                        self.stop_simulation()
                    elif subcmd in ("once", "send", "step"):
                        telem = self.simulator.step(dt=self.sim_interval)
                        hex_payload = telem.to_hex_payload()
                        self.send_message(hex_payload)
                    elif subcmd == "brake":
                        brake_state = True
                        if len(parts) > 2 and parts[2].lower() in ("off", "release", "0", "false"):
                            brake_state = False
                        self.simulator.set_emergency_brake(brake_state)
                        state_str = "\033[91mTRIGGERED (Active)\033[0m" if brake_state else "\033[92mRELEASED (Normal)\033[0m"
                        print(f"[*] Emergency Brake status: {state_str}")
                    elif subcmd == "status":
                        curr = self.simulator.current_telemetry()
                        print(
                            f"[*] Simulation State:\n"
                            f"    Active:   {'ENABLED' if self.sim_enabled else 'DISABLED'} (Interval: {self.sim_interval:.1f}s)\n"
                            f"    {curr.format_display()}\n"
                            f"    Hex Code: \033[90m{curr.to_hex_payload()}\033[0m"
                        )
                    else:
                        print(f"[ERROR] Unknown /sim command. Use: /sim on [sec], /sim off, /sim once, /sim brake [on|off], /sim status")
                    continue
                elif msg.startswith("/gw") or msg.startswith("/gateway"):
                    if not self.gateway:
                        print("[*] Gateway mode is not enabled on this node (Set IS_GATEWAY=true in .env or run with --gateway)")
                        continue
                    parts = msg.split()
                    subcmd = parts[1].lower() if len(parts) > 1 else "status"
                    if subcmd in ("status", "info"):
                        print(f"[*] {self.gateway.get_status_summary()}")
                        tracked = self.gateway.get_tracked_trains()
                        if tracked:
                            print("[*] Active Tracked Trains:")
                            for tid, info in tracked.items():
                                gps = info.get("gps", {})
                                mot = info.get("motion", {})
                                hlth = info.get("device_health", {})
                                print(
                                    f"    Train {info.get('train_id')} @ ({gps.get('latitude')}, {gps.get('longitude')}) | "
                                    f"Spd: {mot.get('speed_kmh')} km/h | Bat: {hlth.get('battery_v')}V | "
                                    f"Packets: {info.get('total_packets')} | Last Seen: {info.get('last_seen_iso')}"
                                )
                        else:
                            print("    (No train packets ingested yet)")
                    elif subcmd in ("log", "logs", "cat"):
                        n = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 5
                        logs = self.gateway.get_recent_logs(max_lines=n)
                        if logs:
                            print(f"[*] Last {len(logs)} Telemetry Records in {self.gateway.log_path.name}:")
                            for line in logs:
                                print(f"    \033[90m{line}\033[0m")
                        else:
                            print(f"[*] No logs found in {self.gateway.log_path}")
                    else:
                        print("[ERROR] Unknown /gw command. Use: /gw status, /gw log [n]")
                    continue

                self.send_message(msg)

        finally:
            self.running = False
            if self.gateway:
                self.gateway.close()
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
    cfg = AppConfig.load()
    parser = argparse.ArgumentParser(
        description="Tactical SDR Point-to-Point Unicast & Multi-Hop Chat (Configured via .env or CLI)"
    )
    parser.add_argument(
        "--id",
        type=lambda x: int(x, 0),
        default=None,
        help=f"My Node ID (default from .env: 0x{cfg.node_id:04X})",
    )
    parser.add_argument(
        "--node",
        default="",
        help=f"Node alias ('mac'=1, 'pi5'=2, 'aml'=3) (default from .env: {cfg.node_alias})",
    )
    parser.add_argument(
        "--uri",
        default="",
        help=f"Pluto SDR URI (default from .env: {cfg.sdr_uri})",
    )
    parser.add_argument(
        "--to",
        type=lambda x: int(x, 0),
        default=None,
        help=f"Target Node ID (default from .env: 0x{cfg.target_id:04X})",
    )
    parser.add_argument(
        "--socket",
        action="store_true",
        default=None,
        help="Use UDP Socket PDU instead of direct Pluto SDR",
    )
    parser.add_argument(
        "--tx-port",
        type=int,
        default=cfg.socket_tx_port,
        help=f"UDP Egress port to SDR modulator (default: {cfg.socket_tx_port})",
    )
    parser.add_argument(
        "--rx-port",
        type=int,
        default=cfg.socket_rx_port,
        help=f"UDP Ingest port from SDR demodulator (default: {cfg.socket_rx_port})",
    )
    parser.add_argument(
        "--host",
        default=cfg.socket_host,
        help=f"Target SDR host IP (default: {cfg.socket_host})",
    )
    parser.add_argument(
        "--rx-gain",
        type=float,
        default=cfg.rx_gain,
        help=f"RX Gain dB (default: {cfg.rx_gain})",
    )
    parser.add_argument(
        "--tx-atten",
        type=float,
        default=cfg.tx_atten,
        help=f"TX Attenuation dB (default: {cfg.tx_atten})",
    )
    parser.add_argument(
        "--no-relay",
        action="store_true",
        default=False,
        help="Disable multi-hop packet forwarding",
    )
    parser.add_argument(
        "--mac",
        default=cfg.mac_mode,
        choices=["lbt", "slotted", "stdma", "off"],
        help=f"MAC anti-collision mode (default from .env: {cfg.mac_mode})",
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        default=None,
        help="Enable simulation mode (auto-broadcast GPS + telemetry in hex)",
    )
    parser.add_argument(
        "--sim-interval",
        type=float,
        default=cfg.sim_interval,
        help=f"Simulation telemetry broadcast interval in seconds (default from .env: {cfg.sim_interval:.1f})",
    )
    parser.add_argument(
        "--gateway",
        action="store_true",
        default=None,
        help="Enable Railway AIS Gateway mode (ingest and log telemetry)",
    )
    parser.add_argument(
        "--station",
        default="",
        help=f"Gateway station name (default from .env: {cfg.gateway_station_name})",
    )
    parser.add_argument(
        "--gateway-log",
        default="",
        help=f"Gateway JSONL log file (default from .env: {cfg.gateway_log_file})",
    )
    args = parser.parse_args()

    # Determine node_id and URI by merging CLI args, .env, and node aliases
    node_str = (args.node or cfg.node_alias or "").lower()
    node_id = args.id
    uri = args.uri

    if uri and ("usbmodem" in uri or "/dev/tty" in uri):
        uri = "ip:192.168.2.10"

    # Profile alias resolution
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

    # Fallback to .env configuration
    node_id = node_id if node_id is not None else cfg.node_id
    uri = uri or cfg.sdr_uri
    target_id = args.to if args.to is not None else cfg.target_id
    use_socket = args.socket if args.socket is not None else cfg.use_socket
    enable_relay = False if args.no_relay else cfg.enable_relay
    mac_mode = args.mac or cfg.mac_mode
    sim_enabled = args.sim if args.sim is not None else cfg.sim_enabled
    sim_interval = args.sim_interval if args.sim_interval is not None else cfg.sim_interval

    is_gateway = args.gateway if args.gateway is not None else cfg.is_gateway
    station_name = args.station or cfg.gateway_station_name
    gateway_log = args.gateway_log or cfg.gateway_log_file

    chat = UnicastSDRChat(
        node_id=node_id,
        target_id=target_id,
        uri=uri,
        use_socket=use_socket,
        tx_port=args.tx_port,
        rx_port=args.rx_port,
        remote_host=args.host,
        rx_gain=args.rx_gain,
        tx_atten=args.tx_atten,
        enable_relay=enable_relay,
        mac_mode=mac_mode,
        sim_enabled=sim_enabled,
        sim_interval=sim_interval,
        is_gateway=is_gateway,
        gateway_station_name=station_name,
        gateway_log_file=gateway_log,
        gateway_log_enabled=cfg.gateway_log_enabled,
        gateway_mqtt_enabled=cfg.gateway_mqtt_enabled,
        gateway_mqtt_broker=cfg.gateway_mqtt_broker,
        gateway_mqtt_port=cfg.gateway_mqtt_port,
        gateway_mqtt_topic=cfg.gateway_mqtt_topic,
    )
    chat.run_cli()


if __name__ == "__main__":
    main()

