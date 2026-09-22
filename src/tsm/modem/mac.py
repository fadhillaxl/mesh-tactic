"""
Tactical Medium Access Control (MAC) Layer for SDR RF & Railway AIS.
Implements:
1. LBT (Listen-Before-Talk) Carrier Sense / Energy Detection (CSMA/CA).
2. Slotted Carrier Sense (Slotted-LBT) aligning bursts to discrete Time Slots.
3. S-TDMA (Self-Organizing Time Division Multiple Access) Foundation compatible with GPS 1-PPS.
"""

import time
import math
import random
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
import numpy as np


class MACMode(str, Enum):
    OFF = "off"              # Uncontrolled transmission (ALOHA without sensing)
    LBT = "lbt"              # Listen-Before-Talk with random backoff (CSMA/CA)
    SLOTTED_LBT = "slotted"  # Slotted LBT (Waits for next time slot boundary + LBT)
    STDMA = "stdma"          # Self-Organizing TDMA with Slot Maps and Reservations


class ChannelStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    UNKNOWN = "unknown"


@dataclass
class SlotReservation:
    slot_id: int
    owner_id: int
    expires_at_frame: int
    last_heard: float = field(default_factory=time.time)


class ChannelEnergyDetector:
    """
    Senses RF energy in baseband I/Q samples to determine channel availability.
    Maintains an adaptive rolling noise floor baseline.
    """

    def __init__(
        self,
        base_threshold_rms: float = 800.0,
        noise_adaptation_rate: float = 0.05,
        busy_snr_factor: float = 2.5,
    ):
        self.noise_floor_rms = base_threshold_rms
        self.adaptation_rate = noise_adaptation_rate
        self.busy_snr_factor = busy_snr_factor
        self.last_energy_rms = 0.0
        self.lock = threading.Lock()

    def compute_rms(self, raw_samples: np.ndarray) -> float:
        """Calculates RMS power of complex baseband int16 samples [[I, Q], ...]."""
        if raw_samples is None or len(raw_samples) == 0:
            return 0.0

        i = raw_samples[:, 0].astype(np.float32)
        q = raw_samples[:, 1].astype(np.float32)
        mean_power = np.mean(i**2 + q**2)
        rms = float(np.sqrt(mean_power))
        return rms

    def update_and_check(self, raw_samples: np.ndarray) -> Tuple[ChannelStatus, float]:
        """
        Updates noise floor baseline and returns (ChannelStatus, current_rms).
        Channel is BUSY if current_rms > (noise_floor * busy_snr_factor) or base threshold.
        """
        rms = self.compute_rms(raw_samples)
        with self.lock:
            self.last_energy_rms = rms
            # Threshold: higher of 800 RMS or (2.5 * ambient noise floor)
            busy_threshold = max(800.0, self.noise_floor_rms * self.busy_snr_factor)

            if rms > busy_threshold:
                status = ChannelStatus.BUSY
            else:
                status = ChannelStatus.IDLE
                # Adapt noise floor slowly only during quiet intervals
                self.noise_floor_rms = (
                    (1.0 - self.adaptation_rate) * self.noise_floor_rms
                    + self.adaptation_rate * rms
                )

        return status, rms


class SlotScheduler:
    """
    Manages discrete Time Slots for Slotted-LBT and S-TDMA.
    Default: 1.0 second frame divided into 20 slots of 50 ms each.
    Transmission takes ~26 ms DMA burst, leaving 24 ms guard time.
    """

    def __init__(
        self,
        frame_duration: float = 1.0,
        slots_per_frame: int = 20,
    ):
        self.frame_duration = frame_duration
        self.slots_per_frame = slots_per_frame
        self.slot_duration = frame_duration / slots_per_frame
        self.slot_map: Dict[int, SlotReservation] = {}
        self.lock = threading.Lock()

    def get_current_frame_and_slot(self, now: Optional[float] = None) -> Tuple[int, int]:
        """Returns (current_frame_index, current_slot_index) based on epoch clock."""
        t = time.time() if now is None else now
        current_frame = int(t / self.frame_duration)
        offset_in_frame = t % self.frame_duration
        current_slot = int(offset_in_frame / self.slot_duration)
        return current_frame, min(current_slot, self.slots_per_frame - 1)

    def time_until_slot_start(self, target_slot: int, now: Optional[float] = None) -> float:
        """Calculates seconds until the exact start of target_slot."""
        t = time.time() if now is None else now
        offset_in_frame = t % self.frame_duration
        current_slot = int(offset_in_frame / self.slot_duration)

        if target_slot > current_slot:
            slot_offset_time = target_slot * self.slot_duration
            return max(0.0, slot_offset_time - offset_in_frame)
        else:
            # Target slot is in the next frame
            time_left_in_frame = self.frame_duration - offset_in_frame
            return time_left_in_frame + (target_slot * self.slot_duration)

    def time_until_next_slot_boundary(self, now: Optional[float] = None) -> float:
        """Returns time to the immediate next slot start."""
        t = time.time() if now is None else now
        offset_in_slot = (t % self.frame_duration) % self.slot_duration
        return max(0.001, self.slot_duration - offset_in_slot)

    def register_remote_reservation(
        self,
        slot_id: int,
        owner_id: int,
        timeout_frames: int = 5,
    ):
        """Records an S-TDMA slot reservation overheard from another node."""
        if slot_id < 0 or slot_id >= self.slots_per_frame:
            return

        current_frame, _ = self.get_current_frame_and_slot()
        with self.lock:
            self.slot_map[slot_id] = SlotReservation(
                slot_id=slot_id,
                owner_id=owner_id,
                expires_at_frame=current_frame + timeout_frames,
                last_heard=time.time(),
            )

    def get_free_slots(self, exclude_my_id: Optional[int] = None) -> List[int]:
        """Returns a list of unoccupied slot indices in the current frame."""
        current_frame, _ = self.get_current_frame_and_slot()
        with self.lock:
            # Purge expired reservations
            expired = [
                s for s, r in self.slot_map.items()
                if r.expires_at_frame < current_frame
            ]
            for s in expired:
                del self.slot_map[s]

            occupied = set(self.slot_map.keys())
            if exclude_my_id is not None:
                # Keep own reservations available for reuse
                occupied = {
                    s for s, r in self.slot_map.items()
                    if r.owner_id != exclude_my_id
                }

            all_slots = set(range(self.slots_per_frame))
            free = sorted(list(all_slots - occupied))
            return free


class TacticalMAC:
    """
    Medium Access Control manager for Tactical SDR and Railway AIS.
    Coordinates Energy Detection (LBT), Slotted Timing, and S-TDMA reservations.
    """

    def __init__(
        self,
        node_id: int,
        mode: MACMode = MACMode.LBT,
        frame_duration: float = 1.0,
        slots_per_frame: int = 20,
        min_backoff_ms: float = 25.0,
        max_backoff_ms: float = 90.0,
        max_retries: int = 4,
    ):
        self.node_id = node_id
        self.mode = mode
        self.detector = ChannelEnergyDetector()
        self.scheduler = SlotScheduler(frame_duration, slots_per_frame)
        self.min_backoff = min_backoff_ms / 1000.0
        self.max_backoff = max_backoff_ms / 1000.0
        self.max_retries = max_retries

        # S-TDMA state
        self.my_reserved_slot: Optional[int] = None
        self.reservation_timeout: int = 0

        # Diagnostics & Metrics
        self.total_tx_attempts = 0
        self.successful_tx = 0
        self.backoffs_count = 0
        self.dropped_busy_count = 0
        self.latest_rx_samples: Optional[np.ndarray] = None
        self.samples_lock = threading.Lock()

    def feed_rx_samples(self, samples: np.ndarray):
        """Called continuously by SDR RX thread to update channel energy state."""
        with self.samples_lock:
            self.latest_rx_samples = samples
        self.detector.update_and_check(samples)

    def _get_current_channel_state(self) -> ChannelStatus:
        """Inspects latest available RX buffer for carrier activity."""
        with self.samples_lock:
            samples = self.latest_rx_samples

        if samples is None:
            return ChannelStatus.IDLE

        status, _ = self.detector.update_and_check(samples)
        return status

    def select_next_stdma_slot(self) -> int:
        """Selects a free slot from the S-TDMA map or keeps current reservation."""
        free_slots = self.scheduler.get_free_slots(exclude_my_id=self.node_id)
        if not free_slots:
            # If map saturated, pick a random slot with uniform distribution
            return random.randint(0, self.scheduler.slots_per_frame - 1)

        # Prefer keeping the existing reserved slot if still available
        if self.my_reserved_slot in free_slots:
            return self.my_reserved_slot

        # Otherwise pick randomly among free slots to minimize collision chance
        selected = random.choice(free_slots)
        self.my_reserved_slot = selected
        self.reservation_timeout = 5  # Hold for 5 frames (~5 seconds)
        return selected

    def acquire_tx_permission(self) -> bool:
        """
        Executes MAC policy before allowing SDR to seize TX buffer.
        Returns True if channel cleared for transmission, False if congested.
        """
        self.total_tx_attempts += 1

        # Case 0: MAC Disabled (Raw ALOHA)
        if self.mode == MACMode.OFF:
            self.successful_tx += 1
            return True

        # Case 1: Standard Listen-Before-Talk (LBT / CSMA-CA)
        if self.mode == MACMode.LBT:
            for attempt in range(self.max_retries + 1):
                state = self._get_current_channel_state()
                if state == ChannelStatus.IDLE:
                    self.successful_tx += 1
                    return True

                # Channel is BUSY -> Random exponential backoff with jitter
                self.backoffs_count += 1
                backoff = random.uniform(
                    self.min_backoff * (1.5**attempt),
                    self.max_backoff * (1.5**attempt),
                )
                time.sleep(backoff)

            self.dropped_busy_count += 1
            return False

        # Case 2: Slotted-LBT (Waits for next slot boundary, then checks LBT)
        if self.mode == MACMode.SLOTTED_LBT:
            for attempt in range(self.max_retries + 1):
                # Align to next 50ms slot start
                wait_time = self.scheduler.time_until_next_slot_boundary()
                time.sleep(wait_time)

                # Quick energy check during slot guard interval
                state = self._get_current_channel_state()
                if state == ChannelStatus.IDLE:
                    self.successful_tx += 1
                    return True

                self.backoffs_count += 1
                # Wait 1-3 slots before retrying
                time.sleep(self.scheduler.slot_duration * random.randint(1, 3))

            self.dropped_busy_count += 1
            return False

        # Case 3: S-TDMA (Self-Organizing Time Division Multiple Access)
        if self.mode == MACMode.STDMA:
            target_slot = self.select_next_stdma_slot()
            wait_time = self.scheduler.time_until_slot_start(target_slot)

            if wait_time > 0.002:
                time.sleep(wait_time)

            # LBT sanity check: verify no unsynchronized station is transmitting in our slot
            state = self._get_current_channel_state()
            if state == ChannelStatus.IDLE:
                self.successful_tx += 1
                return True
            else:
                # Collision in reserved slot! Back off and pick another slot next frame
                self.backoffs_count += 1
                self.my_reserved_slot = None
                return False

        return True

    def get_status_summary(self) -> str:
        """Returns formatted status for CLI and diagnostics."""
        cur_frame, cur_slot = self.scheduler.get_current_frame_and_slot()
        free_count = len(self.scheduler.get_free_slots())
        total_slots = self.scheduler.slots_per_frame

        return (
            f"Mode: {self.mode.value.upper()} | "
            f"Frame #{cur_frame} Slot #{cur_slot:02d}/{total_slots} | "
            f"Free Slots: {free_count}/{total_slots} | "
            f"Noise Floor RMS: {self.detector.noise_floor_rms:.1f} | "
            f"Backoffs: {self.backoffs_count} | "
            f"Tx Attempts: {self.successful_tx}/{self.total_tx_attempts}"
        )
