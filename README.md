# Tactical SDR Mesh Network (TSM-Net SG)

Enterprise-grade decentralized tactical IP Mobile Ad-hoc Network (MANET) bridging Linux kernel-level mesh routing (`batman-adv`) with an SDR continuous digital baseband I/Q modem (`ADALM-PLUTO` / `Pluto+ SDR`) locked to a static ISM frequency (**915.000 MHz**).

Engineered following the **[DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail)** philosophy:
- **Zero-Bloat & Modular**: Pure Python standard library + NumPy. No heavy runtime dependencies on resource-constrained tactical nodes (e.g. Raspberry Pi Zero 2 W).
- **Clean Decoupling**: Pure DSP algorithms (modulation, decimation, discriminator, CFO correction, auto-polarity) are completely separated from hardware I/O for 100% automated testability.
- **Strict MTU Clamping**: Enforces link-level MTU (180 bytes) and 8-byte framing with CRC-16-CCITT integrity verification.
- **Resilient & Anti-Echo**: Auto-detects local TAP MAC to drop self-transmissions and handles spectral inversion (IQ swap) automatically.

---

## Architecture & Directory Layout

```
mesh-tactic/
├── config/                      # Centralized Configuration
│   └── config.yaml              # Active node configuration
├── docs/                        # Architecture & Verification Documentation
│   ├── Tactical_SDR_Mesh_PRD.md # Product Requirements Document
│   └── TACTICAL_MESH_VERIFICATION_REPORT.md # 5-Tier Over-the-air verification report
├── scripts/                     # Shell & Deployment Scripts
│   ├── run_node.sh              # Single-command node runner
│   └── setup_batman.sh          # Kernel MANET (batman-adv) & TAP setup
├── src/                         # Core Modular Python Package (`tsm`)
│   └── tsm/
│       ├── common/              # Shared Protocols & Config Loader
│       │   ├── config.py        # Typed configuration models with YAML fallback
│       │   └── framing.py       # Tactical framing (0xD354) & CRC16-CCITT
│       ├── modem/               # Physical Layer SDR & Baseband DSP
│       │   ├── constants.py     # RF parameters (915 MHz, 2.5 MSps, 50 kbps)
│       │   ├── dsp.py           # Pure 2-FSK DSP algorithms (mod/demod/CFO)
│       │   └── sdr_driver.py    # ADALM-PLUTO IIO hardware streaming interface
│       ├── network/             # Linux Network & Layer-2/3 Routing
│       │   ├── tap_bridge.py    # Native Linux /dev/net/tun TAP interface
│       │   └── orchestrator.py  # TAP <-> Modem IPC bridge & loopback filter
│       └── apps/                # Tactical User Applications
│           └── chat.py          # Interactive terminal chat over bat0
├── tests/                       # Automated Test Suites
│   ├── conftest.py              # Pytest configuration
│   ├── test_dsp.py              # Unit tests for 2-FSK DSP & CFO tolerance
│   ├── test_framing.py          # Unit tests for framing and CRC16
│   ├── test_config.py           # Unit tests for configuration loader
│   ├── test_batman_pipeline.py  # Integration test: Kernel TAP <-> loopback
│   ├── test_hardware.py         # Direct Pluto IIO hardware detection
│   ├── test_ota_fsk.py          # Over-the-air RF verification
│   └── test_rf_link.py          # End-to-end multi-node RF test harness
├── tools/                       # Simulation & Legacy Utilities
│   ├── simulate_mesh.py         # Multi-node mesh simulator
│   ├── gr_lora_pluto_bridge.py  # Alternative GNU Radio LoRa bridge
│   └── tactical_chat_legacy.py  # Legacy serial communicator prototype
│
# Root Backward-Compatibility Entrypoints:
├── run_node.sh                  # Wrapper -> scripts/run_node.sh
├── setup_batman.sh              # Wrapper -> scripts/setup_batman.sh
├── sdr_rf_modem.py              # Wrapper -> tsm.modem.sdr_driver
├── tsm_mesh_orchestrator.py     # Wrapper -> tsm.network.orchestrator
├── mesh_chat.py                 # Wrapper -> tsm.apps.chat
└── pyproject.toml               # Modern Python build metadata
```

---

## Quick Start Guide

### 1. Requirements & Installation

On Raspberry Pi OS (64-bit Debian Bookworm):
```bash
sudo apt update
sudo apt install -y batctl libiio-utils python3-libiio python3-numpy python3-yaml
```

Clone the repository and install in editable mode:
```bash
git clone https://github.com/mm/mesh-tactic.git
cd mesh-tactic
pip3 install -e .
```

---

### 2. Running a Node

#### Node 1 (Command Post / Raspberry Pi 5):
```bash
sudo ./run_node.sh 10.10.0.1/24
```

#### Node 2 (Tactical Outpost / Raspberry Pi Zero 2 W):
```bash
sudo ./run_node.sh 10.10.0.2/24
```

This single command automatically:
1. Loads `batman-adv` kernel module and creates `tap-radio` with MTU clamped to 180 bytes.
2. Enslaves `tap-radio` into `bat0` and assigns the tactical mesh IP.
3. Launches the continuous 2-FSK baseband modem at 915.000 MHz.
4. Starts the orchestrator bridging kernel packets into RF frames.

---

### 3. Interactive Tactical Chat

Open a new terminal on each node and launch the chat application:

* **On Pi 5:**
  ```bash
  python3 mesh_chat.py --node pi5
  ```

* **On Pi 2W:**
  ```bash
  python3 mesh_chat.py --node pi2w
  ```

Type a message and press **[ENTER]**. The text is packetized into IP/UDP datagrams over `bat0`, modulated into 915.000 MHz I/Q baseband, radiated into the air, and displayed on the remote terminal.

---

### 4. Running Automated Tests

Run the pure DSP and framing test suite offline (no SDR hardware required):
```bash
python3 -m unittest tests/test_framing.py tests/test_dsp.py tests/test_config.py
```

All 14 unit tests will execute in under 0.1 seconds, verifying:
- 16-bit CRC-CCITT deterministic integrity & single-bit flip detection.
- Frame sequence incrementation, magic validation (`0xD354`), and fragmentation.
- Pure 2-FSK modulation waveform synthesis and 16-bit DAC boundary clamping.
- End-to-end demodulation accuracy with **Carrier Frequency Offset (CFO $\pm 15$ kHz)**.
- Automatic spectral polarity detection (IQ swap between Pluto and Pluto+).
- False-positive noise rejection on pure Gaussian noise.

---

### 5. Inspecting Link Telemetry

* **View mesh neighbors:**
  ```bash
  sudo batctl n
  ```

* **View routing table:**
  ```bash
  sudo batctl o
  ```

* **Monitor live packet bridging:**
  ```bash
  tail -f orch.log
  ```
