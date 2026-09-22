#!/usr/bin/env python3
import time
import sys
from pathlib import Path
import numpy as np
import iio

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from tsm.modem.constants import *
from tsm.modem.dsp import modulate_2fsk

def main():
    uri = sys.argv[1] if len(sys.argv) > 1 else "usb:1.3.5"
    text = sys.argv[2] if len(sys.argv) > 2 else "HALO_OTA_TEST"
    callsign = sys.argv[3] if len(sys.argv) > 3 else "HQ-PI5"

    print(f"[*] Starting OTA TX Test on {uri} at {CARRIER_FREQ/1e6} MHz ...", flush=True)

    ctx = iio.Context(uri)
    phy = ctx.find_device("ad9361-phy")
    tx_dev = ctx.find_device("cf-ad9361-dds-core-lpc")

    phy.find_channel("altvoltage1", True).attrs["frequency"].value = str(CARRIER_FREQ)
    phy.find_channel("voltage0", True).attrs["sampling_frequency"].value = str(SAMPLE_RATE)
    phy.find_channel("voltage0", True).attrs["rf_bandwidth"].value = "1000000"
    phy.find_channel("voltage0", True).attrs["hardwaregain"].value = "0.0"

    for ch in tx_dev.channels:
        ch.enabled = ch.id in ("voltage0", "voltage1")
        if "raw" in ch.attrs:
            ch.attrs["raw"].value = "0"

    tx_buf = iio.Buffer(tx_dev, TX_BUF_SIZE, False)

    payload = callsign.encode("utf-8") + b"\x00" + text.encode("utf-8")
    iq = modulate_2fsk(payload)
    padded = np.zeros((TX_BUF_SIZE, 2), dtype=np.int16)
    padded[:len(iq)] = iq
    raw_bytes = bytearray(padded.tobytes())

    print(f"[*] Transmitting 3 bursts: '{text}' ({len(iq)} samples) ...", flush=True)
    for i in range(3):
        tx_buf.write(raw_bytes)
        tx_buf.push()
        time.sleep((len(iq) / SAMPLE_RATE) + 0.1)
        print(f"BURST_{i+1}_SENT", flush=True)

    print("TX_COMPLETE", flush=True)

if __name__ == "__main__":
    main()
