#!/usr/bin/env python3
"""
Tactical SDR Mesh (TSM-Net SG) CLI Tool.
Works universally from any machine (Mac, PC, Linux, Pi).
Supports both native gRPC (port 50051) and zero-dependency HTTP/REST fallback (port 8080).
"""

import os
import sys
import json
import base64
import argparse
from pathlib import Path
from urllib import request as url_request
from urllib.error import URLError

# Automatically add src directory to sys.path
REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def print_header(title: str):
    print("=" * 68)
    print(f"  {title}")
    print("=" * 68)


# ==============================================================================
# HTTP Gateway Fallback Client (Zero Dependencies, uses Python stdlib)
# ==============================================================================
class TacticalHttpClient:
    def __init__(self, host: str, port: int = 8080):
        self.base_url = f"http://{host}:{port}"

    def _get(self, path: str):
        url = f"{self.base_url}{path}"
        req = url_request.Request(url, headers={"User-Agent": "TSM-CLI/1.0"})
        with url_request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, data: dict):
        url = f"{self.base_url}{path}"
        payload = json.dumps(data).encode("utf-8")
        req = url_request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "TSM-CLI/1.0"}
        )
        with url_request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def status(self):
        print_header("TACTICAL NODE & SDR STATUS (via HTTP Gateway)")
        data = self._get("/api/telemetry")
        node = data.get("node", {})
        sdr = data.get("sdr", {})

        print(f" Node Callsign:    {node.get('node_id', 'UNKNOWN')} ({node.get('hostname', 'unknown')})")
        print(f" Mesh IP (bat0):   {node.get('mesh_ip', 'none')}")
        print(f" TAP Interface:    MAC={node.get('tap_mac', '--')} | MTU={node.get('tap_mtu', '--')}B")
        print(f" Uptime:           {node.get('uptime_sec', 0):.1f}s")
        mem_mb = node.get("mem_used_bytes", 0) // (1024 * 1024)
        total_mb = node.get("mem_total_bytes", 1) // (1024 * 1024)
        print(f" Memory:           {mem_mb} MB / {total_mb} MB")
        print(f" B.A.T.M.A.N.-adv: Active={node.get('is_mesh_active', False)} ({node.get('batman_version', 'N/A')})")

        print("\n--- Physical RF & SDR Hardware ---")
        conn_str = "CONNECTED" if sdr.get("connected") else "PEER / SYNTHETIC MODE"
        print(f" Transceiver:      {conn_str}")
        print(f" Carrier LO:       {sdr.get('center_freq_hz', 915000000) / 1e6:.3f} MHz (Locked: {sdr.get('is_locked', True)})")
        print(f" Sample Rate:      {sdr.get('sample_rate_sps', 2500000) / 1e6:.2f} MSps")
        print(f" Hardware Gains:   Rx={sdr.get('rx_gain_db', 55.0):.1f} dB | Tx={sdr.get('tx_atten_db', -10.0):.1f} dB")
        print(f" Channel Energy:   Live RSSI = {sdr.get('live_rssi_db', 0.0):.1f} dB")
        print(f" FPGA Core XADC:   Temp = {sdr.get('fpga_temp_c', 0.0):.1f} °C | VccInt = {sdr.get('vccint_v', 0.0):.2f} V")

    def neighbors(self):
        print_header("B.A.T.M.A.N.-ADV MESH TOPOLOGY & NEIGHBORS (via HTTP Gateway)")
        data = self._get("/api/neighbors")
        neighbors = data.get("neighbors", [])
        if not neighbors:
            print("  No neighbors detected in table yet. Is orchestrator running?")
            return

        print(f" Total Discovered Neighbors: {data.get('total', len(neighbors))}\n")
        print(f" {'INTERFACE':<12} {'MAC ADDRESS':<20} {'TQ METRIC':<12} {'LAST SEEN':<12} {'NEXTHOP'}")
        print("-" * 68)
        for n in neighbors:
            tq = n.get("tq_metric", 0)
            tq_pct = round((tq / 255.0) * 100)
            tq_display = f"{tq}/255 ({tq_pct}%)"
            nh_str = "YES (*)" if n.get("is_nexthop") else "NO"
            print(f" {n.get('iface', 'tap-radio'):<12} {n.get('mac', '--'):<20} {tq_display:<12} {n.get('last_seen_sec', 0.0):<10.1f}s  {nh_str}")

    def ping(self, target_ip: str):
        print_header(f"MESH ICMP PING DIAGNOSTIC -> {target_ip}")
        data = self._post("/api/ping", {"target_ip": target_ip})
        print(f" Target IP:     {data.get('target_ip', target_ip)}")
        print(f" Packets Sent:  {data.get('packets_sent', 0)} | Received: {data.get('packets_recv', 0)} | Loss: {data.get('packet_loss_pct', 0.0):.1f}%")
        if data.get("success"):
            print(f" Latency RTT:   Min={data.get('rtt_min_ms', 0):.2f}ms | Avg={data.get('rtt_avg_ms', 0):.2f}ms | Max={data.get('rtt_max_ms', 0):.2f}ms")
            print(" Status:        [PASS] Link is active and operational.")
        else:
            print(" Status:        [FAIL] Destination unreachable.")

    def chat(self, target_ip: str, text: str):
        data = self._post("/api/chat", {"target_ip": target_ip, "text": text})
        print(f"[SENT] Message ID: {data.get('message_id')} | Delivered={data.get('delivered')} | Target={target_ip}")

    def spectrum(self):
        print_header("ASCII RF SPECTRUM SCAN (915.000 MHz)")
        data = self._get("/api/spectrum/scan")
        powers_bytes = base64.b64decode(data.get("powers_b64", ""))
        num_bins = len(powers_bytes)
        if num_bins == 0:
            print("  No spectrum data received.")
            return

        # Downsample to 64 columns
        chunk_size = max(1, num_bins // 64)
        compact = []
        for i in range(64):
            chunk = powers_bytes[i * chunk_size : (i + 1) * chunk_size]
            compact.append(sum(chunk) // len(chunk) if chunk else 0)

        height = 10
        print(f" Center: {data.get('center_mhz', 915.0):.3f} MHz | Span: {data.get('span_mhz', 1.0):.1f} MHz | Peak: {data.get('peak_power_dbm', 0.0):.1f} dBm @ {data.get('peak_freq_mhz', 915.0):.4f} MHz\n")
        for row in range(height, 0, -1):
            threshold = int((row / height) * 255)
            line = "".join("█" if val >= threshold else " " for val in compact)
            dbm = int(-110 + (row / height) * 105)
            print(f" {dbm:>4} dBm | {line}")
        print("          +" + "-" * 64)
        print("           914.5 MHz                      915.0 MHz                    915.5 MHz\n")


# ==============================================================================
# CLI Entrypoint
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Tactical SDR Mesh CLI Tool")
    parser.add_argument("command", choices=["status", "neighbors", "ping", "chat", "spectrum"], help="Action to perform")
    parser.add_argument("--host", default="192.168.0.120", help="Target node host or IP (default: 192.168.0.120)")
    parser.add_argument("--grpc-port", type=int, default=50051, help="gRPC API Port (default: 50051)")
    parser.add_argument("--http-port", type=int, default=8080, help="HTTP API Port (default: 8080)")
    parser.add_argument("--target", default="10.10.0.2", help="Target IP for ping or chat")
    parser.add_argument("--message", default="", help="Message text for chat")
    parser.add_argument("--prefer-grpc", action="store_true", help="Force gRPC client instead of automatic fallback")

    args = parser.parse_args()

    # Try native gRPC if requested or available
    use_grpc = False
    if args.prefer_grpc:
        use_grpc = True
    else:
        try:
            import grpc
            from tsm.generated import tsm_pb2, tsm_pb2_grpc
            use_grpc = True
        except ImportError:
            use_grpc = False

    if use_grpc:
        try:
            from tsm.api.client import (
                cmd_status,
                cmd_neighbors,
                cmd_ping,
                cmd_send_chat,
                cmd_spectrum,
            )
            import grpc
            from tsm.generated import tsm_pb2_grpc

            target = f"{args.host}:{args.grpc_port}"
            channel = grpc.insecure_channel(target)
            stub_node = tsm_pb2_grpc.TacticalNodeServiceStub(channel)
            stub_chat = tsm_pb2_grpc.TacticalChatServiceStub(channel)
            stub_spec = tsm_pb2_grpc.TacticalSpectrumServiceStub(channel)

            if args.command == "status":
                cmd_status(stub_node, stub_node)
            elif args.command == "neighbors":
                cmd_neighbors(stub_node)
            elif args.command == "ping":
                cmd_ping(stub_node, args.target)
            elif args.command == "chat":
                if not args.message:
                    print("Error: --message required for chat")
                    sys.exit(1)
                cmd_send_chat(stub_chat, args.target, args.message)
            elif args.command == "spectrum":
                cmd_spectrum(stub_spec)
            channel.close()
            return
        except Exception as e:
            # Fall back to HTTP if gRPC connection fails or import fails
            if args.prefer_grpc:
                print(f"[ERROR] gRPC failure: {e}", file=sys.stderr)
                sys.exit(1)

    # HTTP Client Fallback (runs everywhere with zero external pip packages)
    http_client = TacticalHttpClient(args.host, args.http_port)
    try:
        if args.command == "status":
            http_client.status()
        elif args.command == "neighbors":
            http_client.neighbors()
        elif args.command == "ping":
            http_client.ping(args.target)
        elif args.command == "chat":
            if not args.message:
                print("Error: --message required for chat")
                sys.exit(1)
            http_client.chat(args.target, args.message)
        elif args.command == "spectrum":
            http_client.spectrum()
    except URLError as e:
        print(f"[ERROR] Could not connect to {args.host}:{args.http_port}: {e}", file=sys.stderr)
        print(f"[HINT] Check if the node server is running: python3 run_grpc_server.sh", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
