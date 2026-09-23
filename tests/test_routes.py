"""
Unit tests for Railway AIS GeoJSON Route & Station Navigation.
Validates geospatial calculations, GeoJSON parsing, waypoint navigation,
and gateway distance computation.
"""

import sys
import unittest
from pathlib import Path

# Ensure src/ is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tsm.routes import (
    haversine_distance_m,
    calculate_bearing_deg,
    load_geojson_station,
    load_geojson_route,
    get_route_waypoints,
    DEFAULT_GATEWAY_NAME,
    DEFAULT_GATEWAY_LAT,
    DEFAULT_GATEWAY_LON,
)
from tsm.telemetry import (
    DummyGPSSimulator,
    TrainAISTelemetry,
    DeviceCondition,
)
from tsm.gateway import RailwayAISGateway


class DummyPacket:
    def __init__(self, src_id=0x0002, dst_id=0xFFFF, msg_id=0x0042, flags=0x02, ttl=3, payload=""):
        self.src_id = src_id
        self.dst_id = dst_id
        self.msg_id = msg_id
        self.flags = flags
        self.ttl = ttl
        self.payload = payload


class TestRailwayRoutes(unittest.TestCase):

    def test_haversine_distance(self):
        # Distance to self must be 0
        d_zero = haversine_distance_m(-6.58025, 107.24695, -6.58025, 107.24695)
        self.assertAlmostEqual(d_zero, 0.0, places=1)

        # Gambir (-6.1767, 106.8307) to Bandung (-6.9141, 107.6119) ~119 km great circle
        d_jkt_bdg = haversine_distance_m(-6.1767, 106.8307, -6.9141, 107.6119)
        self.assertTrue(110_000 < d_jkt_bdg < 130_000, f"Distance {d_jkt_bdg} out of expected bounds")

        # Stasiun Tengah (-6.58025, 107.24695) to Bandung (-6.9141, 107.6119) ~54-56 km
        d_tengah_bdg = haversine_distance_m(-6.58025, 107.24695, -6.9141, 107.6119)
        self.assertTrue(50_000 < d_tengah_bdg < 60_000, f"Distance {d_tengah_bdg} out of expected bounds")

    def test_bearing_calculation(self):
        # Due North
        b_north = calculate_bearing_deg(0.0, 0.0, 1.0, 0.0)
        self.assertAlmostEqual(b_north, 0.0, delta=1.0)

        # Due East
        b_east = calculate_bearing_deg(0.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(b_east, 90.0, delta=1.0)

        # Due South
        b_south = calculate_bearing_deg(1.0, 0.0, 0.0, 0.0)
        self.assertAlmostEqual(b_south, 180.0, delta=1.0)

        # Due West
        b_west = calculate_bearing_deg(0.0, 1.0, 0.0, 0.0)
        self.assertAlmostEqual(b_west, 270.0, delta=1.0)

    def test_load_geojson_station(self):
        station_info = load_geojson_station()
        self.assertIn("Titik Tengah", station_info["name"])
        self.assertAlmostEqual(station_info["lat"], -6.58025, places=5)
        self.assertAlmostEqual(station_info["lon"], 107.24695, places=5)

        # Fallback when non-existent path given
        fallback = load_geojson_station(Path("/non/existent/path.json"))
        self.assertEqual(fallback["name"], DEFAULT_GATEWAY_NAME)
        self.assertEqual(fallback["lat"], DEFAULT_GATEWAY_LAT)
        self.assertEqual(fallback["lon"], DEFAULT_GATEWAY_LON)

    def test_load_geojson_routes(self):
        jkt_bdg_file = REPO_ROOT / "geojson" / "jalur_kereta_jakarta_bandung.json"
        self.assertTrue(jkt_bdg_file.is_file())

        # Feature 0: Whoosh (4 waypoints: Halim, Karawang, Padalarang, Tegalluar)
        pts_whoosh = load_geojson_route(jkt_bdg_file, 0)
        self.assertEqual(len(pts_whoosh), 4)
        # Verify first waypoint is Halim (lat ~ -6.2464, lon ~ 106.8837)
        self.assertAlmostEqual(pts_whoosh[0][0], -6.2464, places=3)
        self.assertAlmostEqual(pts_whoosh[0][1], 106.8837, places=3)

        # Feature 1: Conventional (5 waypoints: Gambir, Manggarai, Bekasi, Purwakarta, Bandung)
        pts_conv = load_geojson_route(jkt_bdg_file, 1)
        self.assertEqual(len(pts_conv), 5)
        self.assertAlmostEqual(pts_conv[0][0], -6.1767, places=3)  # Gambir lat
        self.assertAlmostEqual(pts_conv[-1][0], -6.9141, places=3) # Bandung lat

        # Segmen 2: Titik Tengah ke Bandung (3 waypoints)
        seg2_file = REPO_ROOT / "geojson" / "tengah_bandung_ke_bandung.json"
        pts_seg2 = load_geojson_route(seg2_file, 0)
        self.assertEqual(len(pts_seg2), 3)
        self.assertAlmostEqual(pts_seg2[0][0], -6.58025, places=4) # Titik Tengah lat
        self.assertAlmostEqual(pts_seg2[-1][0], -6.9145, places=3)  # Bandung lat

    def test_get_route_waypoints_auto_mapping(self):
        # Node 0x0002 auto -> Conventional Jakarta-Bandung
        title2, wps2 = get_route_waypoints("auto", node_id=0x0002)
        self.assertIn("Konvensional", title2)
        self.assertEqual(len(wps2), 5)

        # Node 0x0003 auto -> Segmen 2 Titik Tengah - Bandung
        title3, wps3 = get_route_waypoints("auto", node_id=0x0003)
        self.assertIn("Segmen 2", title3)
        self.assertEqual(len(wps3), 3)

        # Named route: whoosh
        title_w, wps_w = get_route_waypoints("whoosh")
        self.assertIn("Whoosh", title_w)
        self.assertEqual(len(wps_w), 4)

    def test_dummy_gps_simulator_route_stepping(self):
        # Initialize simulator on Conventional Route
        sim = DummyGPSSimulator(train_id=0x0002, route_name="jakarta-bandung", sim_speedup=10.0)
        self.assertEqual(len(sim.waypoints), 5)
        self.assertAlmostEqual(sim.lat, -6.1767, places=3) # Starts at Gambir

        # Step forward simulation
        for _ in range(5):
            telem = sim.step(dt=1.0)
            self.assertEqual(telem.train_id, 0x0002)
            self.assertTrue(telem.speed_kmh > 0)
            self.assertIn(telem.condition.status_summary(), ["GPS:3D-FIX | BRAKE:NORMAL | ENG:ON | SDR:OK"])

        # Heading should be towards Manggarai (~165-170 degrees)
        self.assertTrue(150.0 < sim.heading < 190.0, f"Bearing {sim.heading} unexpected")

        # Telemetry format display includes Distance to Gateway
        formatted = telem.format_display(gw_lat=-6.58025, gw_lon=107.24695)
        self.assertIn("Dist to Gateway:", formatted)
        self.assertIn("km", formatted)

    def test_gateway_ingest_records_distance_to_train(self):
        log_file = REPO_ROOT / "logs" / "test_route_gateway.jsonl"
        if log_file.is_file():
            log_file.unlink()

        gw = RailwayAISGateway(
            node_id=0x0001,
            station_name="Titik Tengah Utama (Stasiun Rendeh)",
            latitude=-6.58025,
            longitude=107.24695,
            log_file=str(log_file),
        )

        telem = TrainAISTelemetry(
            train_id=0x0002,
            timestamp=1700000000,
            latitude=-6.9141,   # Stasiun Bandung
            longitude=107.6119,
            speed_kmh=80.0,
            heading_deg=285.0,
            condition=DeviceCondition(),
        )

        pkt = DummyPacket(src_id=0x0002, payload=telem.to_hex_payload())
        gw.ingest(pkt, telem, corr=1.0)
        gw.flush()
        gw.close()

        # Check logged distance in jsonl
        lines = gw.get_recent_logs(max_lines=1)
        self.assertTrue(len(lines) > 0)
        import json
        record = json.loads(lines[0])
        dist = record["gateway"]["distance_to_train_km"]
        # Expected distance between Stasiun Tengah and Bandung is ~54.8 km
        self.assertTrue(50.0 < dist < 60.0, f"Logged distance {dist} not within expected range")

        if log_file.is_file():
            log_file.unlink()


if __name__ == "__main__":
    unittest.main()
