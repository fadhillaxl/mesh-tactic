"""
Unit Tests for Tactical Framing Protocol & CRC16.
"""

import os
import sys
from pathlib import Path

# Ensure src/ is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest
from tsm.common.framing import (
    TacticalFraming,
    crc16_ccitt,
    FRAME_MAGIC,
    MAX_CHUNK_PAYLOAD,
)


class TestFramingAndCRC(unittest.TestCase):
    def setUp(self):
        self.framing = TacticalFraming()

    def test_crc16_ccitt_deterministic(self):
        data1 = b"TSM-Net-SG-Tactical-Packet-12345"
        crc1 = crc16_ccitt(data1)
        crc2 = crc16_ccitt(data1)
        self.assertEqual(crc1, crc2)
        self.assertIsInstance(crc1, int)
        self.assertTrue(0 <= crc1 <= 0xFFFF)

    def test_crc16_detects_single_bit_flip(self):
        data = b"TACTICAL_PAYLOAD_DATA"
        crc_orig = crc16_ccitt(data)

        # Flip 1 bit in the first byte
        corrupted = bytes([data[0] ^ 0x01]) + data[1:]
        crc_corrupt = crc16_ccitt(corrupted)
        self.assertNotEqual(crc_orig, crc_corrupt)

    def test_frame_pack_and_unpack_roundtrip(self):
        payload = b"HQ_REPORT_GRID_ALPHA_CLEAR"
        frame = self.framing.pack(payload, frag_idx=0, total_frags=1)

        result = self.framing.unpack(frame)
        self.assertIsNotNone(result)
        seq, frag_idx, total_frags, unpacked_payload = result

        self.assertEqual(unpacked_payload, payload)
        self.assertEqual(frag_idx, 0)
        self.assertEqual(total_frags, 1)

    def test_frame_sequence_increment(self):
        f1 = self.framing.pack(b"packet_1")
        f2 = self.framing.pack(b"packet_2")
        seq1 = self.framing.unpack(f1)[0]
        seq2 = self.framing.unpack(f2)[0]
        self.assertEqual(seq2, (seq1 + 1) & 0xFFFF)

    def test_unpack_rejects_corrupted_crc(self):
        payload = b"SECRET_TACTICAL_DATA"
        frame = bytearray(self.framing.pack(payload))

        # Corrupt the payload inside the frame
        frame[10] ^= 0xFF
        self.assertIsNone(self.framing.unpack(bytes(frame)))

    def test_unpack_rejects_bad_magic(self):
        payload = b"TEST"
        frame = bytearray(self.framing.pack(payload))
        # Corrupt magic byte
        frame[0] ^= 0x55
        self.assertIsNone(self.framing.unpack(bytes(frame)))

    def test_payload_fragmentation(self):
        large_payload = b"X" * 500
        chunks = TacticalFraming.fragment(large_payload, chunk_size=200)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(len(chunks[0]), 200)
        self.assertEqual(len(chunks[1]), 200)
        self.assertEqual(len(chunks[2]), 100)
        self.assertEqual(b"".join(chunks), large_payload)


if __name__ == "__main__":
    unittest.main()
