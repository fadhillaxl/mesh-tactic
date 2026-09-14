# Tactical SDR Mesh Network (TSM-Net SG) - Baseline Node

Baseline decentralized tactical IP Mobile Ad-hoc Network (MANET) bridging Linux kernel-level mesh routing (`batman-adv`) with an SDR-based LoRa physical layer (`gr-lora_sdr`) on a **Raspberry Pi 5** using a **Pluto+ SDR** transceiver locked to a **single static frequency** (e.g. 915 MHz).

Engineered following the **[DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail)** philosophy:
- **Pragmatic & Zero-Bloat**: Uses Python standard library (`fcntl`, `os`, `struct`, `select`, `socket`) to interface directly with the Linux kernel `/dev/net/tun` interface. No flaky, unmaintained third-party TUN wrappers.
- **Clean Decoupling**: High-speed GNU Radio C++ DSP is decoupled from the Linux network event loop via localhost loopback Socket PDUs.
- **Strict MTU Clamping**: Enforces link-level MTU (180 bytes) and 8-byte framing with CRC-16 integrity verification to respect the 255-byte LoRa physical frame boundary.

---

## Architecture

```
 ┌─────────────────────────────────────────────────────────┐
 │   Tactical Applications / User Traffic (e.g. ATAK, IP)   │
 └────────────────────────────┬────────────────────────────┘
                              │
 ┌────────────────────────────▼────────────────────────────┐
 │      Linux Kernel Mesh: batman-adv (Interface: bat0)    │
 └────────────────────────────┬────────────────────────────┘
                              │ Layer 2 Ethernet Frames
 ┌────────────────────────────▼────────────────────────────┐
 │       Linux Virtual TAP Device: tap-radio (IFF_TAP)     │
 └────────────────────────────┬────────────────────────────┘
                              │ Non-blocking raw frame I/O (fcntl)
 ┌────────────────────────────▼────────────────────────────┐
 │      tsm_mesh_orchestrator.py (Network Ingestor)        │
 │  - Reads Ethernet frames from tap-radio                 │
 │  - Packs Tactical Framing [MAGIC | SEQ | LEN | CRC16]   │
 └─────────────────────┬───────────────────▲───────────────┘
     UDP 127.0.0.1:52001│                   │UDP 127.0.0.1:52002
 ┌─────────────────────▼───────────────────┴───────────────┐
 │      gr_lora_pluto_bridge.py (GNU Radio 3.10 Flowgraph) │
 │   - Tx Chain: PDU -> lora_sdr.modulate -> Pluto Sink    │
 │   - Rx Chain: Pluto Source -> lora_sdr.frame_sync/decode│
 └─────────────────────┬───────────────────▲───────────────┘
          Baseband I/Q │                   │Baseband I/Q
 ┌─────────────────────▼───────────────────┴───────────────┐
 │        Pluto+ SDR (AD9363/4) via LibIIO (USB / GbE)     │
 │        Fixed Static Frequency: 915.0 MHz (No Hopping)   │
 └─────────────────────────────────────────────────────────┘
```

---

## 1. Prerequisites & Dependencies (Raspberry Pi 5)

Run these steps on **Raspberry Pi OS 64-bit (Debian 12 Bookworm)**.

### Step 1.1: System Packages & Kernel Routing
```bash
sudo apt update
sudo apt install -y \
    batctl \
    batman-adv \
    libiio-utils \
    gnuradio \
    gnuradio-dev \
    cmake \
    git \
    build-essential \
    python3-pip \
    python3-yaml \
    python3-pyroute2
```

### Step 1.2: Build & Install `gr-lora_sdr`
Compile the LoRa SDR GNU Radio Out-of-Tree (OOT) module:
```bash
cd ~
git clone https://github.com/tapparelj/gr-lora_sdr.git
cd gr-lora_sdr
mkdir build && cd build
cmake ..
make -j$(nproc)
sudo make install
sudo ldconfig
```

### Step 1.3: Verify Pluto+ SDR Detection
Connect the Pluto+ SDR to the Raspberry Pi 5 via USB or Ethernet. Verify libiio detection:
```bash
# For default USB RNDIS interface:
iio_info -u ip:192.168.2.1

# Verify Local Oscillator (LO) frequency control is accessible:
iio_attr -u ip:192.168.2.1 -c adi,ad9361-phy altvoltage0 frequency
```

---

## 2. Kernel Mesh Setup (`setup_batman.sh`)

Before starting the radio bridge, initialize `batman-adv` and the clamped TAP interface:

```bash
# Make executable
chmod +x setup_batman.sh

# On Node 1:
sudo ./setup_batman.sh 10.10.0.1/24

# On Node 2:
sudo ./setup_batman.sh 10.10.0.2/24
```

This script:
1. Loads `batman-adv` and sets routing algorithm to `BATMAN_IV`.
2. Creates the persistent Layer-2 TAP device `tap-radio` with MTU **180 bytes**.
3. Enslaves `tap-radio` into `bat0`.
4. Activates `bat0` with the specified node IP address.

---

## 3. Running the Mesh Node

### Step 3.1: Start the GNU Radio Physical Bridge
Launches the GNU Radio flowgraph, locks Pluto+ to the static frequency (915.0 MHz), and listens on loopback UDP Socket PDUs:
```bash
python3 gr_lora_pluto_bridge.py --config config.yaml
```

### Step 3.2: Start the Mesh Orchestrator
In a separate terminal or tmux window, launch the network orchestrator:
```bash
sudo python3 tsm_mesh_orchestrator.py --config config.yaml
```

*(Optional Dry-Run Test without SDR hardware)*:
```bash
sudo python3 tsm_mesh_orchestrator.py --config config.yaml --dry-run
```

---

## 4. Verification & Testing

### Test 1: Mesh Neighbor Discovery
Check if `batman-adv` discovers the remote node over the LoRa physical link:
```bash
sudo batctl n
```
You will see the remote node's MAC address and link quality rating.

### Test 2: End-to-End Tactical IP Ping
From Node 1 (10.10.0.1):
```bash
ping -c 5 10.10.0.2
```

### Test 3: Tactical Text Messaging (Netcat)
On Node 2:
```bash
nc -l -u -p 9999
```
On Node 1:
```bash
echo "SITREP: Alpha team reached objective." | nc -u 10.10.0.2 9999
```

---

## Configuration Reference (`config.yaml`)

| Key | Default | Description |
| :--- | :--- | :--- |
| `sdr.center_freq` | `915000000` | Static carrier frequency in Hz (915 MHz ISM) |
| `sdr.sample_rate` | `1000000` | 1 MSps baseband sampling rate |
| `sdr.tx_gain` | `-10` | Output attenuation in dB (0 dB is max power) |
| `sdr.rx_gain` | `55` | Receiver RF amplification in dB |
| `lora.spreading_factor` | `7` | LoRa Spreading Factor (SF7 = lowest latency) |
| `lora.bandwidth` | `125000` | Modulation bandwidth (125 kHz) |
| `lora.sync_word` | `0x12` | Private tactical mesh sync word |
| `network.mtu` | `180` | Link MTU clamped to fit LoRa physical payload |
