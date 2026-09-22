#!/usr/bin/env python3
"""
Tactical SDR Direct RF Chat (915.000 MHz)
Interactive terminal chat communicating directly over ADALM-PLUTO SDR RX/TX via 2-FSK.
Self-contained, robust over-the-air link without kernel modules, TAP, or network stack.
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
from tsm.config import AppConfig, CONFIG

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
        self.seen_messages = {}

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
        print(" TACTICAL SDR DIRECT RF CHAT (915.000 MHz)")
        print(f" Callsign:    \033[96m{self.callsign}\033[0m")
        print(f" Hardware:    {self.uri}")
        print(" Mode:        2-FSK Direct OTA (50 kbps, dev +/- 50 kHz)")
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


def main():
    cfg = AppConfig.load()
    parser = argparse.ArgumentParser(description="Tactical SDR Direct RF Chat (915 MHz, .env enabled)")
    parser.add_argument("--node", default="", help=f"Node name (default from .env: {cfg.node_alias})")
    parser.add_argument("--uri", default="", help=f"Pluto SDR URI (default from .env: {cfg.sdr_uri})")
    parser.add_argument("--callsign", default="", help=f"Callsign (default from .env: {cfg.callsign})")
    parser.add_argument("--rx-gain", type=float, default=cfg.rx_gain, help=f"RX Gain dB (default: {cfg.rx_gain})")
    parser.add_argument("--tx-atten", type=float, default=cfg.tx_atten, help=f"TX Attenuation dB (default: {cfg.tx_atten})")
    args, _ = parser.parse_known_args()

    node_str = (args.node or cfg.node_alias or "").lower()
    uri = args.uri
    callsign = args.callsign

    # Map macOS serial tty or RNDIS alias to Pluto IP URI
    if uri and ("usbmodem" in uri or "/dev/tty" in uri):
        uri = "ip:192.168.2.10"

    # Auto-detect node identity
    if node_str:
        if "pi5" in node_str or node_str in ("1", "node1"):
            uri = uri or "usb:1.3.5"
            callsign = callsign or "HQ-PI5"
        elif "2w" in node_str or "zero" in node_str or node_str in ("2", "node2") or "aml" in node_str:
            uri = uri or "ip:192.168.99.240"
            callsign = callsign or "OUTPOST-PI2W"
        elif "mac" in node_str:
            uri = uri or "ip:192.168.2.10"
            callsign = callsign or "COMMANDER-MAC"

    uri = uri or cfg.sdr_uri
    callsign = callsign or cfg.callsign

    if iio is None:
        print("[ERROR] libiio python binding is not available. Please install python3-libiio.", file=sys.stderr)
        sys.exit(1)

    chat = DirectRFChat(
        uri=uri,
        callsign=callsign,
        rx_gain=args.rx_gain,
        tx_atten=args.tx_atten,
    )
    chat.run_cli()


if __name__ == "__main__":
    main()
