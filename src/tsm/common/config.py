"""
Central Configuration Loader for Tactical SDR Mesh.
Provides typed data structures with fallback parsing if PyYAML is unavailable.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Any, Optional


@dataclass
class SDRConfig:
    uri: str = "usb:1.3.5"
    center_freq: int = 915_000_000
    sample_rate: int = 2_500_000
    bandwidth: int = 1_000_000
    tx_gain: float = 0.0
    rx_gain: float = 62.0
    rx_gain_mode: str = "manual"
    buffer_size: int = 131072


@dataclass
class LoraConfig:
    spreading_factor: int = 7
    bandwidth: int = 125_000
    code_rate: int = 1
    sync_word: int = 0x12
    has_crc: bool = True
    impl_header: bool = False
    ldro: int = 0


@dataclass
class NetworkConfig:
    tap_device: str = "tap-radio"
    bat_device: str = "bat0"
    node_ip: str = "10.10.0.1/24"
    mtu: int = 180
    ipc_tx_port: int = 52001
    ipc_rx_port: int = 52002


@dataclass
class Config:
    sdr: SDRConfig = field(default_factory=SDRConfig)
    lora: LoraConfig = field(default_factory=LoraConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    raw_dict: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sdr": self.sdr.__dict__,
            "lora": self.lora.__dict__,
            "network": self.network.__dict__,
        }


def _parse_yaml_fallback(content: str) -> Dict[str, Any]:
    """Lightweight stdlib-only YAML subset parser for zero-dependency operation."""
    cfg: Dict[str, Any] = {}
    curr_section = None
    for line in content.splitlines():
        line = line.split("#")[0].rstrip()
        if not line:
            continue
        sec_match = re.match(r"^([a-zA-Z0-9_-]+):\s*$", line)
        if sec_match:
            curr_section = sec_match.group(1)
            cfg[curr_section] = {}
            continue
        val_match = re.match(r"^\s+([a-zA-Z0-9_-]+):\s*(.+)$", line)
        if val_match and curr_section:
            k = val_match.group(1)
            v_str = val_match.group(2).strip()
            if v_str.startswith('"') and v_str.endswith('"'):
                v: Any = v_str[1:-1]
            elif v_str.lower() in ("true", "yes"):
                v = True
            elif v_str.lower() in ("false", "no"):
                v = False
            else:
                try:
                    v = int(v_str) if "." not in v_str else float(v_str)
                except ValueError:
                    v = v_str
            cfg[curr_section][k] = v
    return cfg


def load_config(path: str = "config/config.yaml") -> Config:
    """Loads configuration file into typed Config object with fallbacks."""
    # Check alternate paths if default not found
    search_paths = [path, "config/config.yaml", "config.yaml"]
    chosen_path = None
    for p in search_paths:
        if os.path.isfile(p):
            chosen_path = p
            break

    raw: Dict[str, Any] = {}
    if chosen_path:
        try:
            import yaml
            with open(chosen_path, "r") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    raw = loaded
        except ImportError:
            with open(chosen_path, "r") as f:
                raw = _parse_yaml_fallback(f.read())
        except Exception:
            raw = {}

    sdr_dict = raw.get("sdr", {})
    lora_dict = raw.get("lora", {})
    net_dict = raw.get("network", {})

    def _to_int(val, default):
        if val is None:
            return default
        if isinstance(val, int):
            return val
        if isinstance(val, str):
            try:
                return int(val, 0)
            except ValueError:
                return default
        return int(val)

    sdr_cfg = SDRConfig(
        uri=str(sdr_dict.get("uri", SDRConfig.uri)),
        center_freq=_to_int(sdr_dict.get("center_freq"), SDRConfig.center_freq),
        sample_rate=_to_int(sdr_dict.get("sample_rate"), SDRConfig.sample_rate),
        bandwidth=_to_int(sdr_dict.get("bandwidth"), SDRConfig.bandwidth),
        tx_gain=float(sdr_dict.get("tx_gain", SDRConfig.tx_gain)),
        rx_gain=float(sdr_dict.get("rx_gain", SDRConfig.rx_gain)),
        rx_gain_mode=str(sdr_dict.get("rx_gain_mode", SDRConfig.rx_gain_mode)),
        buffer_size=_to_int(sdr_dict.get("buffer_size"), SDRConfig.buffer_size),
    )

    lora_cfg = LoraConfig(
        spreading_factor=_to_int(lora_dict.get("spreading_factor"), LoraConfig.spreading_factor),
        bandwidth=_to_int(lora_dict.get("bandwidth"), LoraConfig.bandwidth),
        code_rate=_to_int(lora_dict.get("code_rate"), LoraConfig.code_rate),
        sync_word=_to_int(lora_dict.get("sync_word"), LoraConfig.sync_word),
        has_crc=bool(lora_dict.get("has_crc", LoraConfig.has_crc)),
        impl_header=bool(lora_dict.get("impl_header", LoraConfig.impl_header)),
        ldro=_to_int(lora_dict.get("ldro"), LoraConfig.ldro),
    )

    net_cfg = NetworkConfig(
        tap_device=str(net_dict.get("tap_device", NetworkConfig.tap_device)),
        bat_device=str(net_dict.get("bat_device", NetworkConfig.bat_device)),
        node_ip=str(net_dict.get("node_ip", NetworkConfig.node_ip)),
        mtu=_to_int(net_dict.get("mtu"), NetworkConfig.mtu),
        ipc_tx_port=_to_int(net_dict.get("ipc_tx_port"), NetworkConfig.ipc_tx_port),
        ipc_rx_port=_to_int(net_dict.get("ipc_rx_port"), NetworkConfig.ipc_rx_port),
    )

    return Config(sdr=sdr_cfg, lora=lora_cfg, network=net_cfg, raw_dict=raw)
