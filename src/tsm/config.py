"""
Centralized Configuration Loader for Mesh-Tactic SDR & Railway AIS.
Loads settings from .env file with fallback to auto-detection and CLI overrides.
Pure Python, zero external dependencies.
"""

import os
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def load_dotenv(env_path: Optional[Path] = None):
    """Parses .env file and sets values into os.environ if not already defined."""
    target = env_path or (REPO_ROOT / ".env")
    if not target.is_file():
        return

    try:
        with open(target, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    # Strip wrapping quotes
                    if len(val) >= 2 and (
                        (val.startswith('"') and val.endswith('"'))
                        or (val.startswith("'") and val.endswith("'"))
                    ):
                        val = val[1:-1]
                    # Inline comments removal (only if preceded by whitespace)
                    if " #" in val:
                        val = val.split(" #", 1)[0].strip()

                    if key not in os.environ:
                        os.environ[key] = val
    except Exception as e:
        print(f"[WARN] Failed to read .env file: {e}", file=sys.stderr)


# Auto-load .env immediately on import
load_dotenv()


def _get_str(key: str, default: str) -> str:
    return os.environ.get(key, default).strip()


def _get_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        val = val.strip()
        return int(val, 16) if val.lower().startswith("0x") else int(val)
    except ValueError:
        return default


def _get_float(key: str, default: float) -> float:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val.strip())
    except ValueError:
        return default


def _get_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on", "t")


def auto_detect_node_id() -> int:
    nodename = os.uname().nodename.lower()
    sysname = os.uname().sysname.lower()
    if "pi5" in nodename:
        return 0x0002
    elif "aml" in nodename or "2w" in nodename:
        return 0x0003
    elif "darwin" in sysname or "mac" in nodename:
        return 0x0001
    return 0x0001


def auto_detect_uri() -> str:
    nodename = os.uname().nodename.lower()
    sysname = os.uname().sysname.lower()
    if "2w" in nodename or "zero" in nodename or "aml" in nodename:
        return "ip:192.168.99.240"
    elif "darwin" in sysname:
        return "ip:192.168.2.10"
    return "usb:1.3.5"


@dataclass
class AppConfig:
    # Node Identity
    node_id: int
    node_alias: str
    callsign: str
    target_id: int

    # Radio Physical Parameters
    carrier_freq: int
    sample_rate: int
    rx_gain: float
    tx_atten: float
    sdr_uri: str

    # Network & Relay
    enable_relay: bool
    default_ttl: int

    # MAC & Anti-Collision
    mac_mode: str
    mac_slots_per_frame: int
    mac_frame_duration: float
    mac_min_backoff_ms: float
    mac_max_backoff_ms: float

    # Socket Transport (Optional)
    use_socket: bool
    socket_host: str
    socket_tx_port: int
    socket_rx_port: int

    # Railway AIS & Gateway Station
    is_gateway: bool
    gateway_station_name: str
    gateway_lat: float
    gateway_lon: float
    gateway_log_file: str
    gateway_log_enabled: bool
    gateway_mqtt_enabled: bool
    gateway_mqtt_broker: str
    gateway_mqtt_port: int
    gateway_mqtt_topic: str

    # AIS GPS Simulation Mode
    sim_enabled: bool
    sim_interval: float
    sim_route: str

    @classmethod
    def load(cls) -> "AppConfig":
        raw_uri = _get_str("SDR_URI", "auto")
        uri = auto_detect_uri() if raw_uri.lower() in ("auto", "", "none") else raw_uri

        node_id_cfg = _get_int("NODE_ID", -1)
        node_id = auto_detect_node_id() if node_id_cfg < 0 else node_id_cfg

        alias = _get_str("NODE_ALIAS", "")
        if not alias:
            alias = "mac" if node_id == 1 else ("pi5" if node_id == 2 else "aml")

        target_id_cfg = _get_int("TARGET_ID", 0xFFFF)

        return cls(
            node_id=node_id,
            node_alias=alias,
            callsign=_get_str("CALLSIGN", f"NODE-{node_id:04X}"),
            target_id=target_id_cfg,
            carrier_freq=_get_int("CARRIER_FREQ", 915_000_000),
            sample_rate=_get_int("SAMPLE_RATE", 2_500_000),
            rx_gain=_get_float("RX_GAIN", 65.0),
            tx_atten=_get_float("TX_ATTEN", 0.0),
            sdr_uri=uri,
            enable_relay=_get_bool("ENABLE_RELAY", True),
            default_ttl=_get_int("DEFAULT_TTL", 3),
            mac_mode=_get_str("MAC_MODE", "lbt").lower(),
            mac_slots_per_frame=_get_int("MAC_SLOTS_PER_FRAME", 20),
            mac_frame_duration=_get_float("MAC_FRAME_DURATION", 1.0),
            mac_min_backoff_ms=_get_float("MAC_MIN_BACKOFF_MS", 25.0),
            mac_max_backoff_ms=_get_float("MAC_MAX_BACKOFF_MS", 90.0),
            use_socket=_get_bool("USE_SOCKET", False),
            socket_host=_get_str("SOCKET_HOST", "127.0.0.1"),
            socket_tx_port=_get_int("SOCKET_TX_PORT", 52001),
            socket_rx_port=_get_int("SOCKET_RX_PORT", 52002),
            is_gateway=_get_bool("IS_GATEWAY", False),
            gateway_station_name=_get_str("GATEWAY_STATION_NAME", "Titik Tengah Utama (Stasiun Rendeh)"),
            gateway_lat=_get_float("GATEWAY_LAT", -6.58025),
            gateway_lon=_get_float("GATEWAY_LON", 107.24695),
            gateway_log_file=_get_str("GATEWAY_LOG_FILE", "logs/gateway_telemetry.jsonl"),
            gateway_log_enabled=_get_bool("GATEWAY_LOG_ENABLED", True),
            gateway_mqtt_enabled=_get_bool("GATEWAY_MQTT_ENABLED", False),
            gateway_mqtt_broker=_get_str("GATEWAY_MQTT_BROKER", _get_str("MQTT_BROKER", "127.0.0.1")),
            gateway_mqtt_port=_get_int("GATEWAY_MQTT_PORT", _get_int("MQTT_PORT", 1883)),
            gateway_mqtt_topic=_get_str("GATEWAY_MQTT_TOPIC", "railway/telemetry"),
            sim_enabled=_get_bool("SIM_ENABLED", False),
            sim_interval=_get_float("SIM_INTERVAL", 5.0),
            sim_route=_get_str("SIM_ROUTE", "auto"),
        )


# Global singleton instance
CONFIG = AppConfig.load()
