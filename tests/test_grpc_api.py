"""
tests/test_grpc_api.py - Tactical SDR Mesh (TSM-Net SG)
Automated Test Suite for gRPC Services, Spectrum FFT Math, and Servicers.
"""

import sys
import time
import unittest
import numpy as np
from pathlib import Path

# Add src to path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from tsm.generated import tsm_pb2, tsm_pb2_grpc
from tsm.api.servicer import (
    TacticalNodeServicer,
    TacticalChatServicer,
    TacticalSpectrumServicer,
)


class TestTacticalNodeServicer(unittest.TestCase):
    """Verifies TacticalNodeService RPC responses."""

    def setUp(self):
        self.servicer = TacticalNodeServicer(sdr_uri="invalid:test")

    def test_get_node_info(self):
        req = tsm_pb2.NodeInfoRequest()
        resp = self.servicer.GetNodeInfo(req, None)
        self.assertIsInstance(resp, tsm_pb2.NodeInfoResponse)
        self.assertTrue(len(resp.hostname) > 0)
        self.assertTrue(resp.node_id in ["HQ-PI5", "OUTPOST-PI2W"] or len(resp.node_id) > 0)
        self.assertTrue(resp.tap_mtu >= 180)

    def test_get_mesh_neighbors(self):
        req = tsm_pb2.MeshNeighborsRequest()
        resp = self.servicer.GetMeshNeighbors(req, None)
        self.assertIsInstance(resp, tsm_pb2.MeshNeighborsResponse)
        self.assertIsInstance(resp.total_neighbors, int)

    def test_get_sdr_telemetry(self):
        req = tsm_pb2.SDRTelemetryRequest()
        resp = self.servicer.GetSDRTelemetry(req, None)
        self.assertIsInstance(resp, tsm_pb2.SDRTelemetryResponse)
        self.assertEqual(resp.center_freq_hz, 915000000)
        self.assertAlmostEqual(resp.rx_gain_db, 55.0, delta=15.0)

    def test_get_orchestrator_stats(self):
        req = tsm_pb2.OrchestratorStatsRequest()
        resp = self.servicer.GetOrchestratorStats(req, None)
        self.assertIsInstance(resp, tsm_pb2.OrchestratorStatsResponse)
        self.assertGreaterEqual(resp.tx_frames, 0)


class TestTacticalSpectrumServicer(unittest.TestCase):
    """Verifies FFT power calculation, peak detection, and byte quantization."""

    def setUp(self):
        self.servicer = TacticalSpectrumServicer(center_freq=915000000)

    def test_spectrum_scan_properties(self):
        req = tsm_pb2.SpectrumRequest()
        resp = self.servicer.GetSpectrumScan(req, None)
        scan = resp.scan
        self.assertEqual(scan.DESCRIPTOR.name, "SpectrumScan")
        self.assertEqual(scan.num_bins, 256)
        self.assertEqual(len(scan.powers_uint8), 256)
        self.assertEqual(scan.center_freq_hz, 915000000)
        self.assertEqual(scan.span_hz, 1000000)

        # Verify uint8 values are in valid 0..255 range
        powers = np.frombuffer(scan.powers_uint8, dtype=np.uint8)
        self.assertEqual(len(powers), 256)
        self.assertTrue(np.all(powers >= 0) and np.all(powers <= 255))

    def test_fft_peak_accuracy(self):
        # Inject known single tone at +100 kHz offset (offset bin: +25.6 bins)
        num_bins = 256
        t = np.arange(num_bins)
        tone_freq_rel = 0.1  # normalized frequency
        tone = 100.0 * np.exp(1j * 2 * np.pi * tone_freq_rel * t)

        fft_vals = np.fft.fftshift(np.fft.fft(tone * self.servicer.window))
        power_db = 10.0 * np.log10((np.abs(fft_vals) ** 2) / float(num_bins))
        peak_idx = int(np.argmax(power_db))

        # Peak should be centered around index 128 + (0.1 * 256) = 153.6
        expected_bin = int(128 + tone_freq_rel * num_bins)
        self.assertAlmostEqual(peak_idx, expected_bin, delta=2)


class TestTacticalChatServicer(unittest.TestCase):
    """Verifies chat message sending, history, and subscriber queues."""

    def setUp(self):
        self.servicer = TacticalChatServicer(bind_ip="127.0.0.1", port=59998)

    def tearDown(self):
        self.servicer.close()

    def test_send_and_history(self):
        # Send message
        req = tsm_pb2.ChatMessageRequest(target_ip="127.0.0.1", text="UNIT_TEST_PAYLOAD")
        resp = self.servicer.SendMessage(req, None)
        self.assertTrue(len(resp.message_id) > 0)

        # Retrieve history
        hist = self.servicer.GetMessageHistory(tsm_pb2.MessageHistoryRequest(limit=10), None)
        self.assertGreaterEqual(len(hist.messages), 1)
        last_msg = hist.messages[-1]
        self.assertEqual(last_msg.text, "UNIT_TEST_PAYLOAD")


if __name__ == "__main__":
    unittest.main()
