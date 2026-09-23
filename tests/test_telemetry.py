"""
Unit tests for Railway AIS Telemetry and Dummy GPS Simulator.
"""

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tsm.telemetry import (
    TrainAISTelemetry,
    DeviceCondition,
    DummyGPSSimulator,
    FLAG_GPS_LOCKED,
    FLAG_ENGINE_ACTIVE,
    FLAG_EMERGENCY_BRAKE,
    TELEMETRY_SIZE,
)


class TestAISTelemetry(unittest.TestCase):
    def test_binary_pack_unpack_roundtrip(self):
        condition = DeviceCondition(
            battery_mv=12540,
            temperature_c=43,
            cpu_load_pct=22,
            flags=FLAG_GPS_LOCKED | FLAG_ENGINE_ACTIVE,
        )
        telemetry = TrainAISTelemetry(
            train_id=0x0002,
            timestamp=1758602800,
            latitude=-6.176712,
            longitude=106.830645,
            speed_kmh=82.4,
            heading_deg=145.2,
            condition=condition,
        )

        binary = telemetry.pack_binary()
        self.assertEqual(len(binary), TELEMETRY_SIZE)
        self.assertEqual(len(binary), 25)

        restored = TrainAISTelemetry.unpack_binary(binary)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.train_id, 0x0002)
        self.assertEqual(restored.timestamp, 1758602800)
        self.assertAlmostEqual(restored.latitude, -6.176712, places=5)
        self.assertAlmostEqual(restored.longitude, 106.830645, places=5)
        self.assertAlmostEqual(restored.speed_kmh, 82.4, places=1)
        self.assertAlmostEqual(restored.heading_deg, 145.2, places=1)
        self.assertEqual(restored.condition.battery_mv, 12540)
        self.assertEqual(restored.condition.temperature_c, 43)
        self.assertEqual(restored.condition.cpu_load_pct, 22)
        self.assertTrue(restored.condition.flags & FLAG_GPS_LOCKED)
        self.assertFalse(restored.condition.is_emergency)

    def test_hex_payload_encoding_and_parsing(self):
        condition = DeviceCondition(
            battery_mv=11900,
            temperature_c=50,
            cpu_load_pct=30,
            flags=FLAG_EMERGENCY_BRAKE,
        )
        telemetry = TrainAISTelemetry(
            train_id=0x0003,
            timestamp=1758602900,
            latitude=-6.210050,
            longitude=106.849020,
            speed_kmh=0.0,
            heading_deg=0.0,
            condition=condition,
        )

        hex_payload = telemetry.to_hex_payload()
        self.assertTrue(hex_payload.startswith("HEX:"))
        # 25 bytes binary = 50 hex chars + 4 prefix chars = 54 chars total
        self.assertEqual(len(hex_payload), 4 + 50)

        restored = TrainAISTelemetry.from_payload_string(hex_payload)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.train_id, 0x0003)
        self.assertTrue(restored.condition.is_emergency)
        self.assertAlmostEqual(restored.latitude, -6.210050, places=5)

    def test_dummy_gps_simulator_steps_and_emergency(self):
        sim = DummyGPSSimulator(train_id=0x0002, cruise_speed_kmh=60.0)

        # Step 1: Initial state accelerates
        t1 = sim.step(dt=1.0)
        self.assertEqual(t1.train_id, 0x0002)
        self.assertGreater(t1.speed_kmh, 0.0)
        self.assertFalse(t1.condition.is_emergency)

        # Trigger emergency brake
        sim.set_emergency(True)
        t2 = sim.step(dt=1.0)
        self.assertTrue(t2.condition.is_emergency)
        self.assertTrue(t2.condition.flags & FLAG_EMERGENCY_BRAKE)

        # Formatting should contain emergency marker
        formatted = t2.format_display()
        self.assertIn("BRAKE:EMERGENCY", formatted)
        self.assertIn("Train 0x0002", formatted)


if __name__ == "__main__":
    unittest.main()
