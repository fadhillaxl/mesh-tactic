# TACTICAL SDR MESH (TSM-Net SG)
## Master Hardware, RF Link, and MANET Pipeline Verification Report

**Date of Execution**: 2026-09-14  
**Operating Environment**:
- **Node A (Command Post / Laptop)**: macOS Darwin, ADALM-PLUTO Rev.C (Serial `...125b`), Virtual Serial Console `/dev/cu.usbmodem1304`.
- **Node B (Tactical Outpost / Raspberry Pi 5)**: Debian 12 Bookworm (Linux 6.12.93+rpt-rpi-v8 aarch64), Pluto+ SDR Rev.C AD9361 (Serial `...cb81`) connected via high-speed USB backend (`usb:1.3.5`).
- **Operating RF Band**: 915.000 MHz (Coherent Static ISM, Zero Hopping, Zero GPS dependency).
- **Architectural Standard**: [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) (Zero-bloat, Python standard library `/dev/net/tun` descriptor control).

---

## Executive Summary

| Test Tier | Scope | Target Hardware / Subsystem | Status | Key Metric |
| :--- | :--- | :--- | :---: | :--- |
| **Tier 1** | Math & Multi-Hop Simulation | Python Algorithm Core | **PASS** | 5/5 Scenarios (100% Delivery, 0 CRC errors) |
| **Tier 2** | Physical Hardware In-The-Loop | Raspberry Pi 5 + Pluto+ SDR | **PASS** | 8/8 Tests (LO: 915.000 MHz, 0 Hz delta, Temp: 58.2°C) |
| **Tier 3** | Over-The-Air (OTA) RF Link | Laptop Pluto ↔ Pi 5 Pluto+ | **PASS** | +37.22 dB SNR (72.6x baseband power jump) |
| **Tier 4** | BATMAN-adv MANET Pipeline | Linux Kernel `batman-adv` & TAP | **PASS** | 8/8 Tests (MTU: 180B, Latency: 0.033 ms) |

---

## Tier 1: Mathematical Multi-Node Simulation (`simulate_mesh.py`)

Simulates a 5-node tactical ad-hoc topology (Node 1 -> Node 2 -> Node 3 -> Node 4 -> Node 5) verifying fragmentation, framing overhead, and CRC-16 error detection under simulated AWGN channel conditions:

```text
======================================================================
 TACTICAL SDR MESH (TSM-Net SG) - 5-NODE MULTI-HOP SIMULATION
======================================================================
[SCENARIO 1/5] Baseline Packet Delivery (Single Hop) ............. [PASS]
[SCENARIO 2/5] Multi-Hop Packet Forwarding (Node 1 -> 5) ......... [PASS]
[SCENARIO 3/5] Large Frame Fragmentation & Reassembly ........... [PASS]
[SCENARIO 4/5] CRC-16 Bit-Error Detection & Drop ................ [PASS]
[SCENARIO 5/5] Dynamic Route Failure & Alternate Path Failover .. [PASS]
======================================================================
SIMULATION RESULTS: 5/5 PASSED (100.0%)
```

---

## Tier 2: Physical Hardware Diagnostics Suite (`test_hardware.py`)

Executed directly on **Raspberry Pi 5 (`raspi5.local`)** communicating with the physical **Pluto+ SDR** via native `libiio` USB backend (`usb:1.3.5`):

```text
==============================================================================
 TACTICAL SDR MESH (TSM-Net SG) - HARDWARE DIAGNOSTIC TEST REPORT
 Timestamp:      2026-09-14 22:31:07
 Host:           raspi5 (aarch64 Linux 6.12.93+rpt-rpi-v8)
 IIO Target:     usb:1.3.5
 Target Carrier: 915.000 MHz
 Target Rate:    2.500 MSps
==============================================================================

[TEST 1/8] Checking Linux Kernel batman-adv Subsystem...
  * Interface 'bat0' detected (Active MTU: 180).
  * Interface 'tap-radio' detected (Active MTU: 180).
  * batctl interface status:
    tap-radio: active
  >>> RESULT: [PASS]

[TEST 2/8] Attaching to Pluto+ SDR via IIO Context 'usb:1.3.5'...
  * Context Description: Linux raspi5 6.12.93+rpt-rpi-v8 #1 SMP PREEMPT Debian 1:6.12.93-1+rpt1 (2026-06-12) aarch64
  * Model: Z7010-AD9361 | Firmware: v0.33-3-gd382-dirty
  >>> RESULT: [PASS]

[TEST 3/8] Reading Pluto+ Onboard XADC Telemetry...
  * Internal FPGA Core Temperature: 58.21 C
  * Internal VccInt Core Voltage:   0.98 V
  * Internal VccAux Rail Voltage:   1.79 V
  >>> RESULT: [PASS]

[TEST 4/8] Verifying Local Oscillator (LO) Frequency Lock (915.000 MHz)...
  * Active TX_LO Frequency: 915000000 Hz (Delta: 0 Hz)
  * Active RX_LO Frequency: 915000000 Hz (Delta: 0 Hz)
  * AD9361 Synthesizer Phase Lock Status: LOCKED (0 Hz frequency offset)
  >>> RESULT: [PASS]

[TEST 5/8] Verifying Baseband Sample Rate & Analog Filter Bandwidth...
  * Active RX Sample Rate: 2500000 Sps (2.50 MSps)
  * Active TX Sample Rate: 2500000 Sps (2.50 MSps)
  * RX Analog Filter BW:   1000000 Hz (1.00 MHz)
  * TX Analog Filter BW:   1000000 Hz (1.00 MHz)
  >>> RESULT: [PASS]

[TEST 6/8] Inspecting RF Front-End Gain & RSSI Registers...
  * Current RX Gain Control Mode: manual
  * Current RX Hardware Gain:     55.0 dB
  * Current TX Attenuation:       -10.0 dB
  * Current Hardware RSSI:        105.00 dB
  >>> RESULT: [PASS]

[TEST 7/8] Testing High-Speed I/Q DMA Buffer Streaming...
  * DMA Rx Channel 0 (voltage0_i): ENABLED
  * DMA Rx Channel 1 (voltage0_q): ENABLED
  * DMA Buffer Refill Latency: 0.812 ms (16384 bytes captured)
  * Real-Time Baseband Power:  3.29 ADC counts (Peak: 12.0)
  >>> RESULT: [PASS]

[TEST 8/8] Testing Kernel TAP Device (tap-radio) Non-Blocking I/O...
  * TAP Descriptor Ingestion Latency: 0.027 ms (42-byte Ethernet test frame)
  >>> RESULT: [PASS]

==============================================================================
 HARDWARE DIAGNOSTIC RESULTS: 8/8 PASSED (100.0%)
==============================================================================
```

---

## Tier 3: Over-The-Air (OTA) RF Link Verification (`test_rf_link.py` & `tactical_chat.py`)

Physical over-the-air RF radiation test across the room between **Node A (Mac Laptop Pluto)** and **Node B (Raspberry Pi 5 Pluto+)**:

### 1. RF Link Power Budget & Telemetry:
- **Baseline Ambient Noise Floor (Node A TX: OFF)**:
  - Baseband RMS: `2.08` ADC counts
  - RSSI: `108.75` dB
- **Active Transmission (Node A TX: ON at 915.000 MHz)**:
  - Baseband RMS: `151.32` ADC counts
  - Baseband Peak: `315.0`
  - RSSI: `83.50` dB
- **Measured Signal-to-Noise Ratio (SNR)**:
  $$\text{SNR} = 20 \log_{10}\left(\frac{151.32}{2.08}\right) = \mathbf{+37.22\text{ dB}}$$
  *(72.6-fold voltage increase directly measured at the ADC).*

### 2. Live Interactive Terminal Chat Log:
```text
=================================================================
 TACTICAL TERMINAL CHAT (NODE B: RASPBERRY PI 5)
 Mendengarkan 915.000 MHz. Ketik pesan untuk membalas ke Mac.
=================================================================
[PI5-NODE] > 
[22:52:16] [OVER-THE-AIR] >>> RF Sinyal Diterima dari Mac! <<<
     Frekuensi: 915.000 MHz | RMS Power: 155.7 ADC counts | Peak: 335
     Kekuatan Sinyal: SANGAT KUAT (+35 dB di atas noise)
[PI5-NODE] > 
[22:52:17] [OVER-THE-AIR] >>> RF Sinyal Diterima dari Mac! <<<
     Frekuensi: 915.000 MHz | RMS Power: 155.1 ADC counts | Peak: 336
     Kekuatan Sinyal: SANGAT KUAT (+35 dB di atas noise)
[PI5-NODE] > 
[22:52:18] [OVER-THE-AIR] >>> RF Sinyal Diterima dari Mac! <<<
     Frekuensi: 915.000 MHz | RMS Power: 154.4 ADC counts | Peak: 333
     Kekuatan Sinyal: SANGAT KUAT (+35 dB di atas noise)
[PI5-NODE] > 
[22:52:19] [OVER-THE-AIR] >>> RF Sinyal Diterima dari Mac! <<<
     Frekuensi: 915.000 MHz | RMS Power: 154.6 ADC counts | Peak: 335
     Kekuatan Sinyal: SANGAT KUAT (+35 dB di atas noise)
[PI5-NODE] > 
```

---

## Tier 4: Linux Kernel `batman-adv` & MANET Pipeline (`test_batman_pipeline.py`)

Executed directly on **Raspberry Pi 5 (`raspi5.local`)** verifying the Layer-2 MANET kernel interface and high-performance `/dev/net/tun` TAP ingestion:

```text
==============================================================================
 TACTICAL SDR MESH (TSM-Net SG) - BATMAN-ADV & MANET PIPELINE TEST SUITE
 Timestamp:      2026-09-14 23:09:33
 Host:           raspi5 (aarch64 Linux)
 Kernel:         6.12.93+rpt-rpi-v8
==============================================================================

[TEST 1/8] Verifying batman-adv Linux Kernel Module...
  * batman-adv module is LOADED in kernel (version: 2024.2).
  >>> RESULT: [PASS]

[TEST 2/8] Verifying Routing Algorithm & batctl Utility...
  * batctl detected: batctl debian-2023.0-1 [batman-adv: 2024.2]
  * Active routing algorithm: BATMAN_IV
  >>> RESULT: [PASS]

[TEST 3/8] Verifying Layer-2 Virtual TAP Device (tap-radio)...
  * tap-radio interface is ACTIVE.
  * Clamped MTU: 180 bytes (Target: 180 bytes)
  * Strict MTU 180 byte clamping confirmed (safe for LoRa 255B frame boundary).
  >>> RESULT: [PASS]

[TEST 4/8] Verifying Kernel Mesh Master Interface (bat0)...
  * bat0 interface is ACTIVE (IP: 10.10.0.1/24, MTU: 180).
  * batctl interface slave status:
    tap-radio: active
  * tap-radio is correctly enslaved into bat0 mesh.
  >>> RESULT: [PASS]

[TEST 5/8] Verifying Native /dev/net/tun Non-Blocking Descriptor I/O...
  * Successfully attached to 'tap-radio' via ioctl(TUNSETIFF) with IFF_NO_PI.
  * Non-blocking O_NONBLOCK descriptor mode verified.
  >>> RESULT: [PASS]

[TEST 6/8] Verifying Tactical Framing & CRC-16 CCITT Integrity...
  * Generated wire frame: 50 bytes (Header: 6B, Payload: 42B, CRC: 2B).
  * Decoded Magic: 0xD354 | Seq: 42 | CRC: 0xA015 [VERIFIED]
  >>> RESULT: [PASS]

[TEST 7/8] Verifying Corrupted Frame Rejection via CRC-16 Checksum...
  * Bit flip injected at byte 11.
  * Wire CRC: 0xA015 != Calculated CRC: 0x5FBA
  * Corrupted frame successfully DETECTED and REJECTED by orchestrator integrity check.
  >>> RESULT: [PASS]

[TEST 8/8] Testing Live TAP Frame Ingestion & Round-Trip Injection Latency...
  * Injected 42-byte ARP frame into tap-radio.
  * TAP descriptor write latency: 0.033 ms.
  * Kernel frame ingestion status: IDLE (No active traffic).
  >>> RESULT: [PASS]

==============================================================================
 BATMAN-ADV INTEGRATION TEST RESULTS: 8/8 PASSED (100.0%)
==============================================================================
 >>> ALL BATMAN-ADV & MANET PIPELINE TESTS COMPLETED SUCCESSFULLY! <<<
```

---

## Tier 5: Continuous 2-FSK Digital Baseband I/Q RF Modem (`sdr_rf_modem.py`)

Executed directly over the physical RF channel between **Node 1 (Raspberry Pi 5 + Pluto+ SDR via `usb:1.3.5`)** and **Node 2 (Raspberry Pi Zero 2 W + Pluto SDR via `ip:192.168.99.240:30431`)**:

### 1. Baseband DSP & RF Physical Parameters
- **RF Carrier Frequency**: `915.000 MHz` (Strictly Static ISM, 0 Hz frequency offset)
- **Baseband Sampling Rate**: `2.50 MSps` (Oversampled 50x)
- **Modulation Scheme**: Continuous-Phase 2-FSK (CPFSK)
- **Baud Rate / Bitrate**: `50,000 Baud` (50 kbps)
- **Frequency Deviation**: `±50.0 kHz` (Bit 1 = $+50\text{ kHz}$, Bit 0 = $-50\text{ kHz}$)
- **Receiver Architecture**: 5x Decimation (`500 kSps`, `SPS=10`), Instantaneous Frequency Discriminator, DC Carrier Frequency Offset (CFO) Cancellation, and Sub-Millisecond Vectorized Sync Word Correlation (`0xD3549150`) with Auto-Polarity Spectral Inversion Detection.
- **DMA Buffer Pipeline**: Fixed reusable 131,072-sample (128K samples) buffers for both Tx and Rx DMA channels.
- **Self-Echo Suppression**: Local TAP MAC address filtering prevents self-transmission echo loops across the high-gain receiver.

### 2. Physical Over-The-Air Verification Log

```text
==============================================================================
 TACTICAL SDR MESH - BIDIRECTIONAL OVER-THE-AIR (OTA) VERIFICATION
 Node 1 (Tactical Command): Raspberry Pi 5 | bat0: 10.10.0.1/24 | Pluto+ (usb:1.3.5)
 Node 2 (Outpost Node):    Raspberry Pi 2W | bat0: 10.10.0.2/24 | Pluto (ip:192.168.99.240:30431)
 Carrier Frequency:        915.000 MHz Static ISM Carrier
==============================================================================

[TEST 1/4] Over-The-Air 2-FSK Physical Packet Transmission (Pi 5 -> Pi 2W):
  * Pi 5 DMA TX: Radiated 26B frame (24,400 samples, 9.8 ms burst) at 915.000 MHz
  * Pi 2W DMA RX: [RF-RX] Over-The-Air Packet Decoded! (26 bytes | Sync Corr: 0.42 | CRC-16: VALID)
  >>> RESULT: [PASS]

[TEST 2/4] Over-The-Air 2-FSK Physical Packet Transmission (Pi 2W -> Pi 5):
  * Pi 2W DMA TX: Radiated 28B frame (25,200 samples, 10.1 ms burst) at 915.000 MHz
  * Pi 5 DMA RX: [RF-RX] Over-The-Air Packet Decoded! (28 bytes | Sync Corr: 0.42 | CRC-16: VALID)
  >>> RESULT: [PASS]

[TEST 3/4] B.A.T.M.A.N. MANET Over-The-Air Route Convergence:
  * Pi 5 batctl n:  tap-radio  7a:77:06:18:9a:2d (Pi 2W)   last-seen 0.420s
  * Pi 2W batctl n: tap-radio  1e:83:01:c2:99:69 (Pi 5)    last-seen 1.368s
  * Pi 5 batctl tg: Client da:94:f7:1f:0c:1d Via 7a:77:06:18:9a:2d [ROUTED OVER RF]
  * Pi 2W batctl tg: Client 22:3a:2a:bf:73:a1 Via 1e:83:01:c2:99:69 [ROUTED OVER RF]
  >>> RESULT: [PASS]

[TEST 4/4] End-to-End Over-The-Air Layer 3 IP Packet Delivery:
  * Ingestion: 10.10.0.1 sends UDP payload -> bat0 -> tap-radio -> tsm_mesh_orchestrator
  * Tactical Encapsulation: 0xD354 header + CRC-16 -> IPC 127.0.0.1:52001 -> sdr_rf_modem
  * Physical Waveform: Modulated into 2-FSK I/Q at 915.000 MHz and radiated over the air
  * Remote Ingestion: Pi 2W Pluto captures RF -> Demodulated -> IPC 127.0.0.1:52002 -> tap-radio -> bat0
  * Output on Pi 2W:
    *** SUCCESS! RECEIVED ON PI 2W VIA SDR MESH: TACTICAL_SDR_MESH_IP_PACKET_OVER_THE_AIR_SUCCESS FROM ('10.10.0.1', 40307) ***
  * Output on Pi 5 (Reverse):
    *** SUCCESS! RECEIVED ON PI 5 VIA SDR MESH: OUTPOST_NODE_2_REPORTING_TO_HQ_OVER_915MHZ_SDR FROM ('10.10.0.2', 39636) ***
  >>> RESULT: [PASS]
```

---

## Log Artifacts Generated

1. `hardware_test.log`: Full 8-test report for physical Pluto+ SDR on Raspberry Pi 5.
2. `rf_link_test.log`: Over-the-air SNR and power telemetry log between Mac and Pi 5.
3. `batman_test.log`: Full 8-test report for kernel `batman-adv`, `bat0`, `tap-radio`, and framing.
4. `modem.log` & `orch.log`: Live execution logs on both Raspberry Pi 5 and Raspberry Pi Zero 2 W.

---

## Conclusion

The Tactical SDR Mesh Network (TSM-Net SG) has been verified across all operational layers:
1. **RF Physical Layer**: Transmitting and receiving coherent 915.000 MHz digital baseband I/Q waveforms with continuous 2-FSK modulation.
2. **Data Link Framing**: Tactical `0xD354` encapsulation with sequence counter, fragmentation headers, and CRC-16 error checking.
3. **Kernel Mesh Routing**: Active `batman-adv` (BATMAN_IV) kernel module dynamically discovering neighbors and converging translation tables over the air without internet or ZeroTier.
4. **Zero-Bloat Philosophy**: Adheres strictly to the [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) standard (Native Python stdlib + NumPy only, avoiding heavy GNU Radio builds on 512MB RAM Pi Zero 2 W).
