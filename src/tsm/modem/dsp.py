"""
Pure Digital Signal Processing (DSP) Functions for 2-FSK Modulation & Demodulation.
Hardware-independent, enabling automated offline testing and verification.
"""

import struct
from typing import Optional, Tuple
import numpy as np

from ..common.framing import crc16_ccitt
from .constants import (
    SAMPLE_RATE,
    SPS,
    DECIMATION,
    SPS_DEC,
    FREQ_DEV,
    TX_AMPLITUDE,
    SYNC_WORD,
)


def build_sync_template() -> Tuple[np.ndarray, int]:
    """Precomputes upsampled sync template for fast sub-millisecond correlation."""
    sync_bits = np.unpackbits(np.frombuffer(SYNC_WORD, dtype=np.uint8))
    sync_bipolar = np.where(sync_bits == 1, 1.0, -1.0).astype(np.float32)
    sync_upsampled = np.repeat(sync_bipolar, SPS_DEC)
    return sync_upsampled, len(sync_upsampled)


def modulate_2fsk(payload: bytes, amplitude: float = TX_AMPLITUDE) -> np.ndarray:
    """
    Synthesizes a digital baseband continuous-phase 2-FSK waveform.
    Returns interleaved int16 I/Q samples of shape (N, 2).
    """
    preamble = b"\xAA" * 16
    postamble = b"\xAA" * 4
    length_byte = bytes([len(payload)])
    body_for_crc = length_byte + payload
    crc = crc16_ccitt(body_for_crc)
    crc_bytes = struct.pack("!H", crc)

    wire_bytes = preamble + SYNC_WORD + length_byte + payload + crc_bytes + postamble
    bits = np.unpackbits(np.frombuffer(wire_bytes, dtype=np.uint8))

    freqs = np.where(bits == 1, FREQ_DEV, -FREQ_DEV)
    freqs_upsampled = np.repeat(freqs, SPS)

    # Continuous phase integration
    phases = 2.0 * np.pi * np.cumsum(freqs_upsampled) / SAMPLE_RATE

    i_samples = (amplitude * np.cos(phases)).astype(np.int16)
    q_samples = (amplitude * np.sin(phases)).astype(np.int16)

    iq = np.empty((len(i_samples), 2), dtype=np.int16)
    iq[:, 0] = i_samples
    iq[:, 1] = q_samples
    return iq


def demodulate_2fsk(
    iq_samples: np.ndarray,
    sync_upsampled: np.ndarray,
    sync_len_samples: int,
    threshold: float = 0.55,
) -> Optional[Tuple[bytes, float]]:
    """
    Demodulates baseband I/Q samples into packet payload using:
    1. Decimation (2.5 MSps -> 500 kSps)
    2. Instantaneous Frequency Discriminator
    3. Carrier Frequency Offset (CFO / DC) cancellation
    4. Moving average pulse shaping filter
    5. Fast decimated sync correlation with auto-polarity
    6. Bit slicing & CRC16 validation

    Returns (payload, normalized_correlation) or None.
    """
    if len(iq_samples) < sync_len_samples * DECIMATION:
        return None

    # 1. Decimate by 5 for optimal phase SNR and sub-millisecond execution
    decimated = iq_samples[::DECIMATION]
    c = decimated[:, 0].astype(np.float32) + 1j * decimated[:, 1].astype(np.float32)

    # 2. Instantaneous frequency discriminator
    d = c[1:] * np.conj(c[:-1])
    diff_phase = np.angle(d)

    # 3. Carrier frequency offset (CFO) cancellation
    diff_phase_ac = diff_phase - np.mean(diff_phase)

    # 4. Moving average filter over 1 symbol period
    kernel = np.ones(SPS_DEC, dtype=np.float32) / SPS_DEC
    smoothed = np.convolve(diff_phase_ac, kernel, mode="same")

    # 5. Correlate with upsampled sync template
    corr = np.correlate(smoothed, sync_upsampled, mode="valid")
    if len(corr) == 0:
        return None

    best_sample_idx = int(np.argmax(np.abs(corr)))
    raw_peak = corr[best_sample_idx]
    norm_corr = float(abs(raw_peak) / sync_len_samples)

    if norm_corr < threshold:
        return None

    # Determine polarity (handles Pluto/Pluto+ spectral inversion automatically)
    polarity = 1.0 if raw_peak > 0 else -1.0

    # 6. Sample at symbol centers with Early/On-Time/Late timing search & Adaptive DC Cancellation
    start_sample = best_sample_idx + sync_len_samples + SPS_DEC // 2

    # Preamble-based residual CFO estimation (preamble is alternating 0xAA = 10101010...)
    preamble_start = max(0, best_sample_idx - 128)
    preamble_samples = smoothed[preamble_start:best_sample_idx]
    cfo_residual = float(np.mean(preamble_samples)) if len(preamble_samples) >= 16 else 0.0

    for timing_offset in (0, 1, -1, 2, -2):
        sample_idx = start_sample + timing_offset
        if sample_idx < 0:
            continue

        # Try preamble-derived bias and zero bias (robust to all bit distributions)
        for bias in (cfo_residual, 0.0):
            syms = (polarity * (smoothed - bias))[sample_idx :: SPS_DEC]
            if len(syms) < 24:
                continue

            bits = (syms > 0).astype(np.uint8)
            num_bytes = len(bits) // 8
            if num_bytes < 3:
                continue

            extracted = np.packbits(bits[: num_bytes * 8]).tobytes()
            payload_len = extracted[0]

            if len(extracted) < 1 + payload_len + 2:
                continue

            payload = extracted[1 : 1 + payload_len]
            rx_crc = struct.unpack("!H", extracted[1 + payload_len : 1 + payload_len + 2])[0]
            expected_crc = crc16_ccitt(extracted[: 1 + payload_len])

            if rx_crc == expected_crc:
                return payload, norm_corr

    return None
