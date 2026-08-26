"""The worker's pacing pieces, unit-tested.

These are pure (an AIMD window derivation and an across-row state
machine) and were reachable only through a management command until
the runner moved into lists/operations. They decide real spend, so
they get real tests.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_fill_pacing
"""

from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from openbower_kernel.adaptive import CLIMB_STREAK, AdaptiveConcurrency

from ..constants import CONSECUTIVE_TRANSIENT_LIMIT, FREE_SEARCH_FAILURE_BREAK, FillFailureCode
from ..operations.fill_worker import _Breakers


def failed_search():
    return SimpleNamespace(failed=True)


def clean_search():
    return SimpleNamespace(failed=False)


class SearchBreakerTests(SimpleTestCase):
    """A failing FREE door is a rate signal first and a stop signal
    only after backing off runs out of room."""

    def test_failures_above_the_floor_do_not_trip(self):
        # There is still width to give up, so the answer is to halve
        # the point, not to kill the fill.
        breakers = _Breakers(free_search_door=True)
        for _ in range(FREE_SEARCH_FAILURE_BREAK * 3):
            breakers.row_finished(transient=False, searches=[failed_search()], at_floor=False)
        self.assertIsNone(breakers.tripped)

    def test_failures_at_the_floor_trip(self):
        # One row in flight and the door is still refusing: this is a
        # ban, not congestion.
        breakers = _Breakers(free_search_door=True)
        for _ in range(FREE_SEARCH_FAILURE_BREAK):
            breakers.row_finished(transient=False, searches=[failed_search()], at_floor=True)
        self.assertEqual(breakers.tripped[0], FillFailureCode.SEARCH_THROTTLED)

    def test_one_clean_search_forfeits_the_streak(self):
        breakers = _Breakers(free_search_door=True)
        for _ in range(FREE_SEARCH_FAILURE_BREAK - 1):
            breakers.row_finished(transient=False, searches=[failed_search()], at_floor=True)
        breakers.row_finished(transient=False, searches=[clean_search()], at_floor=True)
        breakers.row_finished(transient=False, searches=[failed_search()], at_floor=True)
        self.assertIsNone(breakers.tripped)

    def test_a_paid_door_never_arms_this_breaker(self):
        # The remedy in the copy is "connect DataForSEO", which is
        # nonsense told to someone who already has it.
        breakers = _Breakers(free_search_door=False)
        for _ in range(FREE_SEARCH_FAILURE_BREAK * 2):
            breakers.row_finished(transient=False, searches=[failed_search()], at_floor=True)
        self.assertIsNone(breakers.tripped)


class TransientBreakerTests(SimpleTestCase):
    """The provider breaker is NOT floor-gated: a dead provider is
    dead at every width, and its rows already halve the point through
    the transient path."""

    def test_consecutive_transients_trip_at_any_width(self):
        breakers = _Breakers(free_search_door=False)
        for _ in range(CONSECUTIVE_TRANSIENT_LIMIT):
            breakers.row_finished(transient=True, searches=[], at_floor=False)
        self.assertEqual(breakers.tripped[0], FillFailureCode.PROVIDER_THROTTLED)

    def test_one_good_row_forfeits_the_streak(self):
        breakers = _Breakers(free_search_door=False)
        for _ in range(CONSECUTIVE_TRANSIENT_LIMIT - 1):
            breakers.row_finished(transient=True, searches=[], at_floor=False)
        breakers.row_finished(transient=False, searches=[], at_floor=False)
        breakers.row_finished(transient=True, searches=[], at_floor=False)
        self.assertIsNone(breakers.tripped)

    def test_the_first_trip_wins(self):
        # The failed fill's error must name the breaker that actually
        # stopped the spend.
        breakers = _Breakers(free_search_door=True)
        for _ in range(CONSECUTIVE_TRANSIENT_LIMIT):
            breakers.row_finished(transient=True, searches=[], at_floor=True)
        self.assertEqual(breakers.tripped[0], FillFailureCode.PROVIDER_THROTTLED)
        for _ in range(FREE_SEARCH_FAILURE_BREAK):
            breakers.row_finished(transient=False, searches=[failed_search()], at_floor=True)
        self.assertEqual(breakers.tripped[0], FillFailureCode.PROVIDER_THROTTLED)


class ThrottleAsRateSignalTests(SimpleTestCase):
    """The arithmetic the runner now performs on a failed search: a
    throttle HALVES the point, where counting it clean would have
    climbed it into the ban."""

    def test_a_throttle_halves_and_a_clean_streak_climbs(self):
        controller = AdaptiveConcurrency(start=16, ceiling=32)
        controller.record_throttle(controller.generation())
        self.assertEqual(controller.current(), 8)
        for _ in range(CLIMB_STREAK):
            controller.record_success(controller.generation())
        self.assertEqual(controller.current(), 9)

    def test_repeated_throttles_reach_the_floor_and_stop(self):
        # Where the breaker's at_floor gate becomes true: backing off
        # has nothing left to give.
        controller = AdaptiveConcurrency(start=32, ceiling=32)
        for _ in range(10):
            controller.record_throttle(controller.generation())
        self.assertEqual(controller.current(), 1)
