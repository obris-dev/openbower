"""The AIMD fill-concurrency controller (openbower_kernel): additive
climb on clean rounds, halving on throttles, floor 1, ceiling held,
safe under concurrent reporting.

Run: DJANGO_ENV=test uv run python manage.py test common
"""

from __future__ import annotations

import threading

from django.test import SimpleTestCase

from openbower_kernel.adaptive import CLIMB_STREAK, AdaptiveConcurrency, ConcurrencyBoundsError


class ConstructionTests(SimpleTestCase):
    def test_bounds_outside_the_window_refuse_loudly(self):
        with self.assertRaises(ConcurrencyBoundsError):
            AdaptiveConcurrency(start=0, ceiling=8)
        with self.assertRaises(ConcurrencyBoundsError):
            AdaptiveConcurrency(start=9, ceiling=8)
        with self.assertRaises(ConcurrencyBoundsError):
            AdaptiveConcurrency(start=1, ceiling=0)

    def test_the_whole_window_is_constructible(self):
        self.assertEqual(AdaptiveConcurrency(start=1, ceiling=1).current(), 1)
        self.assertEqual(AdaptiveConcurrency(start=16, ceiling=16).current(), 16)


class ClimbTests(SimpleTestCase):
    def test_a_constant_clean_streak_earns_one_slot(self):
        # CLIMB_STREAK cleans per slot, a CONSTANT step (the per-width
        # round cost made high ceilings unreachable: the triangular
        # sum of every width). The raise resets the streak.
        ctrl = AdaptiveConcurrency(start=4, ceiling=16)
        for _ in range(CLIMB_STREAK - 1):
            ctrl.record_success(ctrl.generation())
        self.assertEqual(ctrl.current(), 4)
        ctrl.record_success(ctrl.generation())
        self.assertEqual(ctrl.current(), 5)
        for _ in range(CLIMB_STREAK - 1):
            ctrl.record_success(ctrl.generation())
        self.assertEqual(ctrl.current(), 5)
        ctrl.record_success(ctrl.generation())
        self.assertEqual(ctrl.current(), 6)

    def test_the_ceiling_holds_under_any_streak(self):
        ctrl = AdaptiveConcurrency(start=4, ceiling=5)
        for _ in range(CLIMB_STREAK):
            ctrl.record_success(ctrl.generation())
        self.assertEqual(ctrl.current(), 5)
        for _ in range(64):
            ctrl.record_success(ctrl.generation())
        self.assertEqual(ctrl.current(), 5)


class ThrottleTests(SimpleTestCase):
    def test_a_throttle_halves_and_the_floor_holds(self):
        ctrl = AdaptiveConcurrency(start=8, ceiling=16)
        ctrl.record_throttle(ctrl.generation())
        self.assertEqual(ctrl.current(), 4)
        ctrl.record_throttle(ctrl.generation())
        self.assertEqual(ctrl.current(), 2)
        ctrl.record_throttle(ctrl.generation())
        self.assertEqual(ctrl.current(), 1)
        ctrl.record_throttle(ctrl.generation())
        self.assertEqual(ctrl.current(), 1)

    def test_a_throttle_forfeits_the_streak(self):
        ctrl = AdaptiveConcurrency(start=4, ceiling=16)
        for _ in range(3):
            ctrl.record_success(ctrl.generation())
        ctrl.record_throttle(ctrl.generation())
        self.assertEqual(ctrl.current(), 2)
        # Pre-throttle successes never count toward the next raise: a
        # fresh full streak is what earns width three.
        for _ in range(CLIMB_STREAK - 1):
            ctrl.record_success(ctrl.generation())
        self.assertEqual(ctrl.current(), 2)
        ctrl.record_success(ctrl.generation())
        self.assertEqual(ctrl.current(), 3)

    def test_mixed_traffic_settles_within_the_window(self):
        ctrl = AdaptiveConcurrency(start=4, ceiling=8)
        ctrl.record_success(ctrl.generation())
        ctrl.record_throttle(ctrl.generation())
        self.assertEqual(ctrl.current(), 2)
        for _ in range(CLIMB_STREAK):
            ctrl.record_success(ctrl.generation())
        self.assertEqual(ctrl.current(), 3)
        ctrl.record_throttle(ctrl.generation())
        self.assertEqual(ctrl.current(), 1)


class CongestionEpochTests(SimpleTestCase):
    """One provider event sheds ONE width. The worker runs current()
    rows at a time, so a burst refuses all of them at once; without an
    epoch each refusal halved again and a single event took the point
    to the floor."""

    def test_one_burst_halves_once_however_many_rows_report_it(self):
        c = AdaptiveConcurrency(start=64, ceiling=64)
        generation = c.generation()
        # Every row in flight refuses, as a real burst does.
        for _ in range(64):
            c.record_throttle(generation)
        self.assertEqual(c.current(), 32)

    def test_a_later_event_sheds_again(self):
        # The epoch must not freeze the controller: a burst against the
        # NEW width is a new event and sheds from it.
        c = AdaptiveConcurrency(start=64, ceiling=64)
        # Each batch captures its epoch ONCE, at row start, the way the
        # worker does; reading it per report would defeat the point.
        first = c.generation()
        for _ in range(64):
            c.record_throttle(first)
        self.assertEqual(c.current(), 32)
        second = c.generation()
        for _ in range(32):
            c.record_throttle(second)
        self.assertEqual(c.current(), 16)

    def test_a_stale_throttle_still_forfeits_the_streak(self):
        # It is a refusal, not evidence of headroom: it must not leave
        # a half-built climb standing.
        c = AdaptiveConcurrency(start=8, ceiling=64)
        stale = c.generation()
        c.record_throttle(stale)
        for _ in range(CLIMB_STREAK - 1):
            c.record_success(c.generation())
        c.record_throttle(stale)
        c.record_success(c.generation())
        self.assertEqual(c.current(), 4)

    def test_a_stale_success_does_not_climb_back(self):
        # Rows that started at the refused width finish clean; counting
        # them would re-earn the slot on pre-backoff evidence.
        c = AdaptiveConcurrency(start=8, ceiling=64)
        stale = c.generation()
        c.record_throttle(stale)
        for _ in range(CLIMB_STREAK * 2):
            c.record_success(stale)
        self.assertEqual(c.current(), 4)


class ConcurrentEpochTests(SimpleTestCase):
    def test_a_burst_reported_by_64_THREADS_sheds_exactly_once(self):
        # The serial loop above proves the arithmetic; this proves the
        # LOCK. Sixty-four rows report the same congestion event at
        # once, which is what a real burst looks like, and the point
        # must land on exactly half. The invariant the older thread
        # test asserts (1 <= point <= ceiling) is guaranteed by max()
        # and min() with or without a lock, so it could not have caught
        # a torn read-modify-write here.
        controller = AdaptiveConcurrency(start=64, ceiling=64)
        generation = controller.generation()
        start = threading.Barrier(64)

        def refuse() -> None:
            start.wait()
            controller.record_throttle(generation)

        threads = [threading.Thread(target=refuse) for _ in range(64)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(controller.current(), 32)
        # And the epoch advanced exactly once, however many reported.
        self.assertEqual(controller.generation(), generation + 1)


class ThreadSafetyTests(SimpleTestCase):
    def test_concurrent_reports_hold_the_invariant(self):
        ceiling = 8
        ctrl = AdaptiveConcurrency(start=4, ceiling=ceiling)
        observed: list[int] = []
        observed_lock = threading.Lock()

        def report(throttle_every: int) -> None:
            for i in range(512):
                if throttle_every and i % throttle_every == 0:
                    ctrl.record_throttle(ctrl.generation())
                else:
                    ctrl.record_success(ctrl.generation())
                point = ctrl.current()
                with observed_lock:
                    observed.append(point)

        threads = [threading.Thread(target=report, args=(cadence,)) for cadence in (0, 0, 7, 13)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(observed), 4 * 512)
        self.assertTrue(all(1 <= point <= ceiling for point in observed))
        self.assertTrue(1 <= ctrl.current() <= ceiling)
