"""The worker's pacing pieces, unit-tested.

These are pure (an AIMD window derivation and an across-row state
machine) and were reachable only through a management command until
the runner moved into lists/operations. They decide real spend, so
they get real tests.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_fill_pacing
"""

from __future__ import annotations

from django.test import SimpleTestCase

from openbower_kernel.adaptive import CLIMB_STREAK, ConcurrencyController

from ..constants import CONSECUTIVE_TRANSIENT_LIMIT, FillFailureCode, StoredCellState
from ..operations.fill_worker import _Breakers

WEB_LIMITED = {"web_search": "rate_limited"}


class TransientBreakerTests(SimpleTestCase):
    """The one breaker: consecutive rows parked for retry, whichever
    provider parked them. NOT floor-gated: a dead provider is dead at every
    width, and its rows already halve the point through the park
    path."""

    def test_consecutive_transients_trip_at_any_width(self):
        breakers = _Breakers()
        for _ in range(CONSECUTIVE_TRANSIENT_LIMIT):
            breakers.row_finished(retry_cause=StoredCellState.TRANSIENT, tools={})
        self.assertEqual(breakers.tripped[0], FillFailureCode.PROVIDER_THROTTLED)

    def test_the_blamed_tool_outranks_the_status_walk(self):
        # The status map alone cannot show which tool SERVED before it
        # closed, so the walk alone can name the wrong culprit (a tool
        # that served and then throttled sits first in registration
        # order). The run's own blame verdict wins: FAILS if the
        # breaker falls back to the walk despite a named tool.
        breakers = _Breakers()
        both_closed = {"web_search": "rate_limited", "find_contacts": "rate_limited"}
        for _ in range(CONSECUTIVE_TRANSIENT_LIMIT):
            breakers.row_finished(
                retry_cause=StoredCellState.TOOL_UNAVAILABLE, tools=both_closed, blamed_tool="find_contacts"
            )
        self.assertIn("Finding contacts", breakers.tripped[1])
        self.assertNotIn("Web search", breakers.tripped[1])

    def test_a_record_without_a_blame_falls_back_to_the_walk(self):
        # Stored before the field: "" resolves nothing, registration
        # order decides, and the copy still names a real closed tool.
        breakers = _Breakers()
        for _ in range(CONSECUTIVE_TRANSIENT_LIMIT):
            breakers.row_finished(
                retry_cause=StoredCellState.TOOL_UNAVAILABLE, tools={"web_search": "rate_limited"}, blamed_tool=""
            )
        self.assertIn("Web search", breakers.tripped[1])

    def test_one_good_row_forfeits_the_streak(self):
        breakers = _Breakers()
        for _ in range(CONSECUTIVE_TRANSIENT_LIMIT - 1):
            breakers.row_finished(retry_cause=StoredCellState.TRANSIENT, tools={})
        breakers.row_finished(retry_cause="", tools={})
        breakers.row_finished(retry_cause=StoredCellState.TRANSIENT, tools={})
        self.assertIsNone(breakers.tripped)

    def test_the_first_trip_wins(self):
        # The failed fill's error must name the breaker that actually
        # stopped the spend.
        breakers = _Breakers()
        for _ in range(CONSECUTIVE_TRANSIENT_LIMIT):
            breakers.row_finished(retry_cause=StoredCellState.TRANSIENT, tools={})
        self.assertEqual(breakers.tripped[0], FillFailureCode.PROVIDER_THROTTLED)
        for _ in range(CONSECUTIVE_TRANSIENT_LIMIT):
            breakers.row_finished(retry_cause=StoredCellState.TOOL_UNAVAILABLE, tools=WEB_LIMITED)
        self.assertEqual(breakers.tripped[0], FillFailureCode.PROVIDER_THROTTLED)

    def test_a_tool_streak_names_the_tool_its_status_and_the_provider(self):
        # Tier-1 copy: the tool the user toggled, what its provider said,
        # which provider, and the remedy only where one exists (the paid
        # provider is not a remedy for someone already on it).
        cases = [
            (WEB_LIMITED, "duckduckgo", "Web search is being rate-limited by the free search provider", True),
            (WEB_LIMITED, "dataforseo", "Web search is being rate-limited by DataForSEO", False),
            ({"web_search": "unreachable"}, "duckduckgo", "Web search cannot reach the free search provider", True),
            ({"web_search": "error"}, "duckduckgo", "Web search is failing on the free search provider", True),
            (
                {"find_contacts": "rate_limited"},
                "duckduckgo",
                "Finding contacts is being rate-limited by DataForSEO",
                False,
            ),
            # Both tools closed: the first toggled tool names the fill.
            (
                {"web_search": "unreachable", "find_contacts": "rate_limited"},
                "duckduckgo",
                "Web search cannot reach",
                True,
            ),
        ]
        for tools, provider, opening, remedy in cases:
            with self.subTest(tools=tools, provider=provider):
                # The copy reads settings at FAILURE time (each spec's
                # failure_copy), so the vendor rides the settings
                # override, not a constructor argument.
                self.enterContext(self.settings(TOOL_WIRING={"web_search": provider}))
                breakers = _Breakers()
                for _ in range(CONSECUTIVE_TRANSIENT_LIMIT):
                    breakers.row_finished(retry_cause=StoredCellState.TOOL_UNAVAILABLE, tools=tools)
                code, message = breakers.tripped
                self.assertEqual(code, FillFailureCode.SEARCH_THROTTLED)
                self.assertTrue(message.startswith(opening), message)
                self.assertEqual("Switch search to a metered vendor" in message, remedy, message)

    def test_a_mixed_streak_reports_its_latest_evidence(self):
        breakers = _Breakers()
        for _ in range(CONSECUTIVE_TRANSIENT_LIMIT - 1):
            breakers.row_finished(retry_cause=StoredCellState.TRANSIENT, tools={})
        breakers.row_finished(retry_cause=StoredCellState.TOOL_UNAVAILABLE, tools=WEB_LIMITED)
        self.assertEqual(breakers.tripped[0], FillFailureCode.SEARCH_THROTTLED)


class ThrottleAsRateSignalTests(SimpleTestCase):
    """The arithmetic the runner performs on a failed search: a
    throttle HALVES the point, where counting it clean would have
    climbed it into the ban."""

    def test_a_throttle_halves_and_a_clean_streak_climbs(self):
        controller = ConcurrencyController(start=16, ceiling=32)
        controller.record_throttle(controller.generation())
        self.assertEqual(controller.current(), 8)
        for _ in range(CLIMB_STREAK):
            controller.record_success(controller.generation())
        self.assertEqual(controller.current(), 9)

    def test_repeated_throttles_reach_the_floor_and_stop(self):
        # Backing off has nothing left to give past this; the breaker
        # is what notices a provider that never reopens.
        controller = ConcurrencyController(start=32, ceiling=32)
        for _ in range(10):
            controller.record_throttle(controller.generation())
        self.assertEqual(controller.current(), 1)
