"""
Unit tests for .env configuration loader (tsm.config).
"""

import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tsm.config import AppConfig, load_dotenv


class TestConfigLoader(unittest.TestCase):
    def test_dotenv_parser_and_type_casting(self):
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".env") as f:
            f.write("""
# Test configuration file
NODE_ID=0x0042
NODE_ALIAS=testnode
CALLSIGN=TEST-CALL
TARGET_ID=0x0003
SDR_URI=ip:192.168.100.1
CARRIER_FREQ=868000000
SAMPLE_RATE=2000000
RX_GAIN=55.5
TX_ATTEN=-5.0
ENABLE_RELAY=false
DEFAULT_TTL=5
MAC_MODE=slotted
MAC_SLOTS_PER_FRAME=40
MAC_FRAME_DURATION=2.0
USE_SOCKET=true
IS_GATEWAY=true
""")
            temp_path = Path(f.name)

        try:
            # Clear any interfering env vars
            for k in ["NODE_ID", "NODE_ALIAS", "CALLSIGN", "TARGET_ID", "SDR_URI", 
                      "CARRIER_FREQ", "SAMPLE_RATE", "RX_GAIN", "TX_ATTEN", 
                      "ENABLE_RELAY", "DEFAULT_TTL", "MAC_MODE", "MAC_SLOTS_PER_FRAME", 
                      "MAC_FRAME_DURATION", "USE_SOCKET", "IS_GATEWAY"]:
                os.environ.pop(k, None)

            load_dotenv(temp_path)
            cfg = AppConfig.load()

            self.assertEqual(cfg.node_id, 0x0042)
            self.assertEqual(cfg.node_alias, "testnode")
            self.assertEqual(cfg.callsign, "TEST-CALL")
            self.assertEqual(cfg.target_id, 0x0003)
            self.assertEqual(cfg.sdr_uri, "ip:192.168.100.1")
            self.assertEqual(cfg.carrier_freq, 868_000_000)
            self.assertEqual(cfg.sample_rate, 2_000_000)
            self.assertEqual(cfg.rx_gain, 55.5)
            self.assertEqual(cfg.tx_atten, -5.0)
            self.assertFalse(cfg.enable_relay)
            self.assertEqual(cfg.default_ttl, 5)
            self.assertEqual(cfg.mac_mode, "slotted")
            self.assertEqual(cfg.mac_slots_per_frame, 40)
            self.assertEqual(cfg.mac_frame_duration, 2.0)
            self.assertTrue(cfg.use_socket)
            self.assertTrue(cfg.is_gateway)
        finally:
            if temp_path.exists():
                temp_path.unlink()


if __name__ == "__main__":
    unittest.main()
