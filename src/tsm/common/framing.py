"""
Tactical Framing Protocol & CRC16 Checksum
Standardized framing layer for TSM-Net SG over continuous baseband I/Q SDR.
"""

import struct
from typing import Optional, Tuple, List, Dict

FRAME_MAGIC = 0xD354
HEADER_FORMAT = "!HHBB"  # MAGIC (2B), SEQ (2B), FRAG_INFO (1B), LEN (1B)
HEADER_LEN = struct.calcsize(HEADER_FORMAT)  # 6 bytes
CRC_LEN = 2  # 2 bytes
MAX_CHUNK_PAYLOAD = 240


def crc16_ccitt(data: bytes) -> int:
    """Calculate 16-bit CRC-CCITT (polynomial 0x1021, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


class TacticalFraming:
    """
    Lightweight tactical encapsulation protocol:
    [MAGIC: 2B (0xD354)] [SEQ: 2B] [FRAG_INFO: 1B] [LEN: 1B] [PAYLOAD: N bytes] [CRC16: 2B]
    Header overhead: 6 bytes | Checksum: 2 bytes
    """

    def __init__(self):
        self._seq = 0

    def pack(self, payload: bytes, frag_idx: int = 0, total_frags: int = 1) -> bytes:
        """Packs a payload chunk into an authenticated tactical frame."""
        if len(payload) > MAX_CHUNK_PAYLOAD:
            raise ValueError(f"Payload size {len(payload)} exceeds max chunk ({MAX_CHUNK_PAYLOAD} bytes)")

        self._seq = (self._seq + 1) & 0xFFFF
        frag_info = ((frag_idx & 0x0F) << 4) | (total_frags & 0x0F)
        length = len(payload)

        hdr = struct.pack(HEADER_FORMAT, FRAME_MAGIC, self._seq, frag_info, length)
        body = hdr + payload
        crc = crc16_ccitt(body)
        return body + struct.pack("!H", crc)

    def unpack(self, raw: bytes) -> Optional[Tuple[int, int, int, bytes]]:
        """
        Validates frame framing and CRC.
        Returns: (seq, frag_idx, total_frags, payload) or None if corrupt.
        """
        if len(raw) < HEADER_LEN + CRC_LEN:
            return None

        magic, seq, frag_info, length = struct.unpack(HEADER_FORMAT, raw[:HEADER_LEN])
        if magic != FRAME_MAGIC:
            return None

        expected_len = HEADER_LEN + length + CRC_LEN
        if len(raw) < expected_len:
            return None

        body = raw[: HEADER_LEN + length]
        rx_crc = struct.unpack("!H", raw[HEADER_LEN + length : expected_len])[0]
        calc_crc = crc16_ccitt(body)

        if rx_crc != calc_crc:
            return None

        frag_idx = (frag_info >> 4) & 0x0F
        total_frags = frag_info & 0x0F
        payload = body[HEADER_LEN:]
        return seq, frag_idx, total_frags, payload

    @staticmethod
    def fragment(data: bytes, chunk_size: int = MAX_CHUNK_PAYLOAD) -> List[bytes]:
        """Splits arbitrary payload into chunks sized for transmission."""
        if not data:
            return [b""]
        return [data[i : i + chunk_size] for i in range(0, len(data), chunk_size)]
