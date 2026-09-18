# ARCHITECTURE & PRODUCT REQUIREMENT DOCUMENT (PRD)
## Tactical SDR Mesh Network - Secure GPS-Less (TSM-Net SG)

---

## 1. Project Overview & Objective
*   **Project Name:** Tactical SDR Mesh Network - Secure GPS-Less (TSM-Net SG)
*   **Objective:** To build an off-grid, decentralized, and infrastructure-less tactical IP Mobile Ad-hoc Network (MANET) using a **Raspberry Pi** and a **Pluto+ SDR** transceiver.
*   **Key Characteristics:**
    *   **Zero Infrastructure:** No central towers, access points, or satellite reliance.
    *   **GPS-Independent:** Operates in denied environments (caves, bunkers, heavy electronic warfare) using highly precise localized physical hardware clocks (RTC) and dynamic network beaconing.
    *   **Electronic Counter-Countermeasures (ECCM):** Features robust protection against jamming and signal interception through rapid, wideband **Frequency Hopping Per Node (FHSS)**.

---

## 2. Hardware Architecture & Specifications

| Component | Minimum Specification | Primary Function |
| :--- | :--- | :--- |
| **SBC (Brain)** | Raspberry Pi 4 (4GB) / Raspberry Pi 5 | Handles Linux networking (`batman-adv`), encryption, data packet routing, and algorithmic hopping execution. |
| **SDR (RF Engine)** | Pluto+ SDR Transceiver (70MHz - 6GHz) | Converts digital data into physical LoRa RF waveforms and dynamically switches carrier frequencies. |
| **Hardware RTC** | DS3231 TCXO Module (I2C Interface) | Temperature-Compensated Crystal Oscillator providing highly stable, low-drift millisecond-level hardware timekeeping. |
| **Antenna System** | Wideband Tactical Antenna (VHF/UHF/ISM) | Handles multi-band signal transmission and reception without needing manually tuned matching elements. |
| **Power Source** | USB-C Power Bank (PD 20,000mAh+) | Provides standard tactical, mobile operations power for the entire portable backpack assembly. |

---

## 3. Functional Requirements (FR)

### FR-01: Localized Pre-Mission Time Synchronization
*   **Mechanism:** Before deployment, all nodes must be physically interconnected via a local LAN Switch / Hub to a designated Command Laptop.
*   **Execution:** A broadcast script must synchronize all node system clocks (`sudo date -s`) simultaneously. The synced timestamp must instantly be locked onto the hardware `DS3231 RTC` memory storage.
*   **Tolerance:** The starting delta drift between nodes at the time of detachment must not exceed **2 microseconds**.

### FR-02: Dynamic In-Mission Beaconing (Anti-Drift Compensation)
*   **Mechanism:** Because hardware crystals drift over time due to thermal changes, the system must perform dynamic runtime micro-adjustments.
*   **Execution:** Every 10 seconds, the node currently acting as the active mesh leader or equivalent peer must broadcast an encrypted **Time-Beacon** packet. 
*   **Adjustment:** Receiver nodes capture this out-of-band packet, compute the transit delay, and adjust their software micro-clocks to maintain perfect frequency hopping synchronicity.

### FR-03: Ultra-Wideband Frequency Hopping (FHSS) per Node
*   **Hopping Interval:** The system must execute frequency changes every **50 milliseconds** (20 hops per second).
*   **Algorithmic Sequence:** The exact channel sequence must be determined deterministically by running a **Pseudo-Random Number Generator (PRNG)** initialized with the *Pre-Shared Cryptographic Seed Key* and the *Current Precise Local Millisecond Timestamp*.
*   **Spectrum Spread:** The hopping sequence must jump pseudo-randomly across distinct bands (e.g., jumping between 433 MHz UHF, 915 MHz ISM, and 1.2 GHz Amateur bands) to maximize low probability of intercept (LPI).

### FR-04: Stateless Resynchronization (Listening Mode)
*   **Scenario:** If a node power-cycles or loses alignment in the field, it will fail to track the current hopping matrix.
*   **Recovery:** The disconnected node must fallback to a pre-defined static **Guard/Discovery Frequency**. It remains static until it catches a standard encrypted `Time-Beacon` broadcast, instantly loading the master network timer and resuming active hopping.

---

## 4. Software Stack Configuration

```
┌─────────────────────────────────────────────────────────┐
│     User End Devices: Android (ATAK) / Tactical Laptops │
└────────────────────────────┬────────────────────────────┘
                             │ (Local Wi-Fi Hotspot / Ethernet LAN)
┌────────────────────────────▼────────────────────────────┐
│ Linux Networking Layer: batman-adv (Kernel MANET Router)│
└────────────────────────────┬────────────────────────────┘
                             │ (Virtual TUN/TAP Interface Interception)
┌────────────────────────────▼────────────────────────────┐
│ Core Python Orchestrator: PRNG, Sync Engine & I2C Reader│
└────────────────────────────┬────────────────────────────┘
                             │ (Streaming Packets + Dinamic LO Frequency)
┌────────────────────────────▼────────────────────────────┐
│ Signal Processing Layer: GNU Radio Framework & gr-lora   │
└────────────────────────────┬────────────────────────────┘
                             │ (High-Speed LibIIO Driver via Gigabit LAN)
┌────────────────────────────▼────────────────────────────┐
│ Transceiver Hardware: Pluto+ SDR -> Wideband RF Antenna  │
└─────────────────────────────────────────────────────────┘
```

---

## 5. System Execution Blueprint & Setup Guide

### Step 1: Operating System & MANET Kernel Setup
Run the following terminal environment configurations on the Raspberry Pi:
```bash
# Update repositories and install foundational routing elements
sudo apt update
sudo apt install batman-adv batctl chrony i2c-tools gnuradio -y

# Force load the batman-adv module into the system kernel
sudo modprobe batman-adv
```

### Step 2: Activating the Hardware I2C RTC DS3231
1. Connect the DS3231 module to the Raspberry Pi GPIO pins (VCC, GND, SDA, SCL).
2. Open `/boot/firmware/config.txt` (or `/boot/config.txt`) and append:
   ```text
   dtoverlay=i2c-rtc,ds3231
   ```
3. Reboot the Pi and verify hardware detection on the I2C bus:
   ```bash
   sudo i2cdetect -y 1
   ```
4. Read the absolute hardware time directly using:
   ```bash
   sudo hwclock -r
   ```

### Step 3: Network Interception & Routing Architecture
Create a virtual TAP device to intercept native IP traffic and pass it to the Python-SDR pipeline:
```bash
# Initialize a persistent TAP interface for the custom radio engine
sudo ip tuntap add mode tap dev tap-radio
sudo ip link set dev tap-radio up

# Bind the radio interface under the B.A.T.M.A.N. matrix
sudo batctl if add tap-radio
sudo ip link set dev bat0 up

# Assign a designated IP structure to the local mesh interface node
# Example: Node 1: 10.10.0.1/24, Node 2: 10.10.0.2/24, etc.
sudo ip addr add 10.10.0.1/24 dev bat0
```

---

## 6. Software Repositories Deployment Checklist

To execute this architecture completely, clone the following verified open-source framework assets to the workspace:

1. **Physical Layer LoRa SDR Emulation:**
   * URL: `https://github.com/tapparelj/gr-lora_sdr`
   * Purpose: Provides the GNU Radio blocks necessary to process LoRa modulation streams digitally.
2. **SDR Mesh Network Bridging Driver:**
   * URL: `https://github.com/tvelliott/charon`
   * Purpose: Custom structural bridge layer specifically engineered to tunnel `batman-adv` data structures through Pluto SDR RF engines.
3. **Hopping Algorithm Reference Models:**
   * URL: `https://github.com/ExpressLRS/ExpressLRS`
   * Purpose: Open-source reference logic for high-speed, mathematical time-locked FHSS packet transmission.

---

## 7. Operational Validation & Acceptance Criteria

*   **Synchronization Verification:** The node array must sustain active frequency hopping synchronization under operational environments for a minimum window of **12 continuous hours** without manual realignment.
*   **Anti-Jamming Resilience:** Injected single-band RF interference (e.g., continuous blocking signals focused at 915 MHz) must not degrade message delivery success rates by more than **5%**, validated by automated text messaging stress tests.
*   **Ad-Hoc Structural Elasticity (Self-Healing):** When a middle routing node drops mid-transit, remaining nodes must discover an alternate physical path via neighboring assets and restore end-to-end IP traffic routing automatically within **3 seconds**.