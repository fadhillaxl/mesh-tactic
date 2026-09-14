#!/usr/bin/env python3
"""
gr_lora_pluto_bridge.py - Tactical SDR Mesh (TSM-Net SG)
Physical Layer Bridge: GNU Radio 3.10 + gr-lora_sdr + ADALM-Pluto / Pluto+ SDR

Responsibilities:
1. Locks Pluto+ SDR to a single STATIC Local Oscillator (LO) frequency (e.g., 915 MHz).
2. Connects to `tsm_mesh_orchestrator.py` via asynchronous loopback UDP Socket PDUs.
3. Transmit (Tx): Encapsulated UDP PDUs -> lora_sdr.header -> lora_sdr.modulate -> Pluto Sink.
4. Receive (Rx): Pluto Source -> lora_sdr.frame_sync -> lora_sdr.dewhitening -> lora_sdr.decode -> UDP PDU.

Philosophy (DietrichGebert/ponytail):
- Zero-bloat, direct GNU Radio flowgraph.
- Decoupled from kernel networking using standard localhost UDP Socket PDUs.
"""

import sys
import os
import signal
import yaml
import argparse
from typing import Dict, Any

try:
    from gnuradio import gr, network, blocks
    import gnuradio.iio as iio
    import gnuradio.lora_sdr as lora_sdr
    import pmt
except ImportError as e:
    print(f"[WARN] GNU Radio / IIO / gr-lora_sdr import failed: {e}", file=sys.stderr)
    print("[INFO] If running on Raspberry Pi 5, ensure 'gnuradio' and 'gr-lora_sdr' are installed.", file=sys.stderr)


class LoRaPlutoStaticBridge(gr.top_block):
    """
    GNU Radio Top Block bridging Pluto+ SDR at a fixed static frequency
    with tapparelj/gr-lora_sdr physical layer modulation.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("LoRaPlutoStaticBridge")

        sdr_cfg = config["sdr"]
        lora_cfg = config["lora"]
        net_cfg = config["network"]

        # ----------------------------------------------------------------------
        # 1. PARAMETERS EXTRACTED FROM CONFIG
        # ----------------------------------------------------------------------
        self.uri = sdr_cfg.get("uri", "ip:192.168.2.1")
        self.center_freq = int(sdr_cfg.get("center_freq", 915000000))
        self.sample_rate = int(sdr_cfg.get("sample_rate", 1000000))
        self.bandwidth = int(sdr_cfg.get("bandwidth", 1000000))
        self.buffer_size = int(sdr_cfg.get("buffer_size", 32768))
        self.tx_gain = float(sdr_cfg.get("tx_gain", -10.0))
        self.rx_gain = float(sdr_cfg.get("rx_gain", 55.0))
        self.rx_gain_mode = sdr_cfg.get("rx_gain_mode", "manual")

        self.sf = int(lora_cfg.get("spreading_factor", 7))
        self.lora_bw = int(lora_cfg.get("bandwidth", 125000))
        self.cr = int(lora_cfg.get("code_rate", 1))
        self.sync_word = [int(lora_cfg.get("sync_word", 0x12))]
        self.has_crc = bool(lora_cfg.get("has_crc", True))
        self.impl_header = bool(lora_cfg.get("impl_header", False))
        self.ldro = int(lora_cfg.get("ldro", 0))

        self.ipc_tx_port = int(net_cfg.get("ipc_tx_port", 52001))
        self.ipc_rx_port = int(net_cfg.get("ipc_rx_port", 52002))

        print(f"[BRIDGE] Initializing Pluto+ SDR at STATIC Frequency: {self.center_freq / 1e6:.3f} MHz")
        print(f"[BRIDGE] Sample Rate: {self.sample_rate / 1e6:.2f} MSps | LoRa BW: {self.lora_bw / 1e3:.1f} kHz | SF: {self.sf}")
        print(f"[BRIDGE] Pluto URI: {self.uri} | Tx Gain: {self.tx_gain} dB | Rx Gain: {self.rx_gain} dB")

        # ----------------------------------------------------------------------
        # 2. SDR HARDWARE BLOCKS (PLUTO+ via LIBIIO)
        # ----------------------------------------------------------------------
        # Note: Pluto+ SDR uses AD9363/AD9364 transceiver over IIO.
        # Fixed static LO frequency applied to both Rx and Tx chains.
        self.pluto_sink = iio.pluto_sink(
            self.uri,
            self.center_freq,    # Fixed Static Tx LO Frequency
            self.sample_rate,
            self.bandwidth,
            self.buffer_size,
            False,               # Cyclic buffer: False for streaming packets
            self.tx_gain,        # Hardware attenuation in dB
            "",                  # Filter file: None
            True                 # Auto filter: True
        )

        self.pluto_source = iio.pluto_source(
            self.uri,
            self.center_freq,    # Fixed Static Rx LO Frequency
            self.sample_rate,
            self.bandwidth,
            self.buffer_size,
            False,               # Decimation/Quadrature
            self.rx_gain_mode,   # "manual"
            self.rx_gain,        # Hardware amplification gain (dB)
            "",                  # Filter file: None
            True                 # Auto filter: True
        )

        # ----------------------------------------------------------------------
        # 3. LORA MODULATION CHAIN (TRANSMITTER)
        # ----------------------------------------------------------------------
        # Ingests raw PDU messages from localhost UDP port 52001
        self.tx_socket_pdu = network.socket_pdu("UDP_SERVER", "127.0.0.1", str(self.ipc_tx_port), 10000)

        # LoRa Header & Modulator blocks (gr-lora_sdr)
        self.lora_header = lora_sdr.header(
            self.impl_header,
            self.has_crc,
            self.cr
        )

        self.lora_modulator = lora_sdr.modulate(
            self.sf,
            self.lora_bw,
            self.sample_rate,
            self.sync_word
        )

        # Connect TX Message and Streaming paths
        self.msg_connect((self.tx_socket_pdu, "pdus"), (self.lora_header, "msg"))
        self.msg_connect((self.lora_header, "msg"), (self.lora_modulator, "msg"))
        self.connect((self.lora_modulator, 0), (self.pluto_sink, 0))

        # ----------------------------------------------------------------------
        # 4. LORA DEMODULATION CHAIN (RECEIVER)
        # ----------------------------------------------------------------------
        # LoRa Frame Synchronization & Channel Estimation
        self.lora_frame_sync = lora_sdr.frame_sync(
            self.sf,
            self.lora_bw,
            self.sample_rate,
            self.sync_word,
            self.impl_header,
            self.has_crc,
            self.cr,
            self.ldro
        )

        # Header Decoder
        self.lora_header_decoder = lora_sdr.header_decoder(
            self.impl_header,
            self.has_crc,
            self.cr,
            self.ldro
        )

        # Dewhitening & Symbol Decoder
        self.lora_dewhitening = lora_sdr.dewhitening()
        self.lora_decoder = lora_sdr.decode(self.has_crc)

        # UDP PDU Output Socket (transmits decoded packets to orchestrator port 52002)
        self.rx_socket_pdu = network.socket_pdu("UDP_CLIENT", "127.0.0.1", str(self.ipc_rx_port), 10000)

        # Connect RX Streaming path
        self.connect((self.pluto_source, 0), (self.lora_frame_sync, 0))

        # Connect RX Message passing chain
        self.msg_connect((self.lora_frame_sync, "frames"), (self.lora_header_decoder, "frames"))
        self.msg_connect((self.lora_header_decoder, "payload"), (self.lora_dewhitening, "payload"))
        self.msg_connect((self.lora_dewhitening, "dewhitened"), (self.lora_decoder, "dewhitened"))
        self.msg_connect((self.lora_decoder, "out"), (self.rx_socket_pdu, "pdus"))

        print("[BRIDGE] Physical layer flowgraph assembled successfully.")


def main():
    parser = argparse.ArgumentParser(description="TSM-Net SG GNU Radio Pluto+ LoRa Physical Bridge")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"[ERROR] Config file '{args.config}' not found.", file=sys.stderr)
        sys.exit(1)

    with open(args.config, "r") as f:
        config = yaml.safe_load(f)

    tb = LoRaPlutoStaticBridge(config)

    def sig_handler(sig, frame):
        print("\n[BRIDGE] Received termination signal. Stopping GNU Radio flowgraph...")
        tb.stop()
        tb.wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    print("[BRIDGE] Starting Pluto+ SDR static transceiver loop. Press Ctrl+C to stop.")
    tb.start()
    tb.wait()


if __name__ == "__main__":
    main()
