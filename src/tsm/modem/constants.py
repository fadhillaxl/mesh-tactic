"""
Physical Layer Radio Frequency (RF) and Digital Signal Processing (DSP) Constants.
"""

CARRIER_FREQ: int = 915_000_000   # 915.000 MHz Static ISM Carrier
SAMPLE_RATE: int = 2_500_000      # 2.50 MSps Baseband Rate
SYMBOL_RATE: int = 50_000         # 50,000 Baud (50 kbps)
SPS: int = SAMPLE_RATE // SYMBOL_RATE  # 50 samples per symbol
DECIMATION: int = 10              # Decimate 2.5 MSps -> 250 kSps
SPS_DEC: int = SPS // DECIMATION  # 5 samples per symbol decimated
FREQ_DEV: int = 50_000            # +/- 50 kHz deviation
TX_AMPLITUDE: float = 24000.0     # 16-bit DAC amplitude (max 32767)
SYNC_WORD: bytes = bytes([0xD3, 0x54, 0x91, 0x50])  # 32-bit Tactical Sync Word
RX_BUF_SIZE: int = 131072         # 128K samples DMA buffer (~52.4 ms)
TX_BUF_SIZE: int = 131072         # 128K samples reusable Tx DMA buffer (~52.4 ms)
