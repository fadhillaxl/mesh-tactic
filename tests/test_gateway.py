"""
Unit tests for RailwayAISGateway and JSONL telemetry logging (tsm.gateway).
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

# Ensure src/ is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tsm.gateway import RailwayAISGateway
from tsm.telemetry import DummyGPSSimulator, TrainAISTelemetry
from unicast_mesh_chat import MeshPacket, BROADCAST_ID


class TestRailwayAISGateway(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_file = Path(self.temp_dir.name) / "test_gateway.jsonl"
        self.gateway = RailwayAISGateway(
            node_id=0x0001,
            station_name="Test Stasiun Gambir",
            log_enabled=True,
            log_file=str(self.log_file),
            mqtt_enabled=False,
        )

    def tearDown(self):
        self.gateway.close()
        self.temp_dir.cleanup()

    def test_gateway_ingest_and_jsonl_output(self):
        sim = DummyGPSSimulator(train_id=0x0002)
        telem = sim.step(5.0)
        hex_payload = telem.to_hex_payload()

        packet = MeshPacket(
            src_id=0x0002,
            dst_id=BROADCAST_ID,
            msg_id=0x1234,
            payload=hex_payload,
            flags=0x02,
            ttl=3,
        )

        ok = self.gateway.ingest(packet, telem, corr=0.96)
        self.assertTrue(ok)

        # Flush queue to disk
        self.gateway.flush()

        self.assertTrue(self.log_file.is_file())
        with open(self.log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])

        # Validate top-level schema
        self.assertEqual(record["event"], "railway_ais_telemetry")
        self.assertEqual(record["gateway"]["station_name"], "Test Stasiun Gambir")
        self.assertEqual(record["gateway"]["node_id"], "0x0001")
        self.assertEqual(record["gateway"]["rx_correlation"], 0.96)

        # Validate packet metadata
        self.assertEqual(record["packet"]["src_id"], "0x0002")
        self.assertEqual(record["packet"]["dst_id"], "0xFFFF")
        self.assertEqual(record["packet"]["msg_id"], "0x1234")

        # Validate telemetry payload
        telemetry_dict = record["telemetry"]
        self.assertEqual(telemetry_dict["train_id"], "0x0002")
        self.assertAlmostEqual(telemetry_dict["gps"]["latitude"], telem.latitude, places=4)
        self.assertAlmostEqual(telemetry_dict["gps"]["longitude"], telem.longitude, places=4)
        self.assertEqual(telemetry_dict["device_health"]["battery_mv"], telem.condition.battery_mv)
        self.assertEqual(telemetry_dict["device_health"]["flags"]["gps_locked"], True)

        # Validate raw hex preservation
        self.assertTrue(record["raw_hex"].startswith("HEX:"))

    def test_tracked_trains_cache_and_summary(self):
        sim2 = DummyGPSSimulator(train_id=0x0002)
        sim3 = DummyGPSSimulator(train_id=0x0003)

        t2 = sim2.step(2.0)
        t3 = sim3.step(2.0)

        p2 = MeshPacket(src_id=0x0002, dst_id=0x0001, msg_id=1, payload=t2.to_hex_payload())
        p3 = MeshPacket(src_id=0x0003, dst_id=0x0001, msg_id=2, payload=t3.to_hex_payload())

        self.gateway.ingest(p2, t2, corr=0.91)
        self.gateway.ingest(p3, t3, corr=0.88)
        self.gateway.flush()

        tracked = self.gateway.get_tracked_trains()
        self.assertEqual(len(tracked), 2)
        self.assertIn(0x0002, tracked)
        self.assertIn(0x0003, tracked)
        self.assertEqual(tracked[0x0002]["total_packets"], 1)

        summary = self.gateway.get_status_summary()
        self.assertIn("Test Stasiun Gambir", summary)
        self.assertIn("Tracked: 2 Trains", summary)
        self.assertIn("Ingested: 2 pkts", summary)

    def test_recent_logs_retrieval(self):
        sim = DummyGPSSimulator(train_id=0x0002)
        for i in range(5):
            t = sim.step(1.0)
            p = MeshPacket(src_id=0x0002, dst_id=BROADCAST_ID, msg_id=i, payload=t.to_hex_payload())
            self.gateway.ingest(p, t)

        self.gateway.flush()
        recent = self.gateway.get_recent_logs(max_lines=3)
        self.assertEqual(len(recent), 3)


if __name__ == "__main__":
    unittest.main()
