"""
Unit tests for Tactical MAC (LBT, Slotted-LBT, and S-TDMA).
"""

import sys
import time
from pathlib import Path
import unittest
import numpy as np

# Add src/ to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tsm.modem.mac import (
    MACMode,
    ChannelStatus,
    ChannelEnergyDetector,
    SlotScheduler,
    TacticalMAC,
)


class TestTacticalMAC(unittest.TestCase):
    def test_energy_detector_rms_and_threshold(self):
        detector = ChannelEnergyDetector(base_threshold_rms=500.0)

        # 1. Low energy noise samples
        quiet_samples = np.random.normal(0, 100, size=(1000, 2)).astype(np.int16)
        status, rms = detector.update_and_check(quiet_samples)
        self.assertEqual(status, ChannelStatus.IDLE)
        self.assertLess(rms, 500.0)

        # 2. High energy carrier burst (e.g. amplitude 5000)
        loud_samples = (np.ones((1000, 2)) * 4000).astype(np.int16)
        status, rms = detector.update_and_check(loud_samples)
        self.assertEqual(status, ChannelStatus.BUSY)
        self.assertGreater(rms, 1000.0)

    def test_slot_scheduler_timing_and_boundaries(self):
        # 1.0 second frame with 20 slots (50ms per slot)
        scheduler = SlotScheduler(frame_duration=1.0, slots_per_frame=20)

        fixed_t = 1000.025  # 25 ms into epoch 1000 -> Slot 0
        frame, slot = scheduler.get_current_frame_and_slot(now=fixed_t)
        self.assertEqual(frame, 1000)
        self.assertEqual(slot, 0)

        # Time until next slot boundary (from 25ms to 50ms = 25ms)
        dt = scheduler.time_until_next_slot_boundary(now=fixed_t)
        self.assertAlmostEqual(dt, 0.025, places=3)

        # Time until Slot 5 (at 250ms -> 250ms - 25ms = 225ms)
        dt_slot5 = scheduler.time_until_slot_start(5, now=fixed_t)
        self.assertAlmostEqual(dt_slot5, 0.225, places=3)

        # Target slot in next frame: Slot 0 when at Slot 10 (525ms into frame)
        fixed_t2 = 1000.525
        dt_next_slot0 = scheduler.time_until_slot_start(0, now=fixed_t2)
        # 1.0 - 0.525 = 0.475s
        self.assertAlmostEqual(dt_next_slot0, 0.475, places=3)

    def test_slot_map_reservations_and_free_slots(self):
        scheduler = SlotScheduler(frame_duration=1.0, slots_per_frame=10)

        # Initially all 10 slots free
        free_slots = scheduler.get_free_slots()
        self.assertEqual(len(free_slots), 10)

        # Node 0x0002 reserves Slot 3 for 2 frames
        scheduler.register_remote_reservation(slot_id=3, owner_id=0x0002, timeout_frames=2)
        free_slots = scheduler.get_free_slots()
        self.assertEqual(len(free_slots), 9)
        self.assertNotIn(3, free_slots)

        # Exclude own ID keeps slot available for self
        free_for_me = scheduler.get_free_slots(exclude_my_id=0x0002)
        self.assertIn(3, free_for_me)

    def test_lbt_permission_idle_channel(self):
        mac = TacticalMAC(node_id=0x0001, mode=MACMode.LBT)
        quiet_samples = np.random.normal(0, 100, size=(1000, 2)).astype(np.int16)
        mac.feed_rx_samples(quiet_samples)

        # Should acquire permission immediately
        permitted = mac.acquire_tx_permission()
        self.assertTrue(permitted)
        self.assertEqual(mac.backoffs_count, 0)
        self.assertEqual(mac.successful_tx, 1)

    def test_lbt_permission_busy_channel_backoff(self):
        mac = TacticalMAC(
            node_id=0x0001,
            mode=MACMode.LBT,
            min_backoff_ms=5.0,
            max_backoff_ms=10.0,
            max_retries=2,
        )
        loud_samples = (np.ones((1000, 2)) * 6000).astype(np.int16)
        mac.feed_rx_samples(loud_samples)

        # Channel continuously busy -> should backoff and return False after retries
        permitted = mac.acquire_tx_permission()
        self.assertFalse(permitted)
        self.assertGreaterEqual(mac.backoffs_count, 2)
        self.assertEqual(mac.dropped_busy_count, 1)

    def test_stdma_slot_selection(self):
        mac = TacticalMAC(node_id=0x0003, mode=MACMode.STDMA, slots_per_frame=20)
        slot = mac.select_next_stdma_slot()
        self.assertGreaterEqual(slot, 0)
        self.assertLess(slot, 20)
        self.assertEqual(mac.my_reserved_slot, slot)

        # Summary string check
        summary = mac.get_status_summary()
        self.assertIn("STDMA", summary)
        self.assertIn("Slot #", summary)


if __name__ == "__main__":
    unittest.main()
