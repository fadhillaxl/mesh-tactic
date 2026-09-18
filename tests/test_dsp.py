"""
Unit Tests for Pure 2-FSK Digital Signal Processing (DSP).
Verifies modulation, demodulation, CFO offset tolerance, and auto-polarity.
"""

import os
import sys
from pathlib import Path

# Ensure src/ is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest
import numpy as np

from tsm.modem.dsp import (
    build_sync_template,
    modulate_2fsk,
    demodulate_2fsk,
)
from tsm.modem.constants import (
    SAMPLE_RATE,
    TX_AMPLITUDE,
)


class TestModemDSP(unittest.TestCase):
    def setUp(self):
        self.sync_upsampled, self.sync_len = build_sync_template()

    def test_modulation_shape_and_dtype(self):
        payload = b"TACTICAL_PING_TEST_123"
        iq = modulate_2fsk(payload, amplitude=TX_AMPLITUDE)

        self.assertIsInstance(iq, np.ndarray)
        self.assertEqual(iq.dtype, np.int16)
        self.assertEqual(iq.ndim, 2)
        self.assertEqual(iq.shape[1], 2)
        self.assertGreater(len(iq), 1000)

        # Ensure peak does not exceed int16 range
        self.assertLessEqual(np.max(np.abs(iq)), 32767)

    def test_clean_roundtrip(self):
        payload = b"HQ_TO_OUTPOST_ALL_CLEAR_915MHZ"
        iq = modulate_2fsk(payload)

        # Demodulate directly
        result = demodulate_2fsk(iq, self.sync_upsampled, self.sync_len)
        self.assertIsNotNone(result, "Failed to demodulate clean modulated waveform")
        decoded_payload, corr = result
        self.assertEqual(decoded_payload, payload)
        self.assertGreater(corr, 0.30)

    def test_roundtrip_with_cfo_offset(self):
        """Simulate a 15 kHz Carrier Frequency Offset between transmitter and receiver."""
        payload = b"CFO_TOLERANCE_VERIFICATION_PACKET"
        iq = modulate_2fsk(payload)

        # Inject +15 kHz frequency shift: multiply complex baseband by e^(j * 2*pi * df * t)
        cfo_hz = 15000.0
        t = np.arange(len(iq)) / SAMPLE_RATE
        cfo_carrier = np.exp(1j * 2.0 * np.pi * cfo_hz * t)

        c = iq[:, 0].astype(np.float32) + 1j * iq[:, 1].astype(np.float32)
        c_shifted = c * cfo_carrier

        iq_shifted = np.empty_like(iq)
        iq_shifted[:, 0] = np.real(c_shifted).astype(np.int16)
        iq_shifted[:, 1] = np.imag(c_shifted).astype(np.int16)

        result = demodulate_2fsk(iq_shifted, self.sync_upsampled, self.sync_len)
        self.assertIsNotNone(result, "Modem failed to tolerate 15 kHz CFO offset")
        decoded_payload, corr = result
        self.assertEqual(decoded_payload, payload)

    def test_roundtrip_with_inverted_polarity(self):
        """Simulate spectral inversion / IQ swap between SDR units."""
        payload = b"AUTO_POLARITY_INVERTED_IQ_TEST"
        iq = modulate_2fsk(payload)

        # Invert Q channel to swap spectrum / polarity
        iq_inverted = iq.copy()
        iq_inverted[:, 1] = -iq_inverted[:, 1]

        result = demodulate_2fsk(iq_inverted, self.sync_upsampled, self.sync_len)
        self.assertIsNotNone(result, "Auto-polarity failed on inverted spectrum")
        decoded_payload, corr = result
        self.assertEqual(decoded_payload, payload)

    def test_noise_rejection(self):
        """Pure Gaussian noise should not trigger false demodulation."""
        np.random.seed(42)
        noise_samples = np.random.normal(0, 500, size=(10000, 2)).astype(np.int16)

        result = demodulate_2fsk(noise_samples, self.sync_upsampled, self.sync_len)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
