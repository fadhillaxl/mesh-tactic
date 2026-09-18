#!/usr/bin/env python3
"""
test_modem_math.py - Mathematical Verification of the Tactical 2-FSK Digital Modem
Verifies FSK modulation, preamble detection, sync word correlation, and demodulation.
"""

import struct
import numpy as np

CARRIER_FREQ = 915000000  # 915 MHz
SAMPLE_RATE = 2500000     # 2.5 MSps
SYMBOL_RATE = 50000       # 50 kbps
SPS = SAMPLE_RATE // SYMBOL_RATE  # 50 samples per symbol
FREQ_DEV = 50000          # +/- 50 kHz deviation
SYNC_WORD = bytes([0xD3, 0x54, 0x91, 0x50])


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


def modulate_packet(payload: bytes, amplitude: float = 20000.0) -> np.ndarray:
    """Modulates a packet into baseband complex int16 I/Q samples."""
    # Build complete over-the-air packet
    preamble = b"\xAA" * 16
    postamble = b"\xAA" * 4
    length_byte = bytes([len(payload)])
    crc = calc_crc16(length_byte + payload)
    crc_bytes = struct.pack("!H", crc)
    
    wire_bytes = preamble + SYNC_WORD + length_byte + payload + crc_bytes + postamble
    bits = np.unpackbits(np.frombuffer(wire_bytes, dtype=np.uint8))
    
    # 2-FSK Frequency mapping: bit 1 -> +FREQ_DEV, bit 0 -> -FREQ_DEV
    freqs = np.where(bits == 1, FREQ_DEV, -FREQ_DEV)
    freqs_upsampled = np.repeat(freqs, SPS)
    
    # Continuous phase integration
    phases = 2.0 * np.pi * np.cumsum(freqs_upsampled) / SAMPLE_RATE
    
    # Synthesize I and Q
    i_samples = (amplitude * np.cos(phases)).astype(np.int16)
    q_samples = (amplitude * np.sin(phases)).astype(np.int16)
    
    # Interleave I and Q
    iq = np.empty((len(i_samples), 2), dtype=np.int16)
    iq[:, 0] = i_samples
    iq[:, 1] = q_samples
    return iq


def demodulate_iq(iq_samples: np.ndarray) -> bytes:
    """Demodulates complex I/Q baseband samples back into packet payload."""
    i = iq_samples[:, 0].astype(np.float32)
    q = iq_samples[:, 1].astype(np.float32)
    complex_signal = i + 1j * q
    
    # Frequency discriminator: phase difference
    d = complex_signal[1:] * np.conj(complex_signal[:-1])
    diff_phase = np.angle(d)
    
    # Moving average filter over SPS // 2 samples
    kernel_size = SPS // 2
    kernel = np.ones(kernel_size) / kernel_size
    smoothed = np.convolve(diff_phase, kernel, mode="same")
    
    # Hard decision: > 0 is bit 1, < 0 is bit 0
    raw_bits = (smoothed > 0).astype(np.uint8)
    
    # Sub-sample bits at symbol center (phase offset: SPS // 2)
    # Search for sync word by sliding over sample offsets
    sync_bits = np.unpackbits(np.frombuffer(SYNC_WORD, dtype=np.uint8))
    sync_len = len(sync_bits)
    
    best_offset = None
    min_errors = 999
    
    # Search across SPS possible sampling phases
    for phase in range(SPS):
        sampled = raw_bits[phase::SPS]
        if len(sampled) < sync_len:
            continue
        # Correlate with sync word
        for idx in range(len(sampled) - sync_len):
            window = sampled[idx : idx + sync_len]
            errs = np.sum(window != sync_bits)
            if errs < min_errors:
                min_errors = errs
                if errs <= 1:  # Allow up to 1 bit error in sync
                    best_offset = (phase, idx + sync_len)
                    break
        if best_offset:
            break
            
    if not best_offset:
        return None
        
    phase, start_bit_idx = best_offset
    sampled_bits = raw_bits[phase::SPS][start_bit_idx:]
    
    # Pack bits back into bytes
    num_bytes = len(sampled_bits) // 8
    if num_bytes < 3:
        return None
        
    extracted_bytes = np.packbits(sampled_bits[: num_bytes * 8]).tobytes()
    payload_len = extracted_bytes[0]
    
    if len(extracted_bytes) < 1 + payload_len + 2:
        return None
        
    payload = extracted_bytes[1 : 1 + payload_len]
    rx_crc = struct.unpack("!H", extracted_bytes[1 + payload_len : 1 + payload_len + 2])[0]
    expected_crc = calc_crc16(extracted_bytes[: 1 + payload_len])
    
    if rx_crc == expected_crc:
        return payload
    return None


def main():
    test_msg = b"TACTICAL_BATMAN_0xD354_PACKET_VERIFICATION_SAMPLE"
    print(f"[TEST] Modulating test packet ({len(test_msg)} bytes)...")
    iq = modulate_packet(test_msg)
    print(f"[TEST] Synthesized {len(iq)} complex I/Q baseband samples.")
    
    # Add simulated AWGN channel noise
    noise_power = 2000.0
    noisy_iq = iq.copy()
    noisy_iq += np.random.normal(0, noise_power, noisy_iq.shape).astype(np.int16)
    
    print("[TEST] Demodulating through FSK discriminator...")
    recovered = demodulate_iq(noisy_iq)
    
    if recovered == test_msg:
        print(f"[SUCCESS] 100% Bit-Accurate Payload Recovery: {recovered}")
        print("[PASS] 2-FSK Digital Modem Mathematical Verification Complete!")
    else:
        print(f"[FAIL] Payload mismatch or CRC error. Got: {recovered}")


if __name__ == "__main__":
    main()
