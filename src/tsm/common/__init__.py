"""Common utilities, configurations, and tactical framing protocols."""
from .framing import TacticalFraming, crc16_ccitt
from .config import load_config, Config

__all__ = ["TacticalFraming", "crc16_ccitt", "load_config", "Config"]
