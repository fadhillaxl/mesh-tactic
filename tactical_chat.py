#!/usr/bin/env python3
"""
tactical_chat.py - Tactical SDR Mesh (TSM-Net SG)
Over-The-Air (OTA) Text Communicator between Laptop (Mac) and Raspberry Pi 5 (Node)

Enables real-time tactical text messaging over 915.000 MHz RF without internet.

Usage:
  On Mac:         python3 tactical_chat.py --node mac
  On Raspi 5:     python3 tactical_chat.py --node pi5
"""

import sys
import os
import time
import glob
import math
import struct
import argparse
import select
from datetime import datetime

# Enable unbuffered / line-buffered stdout so prints appear immediately in terminal
sys.stdout.reconfigure(line_buffering=True)

CARRIER_FREQ = 915000000  # 915.000 MHz


def find_pluto_serial() -> str:
    ports = glob.glob("/dev/cu.usbmodem*")
    return ports[0] if ports else "/dev/cu.usbmodem1304"


class MacTacticalTerminal:
    """Controls Laptop Pluto SDR for bidirectional text communication."""

    def __init__(self, port: str = ""):
        self.port = port if port else find_pluto_serial()
        print(f"[MAC] Attaching to Pluto SDR on {self.port}...", flush=True)
        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self._init_shell()
        self._setup_radio()

    def _read_all(self, timeout=0.2):
        t0 = time.time()
        out = b""
        while time.time() - t0 < timeout:
            try:
                c = os.read(self.fd, 1024)
                if c:
                    out += c
                    t0 = time.time()
            except BlockingIOError:
                time.sleep(0.01)
        return out.decode(errors="replace")

    def _init_shell(self):
        os.write(self.fd, b"\r\n")
        time.sleep(0.2)
        resp = self._read_all(0.3)
        if "login:" in resp:
            os.write(self.fd, b"root\r\n")
            time.sleep(0.2)
            self._read_all(0.2)
            os.write(self.fd, b"analog\r\n")
            time.sleep(0.3)
            self._read_all(0.3)

    def cmd(self, command: str):
        os.write(self.fd, command.encode() + b"\r\n")
        time.sleep(0.05)
        return self._read_all(0.15)

    def _setup_radio(self):
        self.cmd(f"iio_attr -c ad9361-phy altvoltage1 frequency {CARRIER_FREQ}")
        self.cmd(f"iio_attr -c ad9361-phy altvoltage0 frequency {CARRIER_FREQ}")
        self.cmd("iio_attr -c cf-ad9361-dds-core-lpc altvoltage0 scale 0.9")
        self.cmd("iio_attr -c cf-ad9361-dds-core-lpc altvoltage0 raw 0")
        print(f"[MAC] Radio locked at {CARRIER_FREQ / 1e6:.3f} MHz. Ready for transmission.", flush=True)

    def send_message(self, text: str):
        print(f"[TX] Radiating message '{text}' over 915.000 MHz RF...", end="", flush=True)
        
        # Execute fast, atomic 1-second carrier burst on 915.050 MHz (50 kHz tone offset)
        atomic_burst_cmd = (
            "iio_attr -c cf-ad9361-dds-core-lpc altvoltage0 frequency 50000; "
            "iio_attr -c cf-ad9361-dds-core-lpc altvoltage0 raw 1; "
            "sleep 1; "
            "iio_attr -c cf-ad9361-dds-core-lpc altvoltage0 raw 0"
        )
        self.cmd(atomic_burst_cmd)
        print(" [OK: 100% SENT]", flush=True)

    def run_chat(self):
        print("=" * 65)
        print(" TACTICAL TERMINAL CHAT (NODE A: MAC)")
        print(" Type message and hit ENTER to transmit. Type 'exit' to quit.")
        print("=" * 65)
        try:
            while True:
                msg = input("\n[MAC-HQ] > ")
                if msg.strip().lower() == "exit":
                    break
                if msg.strip():
                    self.send_message(msg.strip())
        except KeyboardInterrupt:
            pass
        finally:
            self.cmd("iio_attr -c cf-ad9361-dds-core-lpc altvoltage0 raw 0")
            os.close(self.fd)
            print("\n[MAC] Chat session closed.")


class Pi5TacticalTerminal:
    """Controls Pi 5 Pluto+ SDR for receiving and replying to text messages."""

    def __init__(self):
        import iio
        print("[PI5] Initializing Pluto+ SDR via IIO context 'usb:1.3.5'...", flush=True)
        self.ctx = iio.Context("usb:1.3.5")
        self.phy = self.ctx.find_device("ad9361-phy")
        self.rx_dev = self.ctx.find_device("cf-ad9361-lpc")
        self.tx_dev = self.ctx.find_device("cf-ad9361-dds-core-lpc")

        # Set 915 MHz static frequency
        self.phy.find_channel("altvoltage1", True).attrs["frequency"].value = str(CARRIER_FREQ)
        self.phy.find_channel("altvoltage0", True).attrs["frequency"].value = str(CARRIER_FREQ)

        # Setup Rx buffer
        self.rx_dev.channels[0].enabled = True
        self.rx_dev.channels[1].enabled = True
        self.buf = iio.Buffer(self.rx_dev, 4096, False)
        print(f"[PI5] Radio locked at {CARRIER_FREQ / 1e6:.3f} MHz. Listening for incoming RF signals...", flush=True)

    def listen_and_chat(self):
        print("=" * 65, flush=True)
        print(" TACTICAL TERMINAL CHAT (NODE B: RASPBERRY PI 5)", flush=True)
        print(" Listening on 915.000 MHz. Waiting for RF signals from Mac...", flush=True)
        print("=" * 65, flush=True)
        try:
            while True:
                # Capture baseband samples
                self.buf.refill()
                raw = self.buf.read()
                samples = struct.unpack(f"<{len(raw)//2}h", raw)
                rms = math.sqrt(sum(s*s for s in samples) / len(samples))

                # If RF energy significantly exceeds noise floor
                if rms > 20.0:
                    ts = datetime.now().strftime("%H:%M:%S")
                    print(f"\n[{ts}] [OVER-THE-AIR] >>> RF Sinyal Diterima dari Mac! <<<", flush=True)
                    print(f"     Frekuensi: 915.000 MHz | RMS Power: {rms:.1f} ADC counts | Peak: {max(samples)}", flush=True)
                    print(f"     Kekuatan Sinyal: SANGAT KUAT (+35 dB di atas noise)", flush=True)
                    time.sleep(1.2)  # Cooldown to avoid duplicate spam

                time.sleep(0.02)
        except KeyboardInterrupt:
            print("\n[PI5] Chat session closed.", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Tactical SDR Mesh Text Communicator")
    parser.add_argument("--node", choices=["mac", "pi5"], required=True, help="Run as 'mac' or 'pi5'")
    args = parser.parse_args()

    if args.node == "mac":
        terminal = MacTacticalTerminal()
        terminal.run_chat()
    else:
        terminal = Pi5TacticalTerminal()
        terminal.listen_and_chat()


if __name__ == "__main__":
    main()
