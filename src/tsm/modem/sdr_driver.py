"""
Tactical SDR Modem Driver for ADALM-PLUTO / Pluto+ SDR.
Handles hardware streaming via libiio and bridges RF baseband to Local IPC UDP sockets.
"""

import os
import sys
import time
import socket
import select
import threading
import argparse
import numpy as np

from ..common.config import load_config
from .constants import (
    CARRIER_FREQ,
    SAMPLE_RATE,
    SYMBOL_RATE,
    RX_BUF_SIZE,
    TX_BUF_SIZE,
)
from .dsp import (
    build_sync_template,
    modulate_2fsk,
    demodulate_2fsk,
)


class TacticalSDRModem:
    """Continuous baseband I/Q modem driver for Analog Devices AD9361 (Pluto SDR)."""

    def __init__(
        self,
        uri: str,
        ipc_tx_port: int = 52001,
        ipc_rx_port: int = 52002,
        rx_gain: float = 62.0,
        tx_atten: float = 0.0,
    ):
        self.uri = uri
        self.ipc_tx_port = ipc_tx_port
        self.ipc_rx_port = ipc_rx_port
        self.rx_gain = rx_gain
        self.tx_atten = tx_atten
        self.running = False
        self.lock = threading.Lock()
        self.last_tx_time = 0.0

        # Metrics
        self.tx_packets = 0
        self.rx_packets = 0
        self.crc_errors = 0

        # Precompute sync template
        self.sync_upsampled, self.sync_len_samples = build_sync_template()

        # Connect to Radio
        self._init_radio()

        # Setup Local UDP IPC Sockets with Orchestrator
        self.sock_in = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_in.bind(("127.0.0.1", self.ipc_tx_port))
        self.sock_in.setblocking(False)

        self.sock_out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        print(f"[MODEM] Tactical 2-FSK Modem Initialized.")
        print(f"        RF: {CARRIER_FREQ/1e6:.3f} MHz | Rate: {SAMPLE_RATE/1e6:.2f} MSps | Speed: {SYMBOL_RATE/1e3:.0f} kbps")
        print(f"        IPC: Ingest <- 127.0.0.1:{self.ipc_tx_port} | Egress -> 127.0.0.1:{self.ipc_rx_port}", flush=True)

    def _init_radio(self):
        import iio

        print(f"[MODEM] Connecting to Pluto SDR via IIO context '{self.uri}'...", flush=True)
        self.ctx = iio.Context(self.uri)
        self.phy = self.ctx.find_device("ad9361-phy")
        self.rx_dev = self.ctx.find_device("cf-ad9361-lpc")
        self.tx_dev = self.ctx.find_device("cf-ad9361-dds-core-lpc")

        # Configure LO Frequency
        self.phy.find_channel("altvoltage1", True).attrs["frequency"].value = str(CARRIER_FREQ)
        self.phy.find_channel("altvoltage0", True).attrs["frequency"].value = str(CARRIER_FREQ)

        # Configure Baseband Rate & Analog Filter Bandwidth
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
            ch.enabled = True
        for ch in self.tx_dev.channels:
            ch.enabled = True

        # Disable internal DDS tone generators
        for ch in self.tx_dev.channels:
            if "raw" in ch.attrs:
                ch.attrs["raw"].value = "0"

        # Allocate single reusable DMA buffers
        self.rx_buf = iio.Buffer(self.rx_dev, RX_BUF_SIZE, False)
        self.tx_buf = iio.Buffer(self.tx_dev, TX_BUF_SIZE, False)
        print(f"[MODEM] Reusable DMA Buffers Allocated: RX={RX_BUF_SIZE} | TX={TX_BUF_SIZE} samples.", flush=True)

    def transmit(self, payload: bytes):
        """Transmits payload by pushing modulated I/Q samples to Pluto Tx DMA."""
        iq = modulate_2fsk(payload)
        num_samples = len(iq)
        if num_samples > TX_BUF_SIZE:
            print(f"[WARN] Modulated frame {num_samples} samples exceeds TX_BUF_SIZE {TX_BUF_SIZE}", file=sys.stderr)
            return

        padded = np.zeros((TX_BUF_SIZE, 2), dtype=np.int16)
        padded[:num_samples] = iq
        raw_bytes = bytearray(padded.tobytes())

        with self.lock:
            self.last_tx_time = time.time()
            self.tx_buf.write(raw_bytes)
            self.tx_buf.push()

        self.tx_packets += 1
        print(f"[RF-TX] Radiated {len(payload)}B frame ({num_samples} samples, {num_samples/SAMPLE_RATE*1000:.1f}ms) at {CARRIER_FREQ/1e6:.3f} MHz.", flush=True)

    def _rx_worker(self):
        """Continuous background RX loop capturing baseband I/Q from Pluto ADC."""
        print("[MODEM] RX Worker active. Listening for 915.000 MHz over-the-air packets...", flush=True)
        prev_tail = np.zeros((RX_BUF_SIZE // 2, 2), dtype=np.int16)

        while self.running:
            try:
                # Discard hardware spillover during active DAC transmit pulse (~16ms)
                if time.time() - self.last_tx_time < 0.020:
                    self.rx_buf.refill()
                    continue

                self.rx_buf.refill()
                raw_bytes = self.rx_buf.read()
                raw_samples = np.frombuffer(raw_bytes, dtype=np.int16).reshape(-1, 2)

                combined = np.vstack((prev_tail, raw_samples))
                prev_tail = raw_samples[-(RX_BUF_SIZE // 2) :]

                result = demodulate_2fsk(combined, self.sync_upsampled, self.sync_len_samples)
                if result is not None:
                    payload, corr = result
                    self.rx_packets += 1
                    print(f"[RF-RX] Decoded {len(payload)}B frame | SyncCorr: {corr:.3f} | Total RX: {self.rx_packets}", flush=True)
                    self.sock_out.sendto(payload, ("127.0.0.1", self.ipc_rx_port))

            except Exception as e:
                if self.running:
                    time.sleep(0.01)

    def run(self):
        """Main service loop handling outgoing frames from orchestrator."""
        self.running = True
        rx_thread = threading.Thread(target=self._rx_worker, daemon=True)
        rx_thread.start()

        print("[MODEM] Service running. Ready for tactical mesh traffic.", flush=True)
        try:
            while self.running:
                r, _, _ = select.select([self.sock_in], [], [], 0.05)
                if r:
                    data, _ = self.sock_in.recvfrom(4096)
                    if data:
                        self.transmit(data)
        except (KeyboardInterrupt, SystemExit):
            print("\n[MODEM] Stopping modem...")
        finally:
            self.running = False
            self.sock_in.close()
            self.sock_out.close()
            print("[MODEM] Modem shutdown complete.", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Tactical SDR Mesh 2-FSK Modem Service")
    parser.add_argument("--config", default="config/config.yaml", help="Path to config.yaml")
    parser.add_argument("--uri", default="", help="Pluto SDR IIO context URI")
    parser.add_argument("--rx-gain", type=float, default=None, help="RX hardware gain in dB (0-73)")
    parser.add_argument("--tx-atten", type=float, default=None, help="TX attenuation in dB (-89 to 0)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    uri = args.uri or cfg.sdr.uri
    rx_gain = args.rx_gain if args.rx_gain is not None else cfg.sdr.rx_gain
    tx_atten = args.tx_atten if args.tx_atten is not None else cfg.sdr.tx_gain

    modem = TacticalSDRModem(
        uri=uri,
        ipc_tx_port=cfg.network.ipc_tx_port,
        ipc_rx_port=cfg.network.ipc_rx_port,
        rx_gain=rx_gain,
        tx_atten=tx_atten,
    )
    modem.run()


if __name__ == "__main__":
    main()
