#!/usr/bin/env python3
import time
import sys
from pathlib import Path
import numpy as np
import iio

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from tsm.modem.constants import *
from tsm.modem.dsp import build_sync_template, demodulate_2fsk

def main():
    uri = sys.argv[1] if len(sys.argv) > 1 else "ip:192.168.99.240"
    print(f"[*] Starting OTA RX Test on {uri} at {CARRIER_FREQ/1e6} MHz ...", flush=True)

    sync_up, sync_len = build_sync_template()
    ctx = iio.Context(uri)
    phy = ctx.find_device("ad9361-phy")
    rx_dev = ctx.find_device("cf-ad9361-lpc")

    phy.find_channel("altvoltage0", True).attrs["frequency"].value = str(CARRIER_FREQ)
    phy.find_channel("voltage0", False).attrs["sampling_frequency"].value = str(SAMPLE_RATE)
    phy.find_channel("voltage0", False).attrs["rf_bandwidth"].value = "1000000"
    phy.find_channel("voltage0", False).attrs["gain_control_mode"].value = "manual"
    phy.find_channel("voltage0", False).attrs["hardwaregain"].value = "65.0"
    for ch in rx_dev.channels:
        ch.enabled = ch.id in ("voltage0", "voltage1")

    rx_buf = iio.Buffer(rx_dev, RX_BUF_SIZE, False)
    prev_tail = np.zeros((RX_BUF_SIZE // 2, 2), dtype=np.int16)

    print("READY_WAITING_FOR_BURST", flush=True)

    t_end = time.time() + 45.0
    while time.time() < t_end:
        rx_buf.refill()
        raw = rx_buf.read()
        samples = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2)
        comb = np.vstack((prev_tail, samples))
        prev_tail = samples[-(RX_BUF_SIZE // 2):]

        res = demodulate_2fsk(comb, sync_up, sync_len, threshold=0.40)
        if res is not None:
            payload, corr_val = res
            print(f"SUCCESS_DECODED: payload={payload} corr={corr_val:.3f}", flush=True)
            return

        # Periodically check power
        pwr = np.mean(samples[:, 0].astype(np.float64)**2 + samples[:, 1].astype(np.float64)**2)
        if pwr > 100000:
            print(f"ENERGY_DETECTED: pwr={pwr:.0f}", flush=True)

    print("TIMEOUT_NO_PACKET", flush=True)

if __name__ == "__main__":
    main()
