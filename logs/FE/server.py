#!/usr/bin/env python3
"""
Tactical Railway AIS Frontend Server.
Lightweight, zero-dependency HTTP & SSE server that streams telemetry
records from gateway_telemetry.jsonl and serves GeoJSON railway corridors.

Pure Python standard library (http.server, json, threading, pathlib).
"""

import os
import sys
import time
import json
import socket
import argparse
import mimetypes
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import List, Dict, Any, Optional

# File Paths
FE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FE_DIR.parent.parent
LOGS_DIR = REPO_ROOT / "logs"
GEOJSON_DIR = REPO_ROOT / "geojson"
TELEMETRY_LOG = LOGS_DIR / "gateway_telemetry.jsonl"


def parse_telemetry_lines(max_lines: Optional[int] = None) -> List[Dict[str, Any]]:
    """Reads and parses valid JSON objects from the gateway log file."""
    if not TELEMETRY_LOG.is_file():
        return []

    records = []
    try:
        with open(TELEMETRY_LOG, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"[WARN] Error reading {TELEMETRY_LOG}: {e}", file=sys.stderr)

    if max_lines is not None and len(records) > max_lines:
        return records[-max_lines:]
    return records


def get_tracked_trains_summary(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Builds a map of latest state per train from historical records."""
    trains = {}
    for r in records:
        telem = r.get("telemetry", {})
        tid = telem.get("train_id")
        if not tid:
            continue

        trains[tid] = {
            "train_id": tid,
            "train_id_int": telem.get("train_id_int"),
            "last_seen_iso": r.get("received_time_iso"),
            "last_seen_ts": r.get("received_timestamp"),
            "gps": telem.get("gps", {}),
            "motion": telem.get("motion", {}),
            "device_health": telem.get("device_health", {}),
            "gateway": r.get("gateway", {}),
            "total_packets": trains.get(tid, {}).get("total_packets", 0) + 1,
        }
    return trains


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handles requests in a separate thread for concurrent SSE streaming."""
    daemon_threads = True
    allow_reuse_address = True


class AISRequestHandler(BaseHTTPRequestHandler):
    """Custom request handler with SSE streaming and JSON endpoints."""

    def log_message(self, format, *args):
        # Silence standard static file access logs; only log errors & API calls
        if "/api/" in (args[0] if args else "") or int(args[1] if len(args) > 1 else 200) >= 400:
            super().log_message(format, *args)

    def do_OPTIONS(self):
        """CORS preflight handling."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        req_path = self.path.split("?")[0]

        # 1. API: Full Telemetry + Summary
        if req_path == "/api/telemetry":
            self._handle_api_telemetry()
            return

        # 2. API: Server-Sent Events (SSE) Stream
        elif req_path == "/api/stream":
            self._handle_api_stream()
            return

        # 3. API: GeoJSON Track Geometries
        elif req_path.startswith("/api/geojson/"):
            filename = req_path[len("/api/geojson/"):]
            self._handle_api_geojson(filename)
            return

        # 4. Static Files (index.html, styles.css, app.js, assets)
        else:
            self._handle_static_file(req_path)

    def _send_cors_headers(self, content_type: str = "application/json"):
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")

    def _handle_api_telemetry(self):
        records = parse_telemetry_lines()
        trains = get_tracked_trains_summary(records)

        # Gateway Station info
        station_name = "Titik Tengah Utama (Stasiun Rendeh)"
        gw_lat = -6.58025
        gw_lon = 107.24695
        if records:
            last_gw = records[-1].get("gateway", {})
            station_name = last_gw.get("station_name", station_name)
            gw_lat = last_gw.get("gps", {}).get("latitude", gw_lat)
            gw_lon = last_gw.get("gps", {}).get("longitude", gw_lon)

        response = {
            "status": "ok",
            "gateway": {
                "station_name": station_name,
                "node_id": "0x0001",
                "gps": {"latitude": gw_lat, "longitude": gw_lon},
            },
            "total_packets": len(records),
            "trains_count": len(trains),
            "trains": trains,
            "recent_packets": records[-100:] if len(records) > 100 else records,
        }

        body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self._send_cors_headers("application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_api_geojson(self, filename: str):
        safe_name = os.path.basename(filename)
        target = GEOJSON_DIR / safe_name
        if not target.is_file():
            self.send_error(404, f"GeoJSON file '{safe_name}' not found")
            return

        try:
            content = target.read_bytes()
            self.send_response(200)
            self._send_cors_headers("application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error reading GeoJSON: {e}")

    def _handle_api_stream(self):
        """Tails gateway_telemetry.jsonl and pushes SSE events to client."""
        self.send_response(200)
        self._send_cors_headers("text/event-stream")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        # Send initial snapshot of recent 25 packets
        initial_records = parse_telemetry_lines(max_lines=25)
        for r in initial_records:
            msg = f"event: telemetry\ndata: {json.dumps(r, ensure_ascii=False)}\n\n"
            try:
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

        # Tail the file continuously
        last_inode = None
        f = None
        last_pos = 0

        try:
            if TELEMETRY_LOG.is_file():
                f = open(TELEMETRY_LOG, "r", encoding="utf-8", errors="replace")
                f.seek(0, os.SEEK_END)
                last_pos = f.tell()
                last_inode = os.fstat(f.fileno()).st_ino

            keepalive_timer = time.time()

            while True:
                time.sleep(0.3)

                # Send keep-alive comment every 15 seconds to prevent browser timeout
                if time.time() - keepalive_timer > 15.0:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    keepalive_timer = time.time()

                if not TELEMETRY_LOG.is_file():
                    continue

                # Check if file was rotated or created
                if f is None or f.closed:
                    f = open(TELEMETRY_LOG, "r", encoding="utf-8", errors="replace")
                    last_pos = 0
                    last_inode = os.fstat(f.fileno()).st_ino
                else:
                    curr_stat = os.stat(TELEMETRY_LOG)
                    if curr_stat.st_ino != last_inode:
                        f.close()
                        f = open(TELEMETRY_LOG, "r", encoding="utf-8", errors="replace")
                        last_pos = 0
                        last_inode = curr_stat.st_ino

                f.seek(last_pos)
                lines = f.readlines()
                last_pos = f.tell()

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        msg = f"event: telemetry\ndata: {json.dumps(record, ensure_ascii=False)}\n\n"
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                        keepalive_timer = time.time()
                    except json.JSONDecodeError:
                        continue
                    except (BrokenPipeError, ConnectionResetError):
                        return

        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if f and not f.closed:
                f.close()

    def _handle_static_file(self, req_path: str):
        if req_path in ("/", ""):
            filename = "index.html"
        else:
            filename = req_path.lstrip("/")

        target = (FE_DIR / filename).resolve()

        # Prevent directory traversal
        if not str(target).startswith(str(FE_DIR)):
            self.send_error(403, "Access Forbidden")
            return

        if not target.is_file():
            self.send_error(404, f"File '{filename}' not found")
            return

        mime_type, _ = mimetypes.guess_type(str(target))
        if mime_type is None:
            mime_type = "application/octet-stream"

        try:
            content = target.read_bytes()
            self.send_response(200)
            self._send_cors_headers(mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error serving file: {e}")


def main():
    parser = argparse.ArgumentParser(description="Tactical Railway AIS Frontend Web Server")
    parser.add_argument("--host", default="0.0.0.0", help="Binding host IP (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    args = parser.parse_args()

    # Ensure log file directory exists
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if not TELEMETRY_LOG.is_file():
        TELEMETRY_LOG.touch()

    server = ThreadedHTTPServer((args.host, args.port), AISRequestHandler)
    print("=" * 64)
    print(f"[*] Tactical Railway AIS Dashboard Server")
    print(f"[*] Local UI:       http://localhost:{args.port}")
    print(f"[*] Network UI:     http://{args.host}:{args.port}")
    print(f"[*] Telemetry Log:  {TELEMETRY_LOG}")
    print(f"[*] GeoJSON Dir:    {GEOJSON_DIR}")
    print("=" * 64)
    print("[*] Press Ctrl+C to terminate.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down AIS Dashboard Server...")
        server.server_close()


if __name__ == "__main__":
    main()
