"""
Tactical SDR Mesh (TSM-Net SG) Interactive Chat Application.
Enables real-time command post text communication over bat0 MANET @ 915 MHz SDR.
"""

import sys
import socket
import threading
import time
import argparse
from datetime import datetime

CHAT_PORT = 9999


def get_node_defaults():
    """Attempts to auto-detect local bat0 IP address and assign callsign."""
    try:
        import subprocess

        out = subprocess.check_output("ip -4 addr show bat0 | grep inet", shell=True).decode()
        ip = out.strip().split()[1].split("/")[0]
    except Exception:
        ip = "10.10.0.1"

    if ip == "10.10.0.1":
        node_name = "HQ-PI5"
        target_ip = "10.10.0.2"
        target_name = "OUTPOST-PI2W"
    else:
        node_name = "OUTPOST-PI2W"
        target_ip = "10.10.0.1"
        target_name = "HQ-PI5"

    return ip, node_name, target_ip, target_name


def rx_listener(sock: socket.socket, node_name: str):
    """Background listener for incoming chat messages over the mesh."""
    while True:
        try:
            data, addr = sock.recvfrom(2048)
            msg = data.decode("utf-8", errors="replace")
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\r\033[K[{ts}] \033[1;32m[RF-RX from {addr[0]}]\033[0m {msg}\n[{node_name}] > ", end="", flush=True)
        except Exception:
            break


def main():
    parser = argparse.ArgumentParser(description="Tactical SDR Mesh Chat Terminal")
    parser.add_argument("--node", choices=["pi5", "pi2w", "auto"], default="auto", help="Node identity")
    parser.add_argument("--target", default=None, help="Target bat0 IP address")
    args = parser.parse_args()

    detected_ip, auto_name, auto_target, target_name = get_node_defaults()

    if args.node == "pi5":
        bind_ip = "10.10.0.1"
        node_name = "HQ-PI5"
        target_ip = args.target or "10.10.0.2"
    elif args.node == "pi2w":
        bind_ip = "10.10.0.2"
        node_name = "OUTPOST-PI2W"
        target_ip = args.target or "10.10.0.1"
    else:
        bind_ip = detected_ip
        node_name = auto_name
        target_ip = args.target or auto_target

    print("=" * 68)
    print("       TACTICAL SDR MESH (TSM-Net SG) TERMINAL CHAT")
    print(f" Node:       {node_name} ({bind_ip})")
    print(f" Target:     {target_ip} (over bat0 MANET @ 915.000 MHz continuous I/Q)")
    print(" Status:     ONLINE (ZeroTier Bypassed - Pure SDR Air-Link)")
    print(" Command:    Ketik pesan lalu tekan [ENTER]. Ketik 'exit' untuk keluar.")
    print("=" * 68)

    rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        rx_sock.bind((bind_ip, CHAT_PORT))
    except Exception:
        rx_sock.bind(("0.0.0.0", CHAT_PORT))

    tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    listener_thread = threading.Thread(target=rx_listener, args=(rx_sock, node_name), daemon=True)
    listener_thread.start()

    time.sleep(0.2)
    try:
        while True:
            msg = input(f"[{node_name}] > ").strip()
            if not msg:
                continue
            if msg.lower() in ["exit", "quit"]:
                print("\n[INFO] Menutup tactical chat...")
                break

            full_msg = f"<{node_name}>: {msg}"
            tx_sock.sendto(full_msg.encode("utf-8"), (target_ip, CHAT_PORT))
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] \033[1;34m[RF-TX -> {target_ip}]\033[0m {msg}")
    except (KeyboardInterrupt, EOFError):
        print("\n[INFO] Selesai.")
    finally:
        rx_sock.close()
        tx_sock.close()


if __name__ == "__main__":
    main()
