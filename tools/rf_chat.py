#!/usr/bin/env python3
"""
Direct Tactical SDR RF Chat (915.000 MHz)
Interactive terminal chat communicating directly over Pluto SDR RX/TX via 2-FSK.
No kernel modules, no TAP device, no network stack required.
"""

import os
import sys
import time
import struct
import threading
import argparse
from pathlib import Path
from typing import Optional, Tuple
import numpy as np

# Ensure src/ is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
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


def get_default_callsign() -> str:
    nodename = os.uname().nodename.lower()
    sysname = os.uname().sysname.lower()
    if "pi5" in nodename:
        return "HQ-PI5"
    elif "2w" in nodename or "zero" in nodename or "aml" in nodename:
        return "OUTPOST-PI2W"
    elif "darwin" in sysname or "mac" in nodename:
        return "COMMANDER-MAC"
    return nodename.upper()


def get_default_uri() -> str:
    nodename = os.uname().nodename.lower()
    sysname = os.uname().sysname.lower()
    if "2w" in nodename or "zero" in nodename or "aml" in nodename:
        return "ip:192.168.99.240"
    elif "darwin" in sysname:
        return "ip:192.168.2.10"
    return "usb:1.3.5"


class DirectRFChat:
    def __init__(self, uri: str, callsign: str, rx_gain: float = 65.0, tx_atten: float = 0.0):
        self.uri = uri
        self.callsign = callsign
        self.rx_gain = rx_gain
        self.tx_atten = tx_atten
        self.running = False
        self.last_tx_time = 0.0
        self.lock = threading.Lock()

        # Precompute sync template (fast DECIMATION=10)
        self.sync_upsampled, self.sync_len = build_sync_template()

        # Connect to ADALM-PLUTO SDR
        print(f"[*] Initializing ADALM-PLUTO SDR context at: {self.uri} ...")
        self.ctx = iio.Context(self.uri)
        self.phy = self.ctx.find_device("ad9361-phy")
        self.rx_dev = self.ctx.find_device("cf-ad9361-lpc")
        self.tx_dev = self.ctx.find_device("cf-ad9361-dds-core-lpc")

        if not self.phy or not self.rx_dev or not self.tx_dev:
            raise RuntimeError("Required AD9361 IIO devices not found on SDR context.")

        self._configure_sdr()

        # Allocate single reusable DMA buffers
        self.rx_buf = iio.Buffer(self.rx_dev, RX_BUF_SIZE, False)
        self.tx_buf = iio.Buffer(self.tx_dev, TX_BUF_SIZE, False)
        print(f"[*] Radio Hardware Ready: {CARRIER_FREQ/1e6:.3f} MHz | Rate: {SAMPLE_RATE/1e6:.2f} MSps")

    def _configure_sdr(self):
        # Configure LO Frequencies
        self.phy.find_channel("altvoltage1", True).attrs["frequency"].value = str(CARRIER_FREQ)
        self.phy.find_channel("altvoltage0", True).attrs["frequency"].value = str(CARRIER_FREQ)

        # Configure Sample Rate & Analog Filter
        self.phy.find_channel("voltage0", False).attrs["sampling_frequency"].value = str(SAMPLE_RATE)
        self.phy.find_channel("voltage0", True).attrs["sampling_frequency"].value = str(SAMPLE_RATE)
        self.phy.find_channel("voltage0", False).attrs["rf_bandwidth"].value = "1000000"
        self.phy.find_channel("voltage0", True).attrs["rf_bandwidth"].value = "1000000"

        # Configure Gains
        self.phy.find_channel("voltage0", False).attrs["gain_control_mode"].value = "manual"
        self.phy.find_channel("voltage0", False).attrs["hardwaregain"].value = f"{self.rx_gain:.1f}"
        self.phy.find_channel("voltage0", True).attrs["hardwaregain"].value = f"{self.tx_atten:.1f}"

        # Enable only 2 channels (I & Q) for SISO RF link
        for ch in self.rx_dev.channels:
            ch.enabled = ch.id in ("voltage0", "voltage1")
        for ch in self.tx_dev.channels:
            ch.enabled = ch.id in ("voltage0", "voltage1")

        # Disable internal DDS tones
        for ch in self.tx_dev.channels:
            if "raw" in ch.attrs:
                ch.attrs["raw"].value = "0"

        self.seen_messages = {}

    def transmit_text(self, text: str):
        """Encapsulates text message with callsign and transmits over RF."""
        # Frame format: [CALLSIGN_BYTES: max 16B][0x00][TEXT_BYTES]
        callsign_bytes = self.callsign.encode("utf-8")[:16]
        payload = callsign_bytes + b"\x00" + text.encode("utf-8")[:220]

        iq = modulate_2fsk(payload)
        num_samples = len(iq)
        if num_samples > TX_BUF_SIZE:
            print(f"\n[WARN] Message too long ({num_samples} samples > {TX_BUF_SIZE})", file=sys.stderr)
            return

        padded = np.zeros((TX_BUF_SIZE, 2), dtype=np.int16)
        padded[:num_samples] = iq
        raw_bytes = bytearray(padded.tobytes())

        with self.lock:
            self.last_tx_time = time.time()
            # Transmit 3 bursts for link reliability over the air
            for _ in range(3):
                self.tx_buf.write(raw_bytes)
                self.tx_buf.push()
                time.sleep((num_samples / SAMPLE_RATE) + 0.05)

        now_str = time.strftime("%H:%M:%S")
        print(f"\r\033[K[{now_str}] <{self.callsign}> (TX): {text}", flush=True)

    def _rx_worker(self):
        """Continuous RX loop capturing and demodulating OTA frames."""
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
                    payload, corr = result
                    # Parse callsign and text
                    if b"\x00" in payload:
                        parts = payload.split(b"\x00", 1)
                        sender = parts[0].decode("utf-8", errors="replace")
                        text = parts[1].decode("utf-8", errors="replace")
                    else:
                        sender = "UNKNOWN"
                        text = payload.decode("utf-8", errors="replace")

                    # Ignore self transmission
                    if sender == self.callsign or self.callsign.startswith(sender):
                        continue

                    # Deduplicate retransmitted bursts within 2.0s
                    now = time.time()
                    msg_hash = (sender, text)
                    if msg_hash in self.seen_messages and (now - self.seen_messages[msg_hash]) < 2.0:
                        continue
                    self.seen_messages[msg_hash] = now

                    now_str = time.strftime("%H:%M:%S")
                    # Clear current prompt line, print message, and restore prompt
                    print(f"\r\033[K\033[92m[{now_str}] <{sender}> (RX | {corr:.2f}): {text}\033[0m", flush=True)
                    print(f"[{self.callsign}] >>> ", end="", flush=True)

            except Exception as e:
                if self.running:
                    time.sleep(0.05)

    def run_cli(self):
        self.running = True
        rx_thread = threading.Thread(target=self._rx_worker, daemon=True)
        rx_thread.start()

        print("=" * 64)
        print(f" TACTICAL SDR DIRECT RF CHAT (915.000 MHz)")
        print(f" Callsign:    \033[96m{self.callsign}\033[0m")
        print(f" Hardware:    {self.uri}")
        print(f" Mode:        2-FSK Direct OTA (50 kbps, dev +/- 50 kHz)")
        print(f" RX Gain:     {self.rx_gain:.1f} dB  |  TX Atten: {self.tx_atten:.1f} dB (Max Power)")
        print("=" * 64)
        print("Type message and press ENTER to transmit. Type '/exit' to quit.\n")

        try:
            while self.running:
                try:
                    if sys.stdin.isatty():
                        msg = input(f"[{self.callsign}] >>> ").strip()
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

                self.transmit_text(msg)

        finally:
            self.running = False
            print("\n[*] Shutting down RF Chat interface. 73!")


class RemoteGatewayChat:
    """
    Tactical Mesh C2 Terminal for macOS & Remote Operator Workstations.
    Connects to an active mesh gateway node (RasPi 5 / AML) over HTTP/SSE API.
    Bridges operator messages to/from the 915.000 MHz 2-FSK SDR mesh.
    """

    def __init__(self, gateway_url: Optional[str] = None, callsign: str = "MAC-C2", target_ip: str = "255.255.255.255"):
        self.callsign = callsign
        self.target_ip = target_ip
        self.running = False
        self.seen_msg_ids = set()
        self.gateway_url = (gateway_url or self._auto_discover_gateway()).rstrip("/")

    def _auto_discover_gateway(self) -> str:
        candidates = [
            os.environ.get("MESH_GATEWAY", ""),
            "http://192.168.0.120:8080",  # RasPi 5 (HQ)
            "http://192.168.0.12:8080",   # AML (Outpost)
            "http://raspi5.local:8080",
            "http://127.0.0.1:8080",
        ]
        import urllib.request
        for cand in candidates:
            if not cand:
                continue
            cand_url = cand if cand.startswith("http://") or cand.startswith("https://") else f"http://{cand}"
            if cand_url.count(":") == 1:
                cand_url = f"{cand_url}:8080"
            try:
                req = urllib.request.Request(f"{cand_url}/api/telemetry", headers={"User-Agent": "TSM-MacTerminal/1.0"})
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        return cand_url
            except Exception:
                continue
        # Default fallback
        return "http://192.168.0.120:8080"

    def _rx_worker(self):
        import urllib.request
        import json

        # Pre-seed history so we don't spam the console on startup, but show last 3 messages
        try:
            req = urllib.request.Request(f"{self.gateway_url}/api/history", headers={"User-Agent": "TSM-MacTerminal/1.0"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                msgs = json.loads(resp.read().decode("utf-8"))
                for m in msgs[:-3]:
                    mid = m.get("id") or f"{m.get('sender_ip')}:{m.get('timestamp')}:{m.get('text')}"
                    self.seen_msg_ids.add(mid)
                for m in msgs[-3:]:
                    mid = m.get("id") or f"{m.get('sender_ip')}:{m.get('timestamp')}:{m.get('text')}"
                    self.seen_msg_ids.add(mid)
                    self._print_incoming_msg(m)
        except Exception:
            pass

        while self.running:
            try:
                req = urllib.request.Request(f"{self.gateway_url}/api/history", headers={"User-Agent": "TSM-MacTerminal/1.0"})
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    msgs = json.loads(resp.read().decode("utf-8"))
                    for m in msgs:
                        mid = m.get("id") or f"{m.get('sender_ip')}:{m.get('timestamp')}:{m.get('text')}"
                        if mid not in self.seen_msg_ids:
                            self.seen_msg_ids.add(mid)
                            self._print_incoming_msg(m)
            except Exception:
                pass
            time.sleep(1.0)

    def _print_incoming_msg(self, m: dict):
        sender = m.get("sender_callsign") or m.get("sender") or m.get("sender_ip", "UNKNOWN")
        text = m.get("text", "")
        ts = m.get("timestamp", time.time())
        t_str = time.strftime("%H:%M:%S", time.localtime(ts))

        # Check if outbound from self
        if sender == self.callsign or text.startswith(f"[{self.callsign}]"):
            return

        if "PI5" in sender:
            color = "\033[92m"  # Vivid Green for HQ-PI5
        elif "2W" in sender or "AML" in sender:
            color = "\033[96m"  # Vivid Cyan for Outpost
        elif "MAC" in sender:
            color = "\033[93m"  # Yellow for Mac
        else:
            color = "\033[95m"  # Magenta

        print(f"\r\033[K{color}[{t_str}] [{sender}] {text}\033[0m")
        if sys.stdin.isatty():
            print(f"[{self.callsign}] >>> ", end="", flush=True)

    def transmit_text(self, text: str):
        import urllib.request
        import json

        url = f"{self.gateway_url}/api/chat"
        payload = {
            "target_ip": self.target_ip,
            "text": f"[{self.callsign}] {text}" if not text.startswith("[") else text,
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                if res.get("delivered"):
                    print(f"\033[90m[TX -> MESH OK] {payload['text']}\033[0m")
                else:
                    print(f"\033[91m[TX FAILED]\033[0m")
        except Exception as e:
            print(f"\033[91m[ERROR] Gateway unreachable at {self.gateway_url}: {e}\033[0m")

    def _show_status(self):
        import urllib.request
        import json
        try:
            req = urllib.request.Request(f"{self.gateway_url}/api/telemetry", headers={"User-Agent": "TSM-MacTerminal/1.0"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                node = data.get("node", {})
                sdr = data.get("sdr", {})
                neighbors = data.get("neighbors", [])
                print(f"\n--- Mesh Node Status ({self.gateway_url}) ---")
                print(f" Node:      {node.get('hostname')} | IP: {node.get('mesh_ip')} | Uptime: {node.get('uptime_sec')}s")
                print(f" SDR Radio: {sdr.get('center_freq_hz', 915000000)/1e6:.3f} MHz | RSSI: {sdr.get('live_rssi_db', 0):.1f} dB | Temp: {sdr.get('fpga_temp_c', 0):.1f} °C")
                print(f" B.A.T.M.A.N. Peers: {len(neighbors)}")
                for n in neighbors:
                    print(f"   - Peer {n.get('mac')}: TQ={n.get('tq_metric')}/255 ({round(n.get('tq_metric',0)/255*100)}%) | seen: {n.get('last_seen_sec'):.1f}s ago")
                print("---------------------------------------------\n")
        except Exception as e:
            print(f"[ERROR] Failed to fetch status: {e}")

    def _execute_ping(self, target: str):
        import urllib.request
        import json
        print(f"[*] Executing mesh ping to {target} via {self.gateway_url} ...")
        try:
            payload = json.dumps({"target_ip": target, "count": 3}).encode("utf-8")
            req = urllib.request.Request(f"{self.gateway_url}/api/ping", data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=12.0) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                if res.get("success"):
                    print(f"\033[92m[PING OK] {res.get('packets_recv')}/{res.get('packets_sent')} packets ({res.get('packet_loss_pct')}% loss) | RTT: {res.get('rtt_avg_ms'):.1f} ms\033[0m")
                else:
                    print(f"\033[91m[PING FAIL] 100% packet loss to {target}\033[0m")
        except Exception as e:
            print(f"[ERROR] Ping error: {e}")

    def run_cli(self):
        self.running = True

        rx_thread = threading.Thread(target=self._rx_worker, daemon=True)
        rx_thread.start()

        print("=" * 66)
        print(" TACTICAL MESH C2 TERMINAL (TSM-NET SG) - MAC STATION")
        print(f" Callsign:    \033[96m{self.callsign}\033[0m")
        print(f" Gateway:     \033[92m{self.gateway_url}\033[0m")
        print(f" Target IP:   \033[93m{self.target_ip}\033[0m")
        print(" Link Mode:   LAN-bridged to 915.000 MHz 2-FSK SDR Mesh")
        print("=" * 66)
        print("Type message and press ENTER to transmit. Commands: /target, /status, /ping, /exit\n")

        try:
            while self.running:
                try:
                    if sys.stdin.isatty():
                        msg = input(f"[{self.callsign}] >>> ").strip()
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
                elif msg.startswith("/target"):
                    parts = msg.split(maxsplit=1)
                    if len(parts) > 1:
                        self.target_ip = parts[1].strip()
                        print(f"[*] Target IP changed to: {self.target_ip}")
                    else:
                        print(f"[*] Current Target IP: {self.target_ip}")
                    continue
                elif msg == "/status":
                    self._show_status()
                    continue
                elif msg.startswith("/ping"):
                    parts = msg.split(maxsplit=1)
                    tgt = parts[1].strip() if len(parts) > 1 else "10.10.0.1"
                    self._execute_ping(tgt)
                    continue

                self.transmit_text(msg)
        finally:
            self.running = False
            print("\n[*] Disconnected from Tactical Mesh. 73!")


def main():
    parser = argparse.ArgumentParser(description="Tactical SDR RF Chat & Mesh C2 Terminal")
    parser.add_argument("--node", default="", help="Node name (e.g. pi5, pi2w, mac, node1, node2)")
    parser.add_argument("--uri", default="", help="Pluto SDR URI (e.g. usb:1.3.5 or ip:192.168.99.240)")
    parser.add_argument("--callsign", default="", help="Callsign (default: auto from hostname or MAC-C2)")
    parser.add_argument("--gateway", default="", help="Gateway HTTP URL for Mac C2 mode (e.g. http://192.168.0.120:8080)")
    parser.add_argument("--target", default="255.255.255.255", help="Target mesh IP for chat")
    parser.add_argument("--rx-gain", type=float, default=65.0, help="RX Gain dB (0-73)")
    parser.add_argument("--tx-atten", type=float, default=0.0, help="TX Attenuation dB (-89 to 0)")
    args, _ = parser.parse_known_args()

    node_str = (args.node or "").lower()
    uri = args.uri
    callsign = args.callsign

    # Map macOS serial tty or RNDIS alias to Pluto IP URI
    if uri and ("usbmodem" in uri or "/dev/tty" in uri):
        uri = "ip:192.168.2.10"

    # If explicit gateway mode requested, run RemoteGatewayChat
    if args.gateway:
        callsign = callsign or "MAC-C2"
        chat = RemoteGatewayChat(gateway_url=args.gateway, callsign=callsign, target_ip=args.target)
        chat.run_cli()
        return

    # Auto-detect node identity
    if args.node:
        if "pi5" in node_str or node_str in ("1", "node1"):
            if not uri:
                uri = "usb:1.3.5"
            if not callsign:
                callsign = "HQ-PI5"
        elif "2w" in node_str or "zero" in node_str or node_str in ("2", "node2") or "aml" in node_str:
            if not uri:
                uri = "ip:192.168.99.240"
            if not callsign:
                callsign = "OUTPOST-PI2W"
        elif "mac" in node_str:
            if not uri:
                uri = "ip:192.168.2.10"
            if not callsign:
                callsign = "COMMANDER-MAC"

    if not uri:
        uri = get_default_uri()
    if not callsign:
        callsign = get_default_callsign()

    # Try Direct SDR mode first if IIO is available
    if iio is not None:
        try:
            chat = DirectRFChat(
                uri=uri,
                callsign=callsign,
                rx_gain=args.rx_gain,
                tx_atten=args.tx_atten,
            )
            chat.run_cli()
            return
        except Exception as e:
            print(f"[*] Direct SDR context failed ({e}). Falling back to Remote Gateway C2 mode...", file=sys.stderr)

    # Fallback to Remote Gateway C2 mode
    print("[*] Entering Tactical Mesh Remote C2 mode...")
    chat = RemoteGatewayChat(gateway_url=args.gateway, callsign=callsign, target_ip=args.target)
    chat.run_cli()


if __name__ == "__main__":
    main()
