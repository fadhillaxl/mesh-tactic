"""
Unit Tests for Configuration Loader and Fallback Parser.
"""

import os
import sys
from pathlib import Path

# Ensure src/ is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest
from tsm.common.config import load_config, Config, _parse_yaml_fallback


class TestConfig(unittest.TestCase):
    def test_load_default_config(self):
        cfg = load_config("config/config.yaml")
        self.assertIsInstance(cfg, Config)
        self.assertEqual(cfg.sdr.center_freq, 915_000_000)
        self.assertEqual(cfg.network.tap_device, "tap-radio")
        self.assertEqual(cfg.network.bat_device, "bat0")
        self.assertEqual(cfg.network.mtu, 180)

    def test_fallback_yaml_parser(self):
        sample_yaml = """
sdr:
  uri: "usb:1.3.5"
  center_freq: 915000000
  sample_rate: 2500000
  tx_gain: -5.0
  rx_gain: 60.0

network:
  tap_device: "tap-test"
  mtu: 180
  ipc_tx_port: 52001
"""
        parsed = _parse_yaml_fallback(sample_yaml)
        self.assertIn("sdr", parsed)
        self.assertIn("network", parsed)
        self.assertEqual(parsed["sdr"]["uri"], "usb:1.3.5")
        self.assertEqual(parsed["sdr"]["center_freq"], 915000000)
        self.assertEqual(parsed["network"]["tap_device"], "tap-test")
        self.assertEqual(parsed["network"]["mtu"], 180)


if __name__ == "__main__":
    unittest.main()
