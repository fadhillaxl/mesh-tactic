#!/usr/bin/env python3
"""
Test direct OTA RF Link between raspi5 and raspi2w from host workstation.
"""

import subprocess
import time
import sys

def main():
    print("[1] Launching listener on raspi2w (OUTPOST-PI2W)...", flush=True)
    rx_proc = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "raspi2w@192.168.0.95", "python3", "-u", "/home/raspi2w/mesh-tactic/tools/test_ota_rx.py", "ip:192.168.99.240"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    ready = False
    for line in rx_proc.stdout:
        clean = line.strip()
        print(f"  [PI2W-RX] {clean}", flush=True)
        if "READY_WAITING_FOR_BURST" in clean:
            ready = True
            break

    if not ready:
        print("[ERROR] Pi 2W failed to enter listening state.", flush=True)
        rx_proc.terminate()
        return

    time.sleep(1.0)
    print("\n[2] Transmitting OTA test packet from raspi5 (HQ-PI5)...", flush=True)
    tx_proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "raspi5", "python3", "/home/raspi5/mesh-tactic/tools/test_ota_tx.py", "usb:1.3.5", "HALO_PI2W_DARI_PI5_OTA", "HQ-PI5"],
        capture_output=True,
        text=True
    )
    print(tx_proc.stdout)
    if tx_proc.stderr:
        print(f"[TX STDERR] {tx_proc.stderr}")

    print("[3] Monitoring Pi 2W for decoded packet...", flush=True)
    decoded = False
    for line in rx_proc.stdout:
        clean = line.strip()
        print(f"  [PI2W-RX] {clean}", flush=True)
        if "SUCCESS_DECODED" in clean:
            decoded = True
            break
        if "TIMEOUT" in clean:
            break

    rx_proc.terminate()
    if decoded:
        print("\n>>> SUCCESS: OTA RF LINK VERIFIED 100% OPERATIONAL! <<<", flush=True)
    else:
        print("\n>>> FAILED: Packet not decoded on Pi 2W <<<", flush=True)

if __name__ == "__main__":
    main()
