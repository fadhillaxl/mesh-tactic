# Tactical SDR Direct RF Chat (915.000 MHz)

Lightweight, high-performance tactical terminal chat communicating directly over **ADALM-PLUTO SDR** transceivers via **2-FSK** on the static ISM carrier **915.000 MHz**.

Operates entirely over-the-air (OTA) without requiring Linux kernel modules, TAP network interfaces, or IP routing overhead.

---

## Key Features

- **Direct RF PHY Link**: 50,000 Baud (50 kbps) 2-FSK modulation at 2.50 MSps baseband with $\pm50\text{ kHz}$ deviation.
- **Robust DSP Pipeline**:
  - Carrier Frequency Offset (CFO) cancellation & adaptive DC drift tracking.
  - Moving-average pulse shaping & decimated correlation sync with automatic spectral inversion detection.
  - 16-bit CRC-CCITT error detection.
- **3-Burst Redundancy**: Automatically transmits 3 bursts per message with optimal DMA timing to guarantee delivery over the air.
- **Cross-Platform Compatibility**:
  - **Linux (Raspberry Pi 5, Armbian / AML SBC)**: Uses native `python3-libiio` package (`libiio.so.0`).
  - **macOS (Apple Silicon / Intel)**: Built-in smart shim with fallback to native `/Library/Frameworks/iio.framework/iio`.
- **Zero Bloat**: Pure Python standard library + `numpy`.

---

## Repository Structure

```text
mesh-tactic/
├── mesh_chat.py         # Main interactive tactical RF chat CLI
├── src/
│   ├── iio.py           # Smart libiio shim (system dist-packages on Linux, fallback on macOS)
│   ├── _iio_vendored.py # Vendored ctypes libiio bindings for macOS
│   └── tsm/
│       ├── common/
│       │   └── framing.py   # CRC-16-CCITT packet integrity checksums
│       └── modem/
│           ├── constants.py # RF physical layer constants (915 MHz, 2.5 MSps, 50 kbps)
│           └── dsp.py       # Pure 2-FSK modulation and demodulation algorithms
├── tests/
│   └── test_dsp.py      # Automated unit tests for DSP mod/demod & CFO tolerance
├── requirements.txt     # Minimal dependencies (numpy>=1.17)
└── README.md
```

---

## Quick Start

### 1. Requirements

- Python 3.8+
- `numpy>=1.17`
- `libiio` installed on your operating system:
  - **Debian / Ubuntu / Armbian**: `sudo apt install python3-libiio libiio-utils`
  - **macOS**: Installed via official ADI `libiio` installer package.

Install Python dependencies:
```bash
pip3 install -r requirements.txt
```

---

### 2. Running Tactical Chat

#### On macOS:
Connect Pluto SDR via USB (bridged to `192.168.2.10`):
```bash
python3 mesh_chat.py --node mac
```

#### On Raspberry Pi 5:
Connect Pluto SDR via USB (e.g. `usb:1.3.5`):
```bash
python3 mesh_chat.py --node pi5
```

#### On Armbian / AML SBC:
Connect Pluto SDR via USB/RNDIS IP (e.g. `ip:192.168.99.240`):
```bash
python3 mesh_chat.py --node aml --uri ip:192.168.99.240
```

---

### 3. Command Line Options

```bash
python3 mesh_chat.py --help
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--node` | auto | Node profile (`mac`, `pi5`, `aml`) |
| `--uri` | auto | Pluto SDR IIO URI (`usb:1.3.5` or `ip:192.168.x.x`) |
| `--callsign` | auto | Operator callsign (e.g. `COMMANDER-MAC`, `HQ-PI5`, `OUTPOST-PI2W`) |
| `--rx-gain` | `65.0` | RX Hardware Gain in dB (0 to 73 dB) |
| `--tx-atten` | `0.0` | TX Attenuation in dB (0.0 dB = maximum RF output) |

---

## Testing & Verification

Run automated DSP unit tests (modulation, demodulation, CFO tolerance, spectral inversion, noise rejection):

```bash
python3 -m unittest tests/test_dsp.py
```
