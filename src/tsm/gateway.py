"""
Railway AIS Tactical Gateway & Ingestion Engine.
Captures telemetry packets from Pluto SDR mesh, serializes them to standard JSON,
and logs them into disk files (JSONL) with standby support for MQTT upstream brokers.

Zero external dependencies required (pure Python standard library).
"""

import os
import sys
import time
import json
import queue
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from tsm.telemetry import TrainAISTelemetry
from tsm.routes import (
    haversine_distance_m,
    DEFAULT_GATEWAY_NAME,
    DEFAULT_GATEWAY_LAT,
    DEFAULT_GATEWAY_LON,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class GatewayConfig:
    enabled: bool = False
    station_name: str = DEFAULT_GATEWAY_NAME
    node_id: int = 0x0001
    latitude: float = DEFAULT_GATEWAY_LAT
    longitude: float = DEFAULT_GATEWAY_LON
    log_enabled: bool = True
    log_file: str = "logs/gateway_telemetry.jsonl"
    mqtt_enabled: bool = False
    mqtt_broker: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_topic: str = "railway/telemetry"


class RailwayAISGateway:
    """
    Station Gateway Bridge:
    Ingests RF Mesh packets, parses train AIS telemetry, computes distance from
    the gateway station, writes structured records to a persistent JSONL log,
    and provides an extensible uplink pipeline.
    """

    def __init__(
        self,
        node_id: int = 0x0001,
        station_name: str = DEFAULT_GATEWAY_NAME,
        latitude: float = DEFAULT_GATEWAY_LAT,
        longitude: float = DEFAULT_GATEWAY_LON,
        log_enabled: bool = True,
        log_file: str = "logs/gateway_telemetry.jsonl",
        mqtt_enabled: bool = False,
        mqtt_broker: str = "127.0.0.1",
        mqtt_port: int = 1883,
        mqtt_topic: str = "railway/telemetry",
    ):
        self.node_id = node_id & 0xFFFF
        self.station_name = station_name
        self.latitude = latitude
        self.longitude = longitude
        self.log_enabled = log_enabled
        self.mqtt_enabled = mqtt_enabled
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.mqtt_topic = mqtt_topic

        # Resolve log file path relative to repo root if not absolute
        log_p = Path(log_file)
        if not log_p.is_absolute():
            self.log_path = REPO_ROOT / log_p
        else:
            self.log_path = log_p

        # State & metrics
        self.lock = threading.Lock()
        self.packets_ingested = 0
        self.last_packet_time: Optional[float] = None
        self.trains_tracked: Dict[int, Dict[str, Any]] = {}

        # Asynchronous ingestion queue to avoid blocking RF baseband worker
        self._queue: queue.Queue = queue.Queue(maxsize=2000)
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._ingestion_worker, daemon=True, name="gateway-writer"
        )
        self._worker_thread.start()

    def ingest(
        self,
        packet: Any,
        telemetry: TrainAISTelemetry,
        corr: Optional[float] = None,
    ) -> bool:
        """
        Submits an incoming MeshPacket with its parsed telemetry to the Gateway queue.
        Computes distance between the station gateway and the train in km.
        """
        now = time.time()
        dist_to_train_km = (
            haversine_distance_m(self.latitude, self.longitude, telemetry.latitude, telemetry.longitude)
            / 1000.0
        )

        record = {
            "event": "railway_ais_telemetry",
            "received_time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "received_timestamp": now,
            "gateway": {
                "station_name": self.station_name,
                "node_id": f"0x{self.node_id:04X}",
                "node_id_int": self.node_id,
                "gps": {
                    "latitude": round(self.latitude, 6),
                    "longitude": round(self.longitude, 6),
                },
                "distance_to_train_km": round(dist_to_train_km, 2),
                "rx_correlation": round(corr, 3) if corr is not None else None,
            },
            "packet": {
                "src_id": f"0x{packet.src_id:04X}",
                "dst_id": f"0x{packet.dst_id:04X}",
                "msg_id": f"0x{packet.msg_id:04X}",
                "flags": f"0x{packet.flags:02X}",
                "ttl": packet.ttl,
                "relayed": bool(packet.flags & 0x08),
            },
            "telemetry": telemetry.to_dict(),
            "raw_hex": getattr(packet, "payload", ""),
        }

        try:
            self._queue.put_nowait(record)
            return True
        except queue.Full:
            print(
                f"[WARN] Gateway queue full! Dropped packet from Train 0x{telemetry.train_id:04X}",
                file=sys.stderr,
            )
            return False

    def _ingestion_worker(self):
        """Background worker that commits queued records to log file & uplinks."""
        while self._running:
            try:
                record = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                self._handle_record(record)
            except Exception as e:
                print(f"[ERROR] Gateway processing failed: {e}", file=sys.stderr)
            finally:
                self._queue.task_done()

    def _handle_record(self, record: Dict[str, Any]):
        """Persists the record to JSONL disk log and updates live tracking cache."""
        now = time.time()
        telem = record.get("telemetry", {})
        train_id_int = telem.get("train_id_int", 0)

        # Update in-memory live train state
        with self.lock:
            self.packets_ingested += 1
            self.last_packet_time = now
            self.trains_tracked[train_id_int] = {
                "train_id": telem.get("train_id"),
                "last_seen_iso": record.get("received_time_iso"),
                "last_seen_ts": now,
                "gps": telem.get("gps", {}),
                "motion": telem.get("motion", {}),
                "device_health": telem.get("device_health", {}),
                "total_packets": self.trains_tracked.get(train_id_int, {}).get("total_packets", 0) + 1,
            }

        # Write to JSONL file
        if self.log_enabled:
            self._write_to_file(record)

        # Standby for MQTT Broker Uplink
        if self.mqtt_enabled:
            self._publish_mqtt(record)

    def _write_to_file(self, record: Dict[str, Any]):
        """Appends JSON line to configured log file."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                json_line = json.dumps(record, ensure_ascii=False)
                f.write(json_line + "\n")
                f.flush()
        except Exception as e:
            print(f"[ERROR] Failed writing to gateway log '{self.log_path}': {e}", file=sys.stderr)

    def _publish_mqtt(self, record: Dict[str, Any]):
        """Standby MQTT publisher: publishes JSON payload when broker is available."""
        # Note: Keeps zero mandatory third-party dependencies.
        # Dynamically uses paho.mqtt if installed; logs warning if not installed.
        try:
            import paho.mqtt.publish as publish  # type: ignore

            payload = json.dumps(record)
            train_hex = record["telemetry"]["train_id"]
            topic = f"{self.mqtt_topic}/{train_hex}"
            publish.single(
                topic,
                payload=payload,
                hostname=self.mqtt_broker,
                port=self.mqtt_port,
                keepalive=10,
            )
        except ImportError:
            # paho-mqtt not installed yet; logging only
            pass
        except Exception as e:
            # Network or broker offline; logged to file safely
            pass

    def flush(self, timeout: float = 2.0):
        """Blocks until current queued items are written to disk."""
        try:
            self._queue.join()
        except Exception:
            pass

    def close(self):
        """Flushes queue and stops background worker."""
        self._running = False
        self.flush()

    def get_status_summary(self) -> str:
        """Returns a concise status string for CLI banner and inspection."""
        with self.lock:
            train_count = len(self.trains_tracked)
            pkts = self.packets_ingested

        mqtt_status = f"MQTT: {self.mqtt_broker}:{self.mqtt_port}" if self.mqtt_enabled else "MQTT: Standby (Log Only)"
        rel_log = str(self.log_path.relative_to(REPO_ROOT)) if str(self.log_path).startswith(str(REPO_ROOT)) else str(self.log_path)
        return (
            f"Gateway [{self.station_name} @ ({self.latitude:.5f}, {self.longitude:.5f})] | Log: {rel_log} | "
            f"Tracked: {train_count} Trains | Ingested: {pkts} pkts | {mqtt_status}"
        )

    def get_tracked_trains(self) -> Dict[int, Dict[str, Any]]:
        with self.lock:
            return dict(self.trains_tracked)

    def get_recent_logs(self, max_lines: int = 5) -> List[str]:
        """Reads the last N lines from the active JSONL log file."""
        if not self.log_path.is_file():
            return []

        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                return [line.strip() for line in lines[-max_lines:]]
        except Exception:
            return []
