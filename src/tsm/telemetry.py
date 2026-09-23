"""
Railway AIS Telemetry & Device Condition Protocol.
Serializes GPS coordinates and device health metrics into compact binary (25 bytes)
and hex-encoded frames for tactical mesh transmission.
"""

import time
import math
import struct
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple

# Opcode marker for Railway AIS Telemetry (ASCII 'T' = 0x54)
AIS_TELEMETRY_OPCODE = 0x54
HEX_PREFIX = "HEX:"

# Status Bitmask Flags
FLAG_GPS_LOCKED = 0x0001
FLAG_ENGINE_ACTIVE = 0x0002
FLAG_EMERGENCY_BRAKE = 0x0004
FLAG_DOORS_LOCKED = 0x0008
FLAG_SDR_HEALTHY = 0x0010
FLAG_STATION_DOCKED = 0x0020

# Binary format layout:
# [Opcode 1B][Train_ID 2B][Timestamp 4B][Lat 4B][Lon 4B][Speed 2B][Heading 2B][Bat 2B][Temp 1B][CPU 1B][Flags 2B]
# Total = 25 bytes
TELEMETRY_STRUCT = "!B H I i i H H H b B H"
TELEMETRY_SIZE = struct.calcsize(TELEMETRY_STRUCT)


@dataclass
class DeviceCondition:
    battery_mv: int = 12600      # Millivolts (e.g. 12600 mV = 12.60 V)
    temperature_c: int = 42      # Celsius (-40 to +125 C)
    cpu_load_pct: int = 18       # CPU utilization (0-100%)
    flags: int = FLAG_GPS_LOCKED | FLAG_ENGINE_ACTIVE | FLAG_DOORS_LOCKED | FLAG_SDR_HEALTHY

    @property
    def battery_volts(self) -> float:
        return self.battery_mv / 1000.0

    @property
    def is_emergency(self) -> bool:
        return bool(self.flags & FLAG_EMERGENCY_BRAKE)

    def status_summary(self) -> str:
        parts = []
        if self.flags & FLAG_GPS_LOCKED:
            parts.append("GPS:3D-FIX")
        else:
            parts.append("GPS:NO-FIX")

        if self.flags & FLAG_EMERGENCY_BRAKE:
            parts.append("BRAKE:EMERGENCY")
        else:
            parts.append("BRAKE:NORMAL")

        if self.flags & FLAG_ENGINE_ACTIVE:
            parts.append("ENG:ON")
        else:
            parts.append("ENG:OFF")

        if self.flags & FLAG_SDR_HEALTHY:
            parts.append("SDR:OK")

        return " | ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "battery_mv": self.battery_mv,
            "battery_v": round(self.battery_volts, 2),
            "temperature_c": self.temperature_c,
            "cpu_load_pct": self.cpu_load_pct,
            "is_emergency": self.is_emergency,
            "status_summary": self.status_summary(),
            "flags_hex": f"0x{self.flags:04X}",
            "flags": {
                "gps_locked": bool(self.flags & FLAG_GPS_LOCKED),
                "engine_active": bool(self.flags & FLAG_ENGINE_ACTIVE),
                "emergency_brake": bool(self.flags & FLAG_EMERGENCY_BRAKE),
                "doors_locked": bool(self.flags & FLAG_DOORS_LOCKED),
                "sdr_healthy": bool(self.flags & FLAG_SDR_HEALTHY),
                "station_docked": bool(self.flags & FLAG_STATION_DOCKED),
            },
        }


@dataclass
class TrainAISTelemetry:
    train_id: int
    timestamp: int
    latitude: float
    longitude: float
    speed_kmh: float
    heading_deg: float
    condition: DeviceCondition

    def to_dict(self) -> Dict[str, Any]:
        return {
            "train_id": f"0x{self.train_id:04X}",
            "train_id_int": self.train_id,
            "timestamp": self.timestamp,
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.timestamp)),
            "gps": {
                "latitude": round(self.latitude, 6),
                "longitude": round(self.longitude, 6),
            },
            "motion": {
                "speed_kmh": round(self.speed_kmh, 1),
                "heading_deg": round(self.heading_deg, 1),
            },
            "device_health": self.condition.to_dict(),
        }

    def pack_binary(self) -> bytes:
        """Serializes telemetry into compact 25-byte binary struct."""
        lat_int = int(round(self.latitude * 1e6))
        lon_int = int(round(self.longitude * 1e6))
        speed_int = int(round(max(0.0, self.speed_kmh) * 10))
        heading_int = int(round((self.heading_deg % 360.0) * 10))

        return struct.pack(
            TELEMETRY_STRUCT,
            AIS_TELEMETRY_OPCODE,
            self.train_id & 0xFFFF,
            self.timestamp & 0xFFFFFFFF,
            lat_int,
            lon_int,
            speed_int & 0xFFFF,
            heading_int & 0xFFFF,
            self.condition.battery_mv & 0xFFFF,
            max(-128, min(127, self.condition.temperature_c)),
            max(0, min(100, self.condition.cpu_load_pct)),
            self.condition.flags & 0xFFFF,
        )

    def to_hex_payload(self) -> str:
        """Returns the serialized payload formatted with the HEX: prefix."""
        raw = self.pack_binary()
        return f"{HEX_PREFIX}{raw.hex().upper()}"

    @classmethod
    def unpack_binary(cls, raw: bytes) -> Optional["TrainAISTelemetry"]:
        """Parses a 25-byte binary telemetry slice."""
        if len(raw) < TELEMETRY_SIZE:
            return None

        try:
            (
                opcode,
                train_id,
                ts,
                lat_int,
                lon_int,
                speed_int,
                heading_int,
                bat_mv,
                temp_c,
                cpu_pct,
                flags,
            ) = struct.unpack(TELEMETRY_STRUCT, raw[:TELEMETRY_SIZE])
        except struct.error:
            return None

        if opcode != AIS_TELEMETRY_OPCODE:
            return None

        condition = DeviceCondition(
            battery_mv=bat_mv,
            temperature_c=temp_c,
            cpu_load_pct=cpu_pct,
            flags=flags,
        )

        return cls(
            train_id=train_id,
            timestamp=ts,
            latitude=lat_int / 1e6,
            longitude=lon_int / 1e6,
            speed_kmh=speed_int / 10.0,
            heading_deg=heading_int / 10.0,
            condition=condition,
        )

    @classmethod
    def from_payload_string(cls, payload_str: str) -> Optional["TrainAISTelemetry"]:
        """Decodes either a raw hex string or a string prefixed with HEX:."""
        text = payload_str.strip()
        if text.startswith(HEX_PREFIX):
            hex_data = text[len(HEX_PREFIX) :].strip()
        elif len(text) == TELEMETRY_SIZE * 2:
            hex_data = text
        else:
            return None

        try:
            raw_bytes = bytes.fromhex(hex_data)
            return cls.unpack_binary(raw_bytes)
        except (ValueError, TypeError):
            return None

    def format_display(self) -> str:
        """Formatted human-readable telemetry summary for UI and terminals."""
        time_str = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        status_color = "\033[91m" if self.condition.is_emergency else "\033[92m"

        return (
            f"Train 0x{self.train_id:04X} [{time_str}] | "
            f"GPS: ({self.latitude:.5f}, {self.longitude:.5f}) | "
            f"Spd: {self.speed_kmh:.1f} km/h | Hdg: {self.heading_deg:.1f}° | "
            f"Bat: {self.condition.battery_volts:.2f}V | Temp: {self.condition.temperature_c}°C | "
            f"CPU: {self.condition.cpu_load_pct}% | "
            f"Status: {status_color}{self.condition.status_summary()}\033[0m"
        )


class DummyGPSSimulator:
    """
    Simulates train movement along a realistic railway corridor with dynamic
    device health conditions (battery drain, temperature, emergency brake).
    Default track route: Gambir -> Manggarai Corridor, Jakarta.
    """

    def __init__(
        self,
        train_id: int = 0x0002,
        start_lat: float = -6.1767,
        start_lon: float = 106.8306,
        target_lat: float = -6.2100,
        target_lon: float = 106.8490,
        cruise_speed_kmh: float = 75.0,
    ):
        self.train_id = train_id
        self.lat = start_lat
        self.lon = start_lon
        self.target_lat = target_lat
        self.target_lon = target_lon
        self.cruise_speed = cruise_speed_kmh
        self.speed = 0.0
        self.heading = 150.0  # Initial bearing South-East
        self.battery_mv = 12750
        self.temp_c = 38
        self.cpu_pct = 15
        self.emergency_brake = False
        self.last_update = time.time()

    def set_emergency(self, active: bool = True):
        self.emergency_brake = active

    def set_emergency_brake(self, active: bool = True):
        self.emergency_brake = active

    def step(self, dt: Optional[float] = None) -> TrainAISTelemetry:
        """Advances simulation by dt seconds and returns fresh TrainAISTelemetry."""
        now = time.time()
        delta = (now - self.last_update) if dt is None else dt
        delta = max(0.1, min(10.0, delta))
        self.last_update = now

        # Speed acceleration / deceleration
        if self.emergency_brake:
            self.speed = max(0.0, self.speed - 35.0 * delta)  # Rapid emergency deceleration
        else:
            if self.speed < self.cruise_speed:
                self.speed = min(self.cruise_speed, self.speed + 8.0 * delta)

        # Bearing to destination
        d_lat = self.target_lat - self.lat
        d_lon = self.target_lon - self.lon
        dist_deg = math.hypot(d_lat, d_lon)

        if dist_deg < 0.0005:
            # Reached waypoint -> reverse target route
            self.target_lat, self.target_lon, self.lat, self.lon = (
                self.lat,
                self.lon,
                self.target_lat,
                self.target_lon,
            )
            d_lat = self.target_lat - self.lat
            d_lon = self.target_lon - self.lon

        # Calculate heading
        self.heading = (math.degrees(math.atan2(d_lon, d_lat)) + 360.0) % 360.0

        # Displace position based on speed
        speed_mps = (self.speed * 1000.0) / 3600.0
        dist_m = speed_mps * delta
        # 1 degree latitude ~ 111,320 meters
        self.lat += (dist_m * math.cos(math.radians(self.heading))) / 111320.0
        self.lon += (dist_m * math.sin(math.radians(self.heading))) / (
            111320.0 * math.cos(math.radians(self.lat))
        )

        # Device condition dynamics
        self.battery_mv = max(11200, self.battery_mv - int(1 * delta))
        # Temperature increases slightly under high speed
        target_temp = 38 + int(self.speed / 10.0)
        if self.temp_c < target_temp:
            self.temp_c += 1
        elif self.temp_c > target_temp:
            self.temp_c -= 1

        self.cpu_pct = int(12 + (self.speed / 5.0) % 15)

        flags = FLAG_GPS_LOCKED | FLAG_DOORS_LOCKED | FLAG_SDR_HEALTHY
        if self.speed > 1.0:
            flags |= FLAG_ENGINE_ACTIVE
        if self.emergency_brake:
            flags |= FLAG_EMERGENCY_BRAKE

        cond = DeviceCondition(
            battery_mv=self.battery_mv,
            temperature_c=self.temp_c,
            cpu_load_pct=self.cpu_pct,
            flags=flags,
        )

        return TrainAISTelemetry(
            train_id=self.train_id,
            timestamp=int(now),
            latitude=self.lat,
            longitude=self.lon,
            speed_kmh=self.speed,
            heading_deg=self.heading,
            condition=cond,
        )
