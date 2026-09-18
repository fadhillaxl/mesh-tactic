"""
Unified Tactical SDR Mesh Micro-Server.
Runs high-performance gRPC server on port 50051 and embedded HTTP/SSE micro-gateway on port 8080.
Memory footprint: < 25 MB RAM total. Zero Node.js / zero external runtime needed.
"""

import os
import sys
import time
import json
import base64
import signal
import argparse
import threading
from concurrent import futures
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from pathlib import Path

import grpc
try:
    from grpc_reflection.v1alpha import reflection
except ImportError:
    reflection = None

from ..generated import tsm_pb2, tsm_pb2_grpc
from .servicer import (
    TacticalNodeServicer,
    TacticalChatServicer,
    TacticalSpectrumServicer,
    get_local_mesh_ip,
    get_callsign,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multithreaded HTTP Server to support concurrent long-lived SSE streams & REST requests."""
    daemon_threads = True
    allow_reuse_address = True


# ==============================================================================
# HTTP & SSE Micro-Gateway Request Handler
# ==============================================================================
class TacticalWebHandler(SimpleHTTPRequestHandler):
    """Serves static Cyber-HUD UI and provides REST / SSE endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self):
        # Enable CORS for development
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path in ("/api/telemetry", "/api/status"):
            self._handle_get_telemetry()
        elif parsed.path == "/api/neighbors":
            self._handle_get_neighbors()
        elif parsed.path == "/api/spectrum/scan":
            self._handle_get_spectrum_scan()
        elif parsed.path == "/api/events":
            self._handle_sse_stream()
        elif parsed.path == "/api/history":
            self._handle_get_history()
        else:
            # Fallback to serving static UI files (index.html, styles.css, app.js)
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        content_length = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            payload = json.loads(post_body)
        except Exception:
            payload = {}

        if parsed.path == "/api/chat":
            self._handle_post_chat(payload)
        elif parsed.path == "/api/ping":
            self._handle_post_ping(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_get_telemetry(self):
        node_servicer: TacticalNodeServicer = self.server.node_servicer
        info = node_servicer.GetNodeInfo(tsm_pb2.NodeInfoRequest(), None)
        neighbors = node_servicer.GetMeshNeighbors(tsm_pb2.MeshNeighborsRequest(), None)
        sdr = node_servicer.GetSDRTelemetry(tsm_pb2.SDRTelemetryRequest(), None)
        stats = node_servicer.GetOrchestratorStats(tsm_pb2.OrchestratorStatsRequest(), None)

        data = {
            "node": {
                "hostname": info.hostname,
                "node_id": info.node_id,
                "mesh_ip": info.mesh_ip,
                "tap_mac": info.tap_mac,
                "tap_mtu": info.tap_mtu,
                "uptime_sec": info.uptime_sec,
                "mem_used_bytes": info.mem_used_bytes,
                "mem_total_bytes": info.mem_total_bytes,
                "batman_version": info.batman_version,
                "is_mesh_active": info.is_mesh_active,
            },
            "neighbors": [
                {
                    "mac": n.mac_address,
                    "iface": n.interface,
                    "last_seen_sec": n.last_seen_sec,
                    "tq_metric": n.tq_metric,
                    "is_nexthop": n.is_nexthop,
                }
                for n in neighbors.neighbors
            ],
            "sdr": {
                "connected": sdr.sdr_connected,
                "center_freq_hz": sdr.center_freq_hz,
                "sample_rate_sps": sdr.sample_rate_sps,
                "rx_gain_db": sdr.rx_gain_db,
                "tx_atten_db": sdr.tx_atten_db,
                "live_rssi_db": sdr.live_rssi_db,
                "fpga_temp_c": sdr.fpga_temp_c,
                "vccint_v": sdr.vccint_v,
                "is_locked": sdr.is_lo_locked,
            },
            "stats": {
                "tx_frames": stats.tx_frames,
                "rx_frames": stats.rx_frames,
                "crc_drops": stats.crc_drops,
                "echo_drops": stats.echo_drops,
            },
        }

        resp_bytes = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def _handle_get_neighbors(self):
        node_servicer: TacticalNodeServicer = self.server.node_servicer
        neighbors = node_servicer.GetMeshNeighbors(tsm_pb2.MeshNeighborsRequest(), None)
        data = {
            "total": neighbors.total_neighbors,
            "neighbors": [
                {
                    "mac": n.mac_address,
                    "iface": n.interface,
                    "last_seen_sec": n.last_seen_sec,
                    "tq_metric": n.tq_metric,
                    "is_nexthop": n.is_nexthop,
                }
                for n in neighbors.neighbors
            ],
        }
        resp_bytes = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def _handle_get_spectrum_scan(self):
        spectrum_servicer: TacticalSpectrumServicer = self.server.spectrum_servicer
        resp = spectrum_servicer.GetSpectrumScan(tsm_pb2.SpectrumRequest(), None)
        scan = resp.scan
        data = {
            "center_mhz": scan.center_freq_hz / 1e6,
            "span_mhz": scan.span_hz / 1e6,
            "num_bins": scan.num_bins,
            "powers_b64": base64.b64encode(scan.powers_uint8).decode("ascii"),
            "peak_freq_mhz": round(scan.peak_freq_hz / 1e6, 4),
            "peak_power_dbm": round(scan.peak_power_dbm, 1),
        }
        resp_bytes = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def _handle_get_history(self):
        chat_servicer: TacticalChatServicer = self.server.chat_servicer
        hist = chat_servicer.GetMessageHistory(tsm_pb2.MessageHistoryRequest(limit=50), None)
        data = [
            {
                "id": m.message_id,
                "timestamp": m.timestamp,
                "sender_callsign": m.sender_callsign,
                "sender_ip": m.sender_ip,
                "target_ip": m.target_ip,
                "text": m.text,
            }
            for m in hist.messages
        ]
        resp_bytes = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def _handle_post_chat(self, payload: dict):
        chat_servicer: TacticalChatServicer = self.server.chat_servicer
        target = payload.get("target_ip", "")
        text = payload.get("text", "")

        res = chat_servicer.SendMessage(tsm_pb2.ChatMessageRequest(target_ip=target, text=text), None)
        resp_bytes = json.dumps({"message_id": res.message_id, "delivered": res.delivered}).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def _handle_post_ping(self, payload: dict):
        node_servicer: TacticalNodeServicer = self.server.node_servicer
        target = payload.get("target_ip", "10.10.0.2")
        count = int(payload.get("count", 3))

        res = node_servicer.ExecutePing(tsm_pb2.PingRequest(target_ip=target, count=count), None)
        resp_bytes = json.dumps({
            "target_ip": res.target_ip,
            "packets_sent": res.packets_sent,
            "packets_recv": res.packets_recv,
            "packet_loss_pct": res.packet_loss_pct,
            "rtt_min_ms": res.rtt_min_ms,
            "rtt_avg_ms": res.rtt_avg_ms,
            "rtt_max_ms": res.rtt_max_ms,
            "success": res.is_success,
        }).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def _handle_sse_stream(self):
        """Server-Sent Events stream for telemetry, chat messages, and spectrum scans."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        node_servicer: TacticalNodeServicer = self.server.node_servicer
        chat_servicer: TacticalChatServicer = self.server.chat_servicer
        spectrum_servicer: TacticalSpectrumServicer = self.server.spectrum_servicer

        # Subscribe to chat queue
        import queue
        chat_sub = queue.Queue(maxsize=50)
        with chat_servicer.lock:
            chat_servicer.subscribers.add(chat_sub)

        last_telemetry_time = 0.0

        try:
            while self.server.running:
                now = time.time()

                # 1. Telemetry heartbeat every 2 seconds
                if now - last_telemetry_time >= 2.0:
                    info = node_servicer.GetNodeInfo(tsm_pb2.NodeInfoRequest(), None)
                    neighbors = node_servicer.GetMeshNeighbors(tsm_pb2.MeshNeighborsRequest(), None)
                    sdr = node_servicer.GetSDRTelemetry(tsm_pb2.SDRTelemetryRequest(), None)
                    stats = node_servicer.GetOrchestratorStats(tsm_pb2.OrchestratorStatsRequest(), None)

                    t_data = {
                        "node_id": info.node_id,
                        "mesh_ip": info.mesh_ip,
                        "uptime_sec": int(info.uptime_sec),
                        "mem_used_mb": round(info.mem_used_bytes / (1024 * 1024), 1),
                        "mem_total_mb": round(info.mem_total_bytes / (1024 * 1024), 1),
                        "neighbors_count": neighbors.total_neighbors,
                        "neighbors": [
                            {"mac": n.mac_address, "tq": n.tq_metric, "last_seen": n.last_seen_sec}
                            for n in neighbors.neighbors
                        ],
                        "sdr_connected": sdr.sdr_connected,
                        "freq_mhz": sdr.center_freq_hz / 1e6,
                        "rssi_db": sdr.live_rssi_db,
                        "fpga_temp_c": sdr.fpga_temp_c,
                        "tx_frames": stats.tx_frames,
                        "rx_frames": stats.rx_frames,
                    }
                    msg = f"event: telemetry\ndata: {json.dumps(t_data)}\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                    last_telemetry_time = now

                # 2. Check for new chat messages
                try:
                    chat_msg = chat_sub.get_nowait()
                    c_data = {
                        "id": chat_msg.message_id,
                        "timestamp": chat_msg.timestamp,
                        "sender": chat_msg.sender_callsign,
                        "sender_ip": chat_msg.sender_ip,
                        "text": chat_msg.text,
                    }
                    msg = f"event: chat\ndata: {json.dumps(c_data)}\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    pass

                # 3. Spectrum scan update (5 Hz = 200 ms interval)
                scan = spectrum_servicer._acquire_spectrum_scan()
                b64_powers = base64.b64encode(scan.powers_uint8).decode("ascii")
                s_data = {
                    "center_mhz": scan.center_freq_hz / 1e6,
                    "span_mhz": scan.span_hz / 1e6,
                    "num_bins": scan.num_bins,
                    "powers_b64": b64_powers,
                    "peak_freq_mhz": round(scan.peak_freq_hz / 1e6, 4),
                    "peak_power_dbm": scan.peak_power_dbm,
                }
                msg = f"event: spectrum\ndata: {json.dumps(s_data)}\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()

                time.sleep(0.18)  # ~5.5 Hz update rate (extremely light on network & CPU)

        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with chat_servicer.lock:
                chat_servicer.subscribers.discard(chat_sub)


# ==============================================================================
# Micro Server Main Execution
# ==============================================================================
class TacticalMicroServer:
    """Manages concurrent gRPC and Web gateway daemons."""

    def __init__(self, grpc_port: int = 50051, http_port: int = 8080, sdr_uri: str = "usb:1.3.5"):
        self.grpc_port = grpc_port
        self.http_port = http_port
        self.sdr_uri = sdr_uri
        self.running = False

        # Servicers
        self.node_servicer = TacticalNodeServicer(sdr_uri=self.sdr_uri)
        self.chat_servicer = TacticalChatServicer()
        self.spectrum_servicer = TacticalSpectrumServicer(sdr_uri=self.sdr_uri)

        # gRPC Server
        self.grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        tsm_pb2_grpc.add_TacticalNodeServiceServicer_to_server(self.node_servicer, self.grpc_server)
        tsm_pb2_grpc.add_TacticalChatServiceServicer_to_server(self.chat_servicer, self.grpc_server)
        tsm_pb2_grpc.add_TacticalSpectrumServiceServicer_to_server(self.spectrum_servicer, self.grpc_server)

        # Enable Reflection for grpcurl / Postman (if available)
        if reflection is not None:
            SERVICE_NAMES = (
                tsm_pb2.DESCRIPTOR.services_by_name["TacticalNodeService"].full_name,
                tsm_pb2.DESCRIPTOR.services_by_name["TacticalChatService"].full_name,
                tsm_pb2.DESCRIPTOR.services_by_name["TacticalSpectrumService"].full_name,
                reflection.SERVICE_NAME,
            )
            reflection.enable_server_reflection(SERVICE_NAMES, self.grpc_server)
        self.grpc_server.add_insecure_port(f"0.0.0.0:{self.grpc_port}")

        # HTTP Server (Multithreaded for concurrent SSE streams + REST APIs)
        self.http_server = ThreadedHTTPServer(("0.0.0.0", self.http_port), TacticalWebHandler)
        self.http_server.node_servicer = self.node_servicer
        self.http_server.chat_servicer = self.chat_servicer
        self.http_server.spectrum_servicer = self.spectrum_servicer
        self.http_server.running = True

    def start(self):
        self.running = True
        self.grpc_server.start()
        print(f"[gRPC] Tactical SDR Mesh gRPC Server running on 0.0.0.0:{self.grpc_port}")

        # Start HTTP server in background thread
        http_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        http_thread.start()
        print(f"[HTTP] Cyber-HUD Web Dashboard & SSE Gateway running on http://0.0.0.0:{self.http_port}")

        local_ip = get_local_mesh_ip()
        callsign = get_callsign(local_ip)
        print(f"[NODE] Identified as: {callsign} (Mesh IP: {local_ip})")
        print(f"       Open in browser: http://localhost:{self.http_port} or http://{os.uname().nodename}.local:{self.http_port}")

        def _sig_handler(sig, frame):
            print("\n[SHUTDOWN] Stopping gRPC and HTTP servers...")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        self.running = False
        self.http_server.running = False
        self.chat_servicer.close()
        self.grpc_server.stop(grace=1.0)
        self.http_server.shutdown()
        print("[SHUTDOWN] Tactical Micro-Server gracefully stopped.")


def main():
    parser = argparse.ArgumentParser(description="Tactical SDR Mesh Unified gRPC & Web Server")
    parser.add_argument("--grpc-port", type=int, default=50051, help="gRPC server port (default: 50051)")
    parser.add_argument("--http-port", type=int, default=8080, help="HTTP/UI server port (default: 8080)")
    parser.add_argument("--sdr-uri", type=str, default="usb:1.3.5", help="Pluto SDR URI")
    args = parser.parse_args()

    server = TacticalMicroServer(grpc_port=args.grpc_port, http_port=args.http_port, sdr_uri=args.sdr_uri)
    server.start()


if __name__ == "__main__":
    main()
