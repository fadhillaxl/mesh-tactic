#!/usr/bin/env python3
"""
test_ota_fsk.py - Over-The-Air 2-FSK Packet Transmission & Reception Test
Tests physical 915.000 MHz transmission between Pi 5 (usb:1.3.5) and Pi 2W (ip:192.168.99.240:30431).
"""

import sys
import time
import struct
import numpy as np

CARRIER_FREQ = 915000000
SAMPLE_RATE  = 2500000
SYMBOL_RATE  = 50000
SPS          = SAMPLE_RATE // SYMBOL_RATE  # 50
FREQ_DEV     = 50000
TX_AMPLITUDE = 24000.0
SYNC_WORD    = bytes([0xD3, 0x54, 0x91, 0x50])
SYNC_BITS    = np.unpackbits(np.frombuffer(SYNC_WORD, dtype=np.uint8))
SYNC_BIPOLAR = np.where(SYNC_BITS == 1, 1.0, -1.0).astype(np.float32)
SYNC_LEN     = len(SYNC_BITS)


def calc_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def modulate(payload: bytes) -> np.ndarray:
    preamble = b"\xAA" * 16
    postamble = b"\xAA" * 4
    length_byte = bytes([len(payload)])
    crc = calc_crc16(length_byte + payload)
    crc_bytes = struct.pack("!H", crc)

    wire_bytes = preamble + SYNC_WORD + length_byte + payload + crc_bytes + postamble
    bits = np.unpackbits(np.frombuffer(wire_bytes, dtype=np.uint8))

    freqs = np.where(bits == 1, FREQ_DEV, -FREQ_DEV)
    freqs_upsampled = np.repeat(freqs, SPS)
    phases = 2.0 * np.pi * np.cumsum(freqs_upsampled) / SAMPLE_RATE

    i_samples = (TX_AMPLITUDE * np.cos(phases)).astype(np.int16)
    q_samples = (TX_AMPLITUDE * np.sin(phases)).astype(np.int16)

    iq = np.empty((len(i_samples), 2), dtype=np.int16)
    iq[:, 0] = i_samples
    iq[:, 1] = q_samples
    return iq


def demodulate_buffer(iq_samples: np.ndarray):
    """Demodulates a block of I/Q samples searching for the sync word."""
    i = iq_samples[:, 0].astype(np.float32)
    q = iq_samples[:, 1].astype(np.float32)
    c = i + 1j * q

    # Instantaneous frequency discriminator
    d = c[1:] * np.conj(c[:-1])
    diff_phase = np.angle(d)

    # CFO (DC) cancellation
    diff_phase_ac = diff_phase - np.mean(diff_phase)

    # Moving average filter
    kernel_size = SPS // 2
    kernel = np.ones(kernel_size, dtype=np.float32) / kernel_size
    smoothed = np.convolve(diff_phase_ac, kernel, mode="same")
    bipolar = np.where(smoothed > 0, 1.0, -1.0).astype(np.float32)

    best_corr = -999.0
    best_phase = -1
    best_idx = -1

    for phase in range(0, SPS, 2):
        sampled = bipolar[phase::SPS]
        if len(sampled) < SYNC_LEN:
            continue
        corr = np.correlate(sampled, SYNC_BIPOLAR, mode="valid")
        max_idx = int(np.argmax(corr))
        val = corr[max_idx]
        if val > best_corr:
            best_corr = val
            best_phase = phase
            best_idx = max_idx

    if best_corr < 28.0:  # Threshold for 32-bit sync word (allows up to 2 bit errors)
        return None

    # Sync word detected!
    start_sym = best_idx + SYNC_LEN
    bits_recovered = (bipolar[best_phase::SPS][start_sym:] > 0).astype(np.uint8)
    num_bytes = len(bits_recovered) // 8
    if num_bytes < 3:
        return None

    extracted = np.packbits(bits_recovered[: num_bytes * 8]).tobytes()
    payload_len = extracted[0]
    if len(extracted) < 1 + payload_len + 2:
        return None

    payload = extracted[1 : 1 + payload_len]
    rx_crc = struct.unpack("!H", extracted[1 + payload_len : 1 + payload_len + 2])[0]
    expected_crc = calc_crc16(extracted[: 1 + payload_len])

    if rx_crc == expected_crc:
        return payload, best_corr
    return None
