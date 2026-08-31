"""Per-fill AIMD concurrency: the operating point for a fill's
concurrent cell runs, tuned by the live throttle signal. A static
width either wastes a hosted vendor's headroom or overruns a
one-parallel local server, and only the 429s and completion timeouts
a fill actually sees know which world it is in. CLIMB_STREAK
consecutive clean completions earn one more slot; a throttle halves
the point, floor 1, once per congestion epoch. Pure math behind a
lock: worker threads report results concurrently, and the controller
holds no threads of its own. The point caps a fill's SHARE of the
worker's pool, which is sized against MAX_FILL_CONCURRENCY."""

from __future__ import annotations

import threading


class ConcurrencyBoundsError(Exception):
    """start and ceiling do not describe a usable window; refusing at
    construction beats a controller that silently reshapes a typo."""


# Consecutive clean completions that earn one more slot (binary).
CLIMB_STREAK = 8


class ConcurrencyController:
    """The AIMD state machine, isolated: an operating point in
    [1, ceiling], adjusted on every reported completion. Thread-safe;
    every method takes the one lock, so reports from concurrent
    workers interleave without tearing the streak arithmetic."""

    def __init__(self, start: int, ceiling: int) -> None:
        if not 1 <= start <= ceiling:
            raise ConcurrencyBoundsError(f"need 1 <= start <= ceiling, got start={start} ceiling={ceiling}")
        self._ceiling = ceiling
        self._point = start
        self._streak = 0
        # The congestion EPOCH. Every shed bumps it, and a report
        # stamped with an older one is evidence about a width that has
        # already been given up.
        self._generation = 0
        self._lock = threading.Lock()

    def current(self) -> int:
        """The operating point, held within [1, ceiling] by every
        transition."""
        with self._lock:
            return self._point

    def generation(self) -> int:
        """The congestion epoch a row is starting in. A caller captures
        this BEFORE the work and hands it back with the result, so the
        controller can tell one provider event from many.

        REQUIRED on every report, deliberately. An optional stamp would
        default to the pre-epoch behaviour, which is the bug: a caller
        that forgot it would silently collapse the point to the floor
        on one burst, and nothing would say so."""
        with self._lock:
            return self._generation

    def record_success(self, generation: int) -> None:
        """Count one clean completion toward the streak; CLIMB_STREAK
        consecutive cleans raise the point by one slot. The step cost
        is CONSTANT, not per-width: a cost that scaled with the
        current width would need the triangular sum of every width to
        cross a wide range, putting a high ceiling out of reach. The
        throttle side stays multiplicative, so caution keeps its
        asymmetry."""
        with self._lock:
            if generation < self._generation:
                # Clean, but it started at a width the provider has
                # since refused. Counting it would climb back on
                # evidence gathered before the backoff.
                return
            self._streak += 1
            if self._streak >= CLIMB_STREAK:
                self._point = min(self._point + 1, self._ceiling)
                self._streak = 0

    def record_throttle(self, generation: int) -> None:
        """Halve the operating point (floor 1) and forfeit the streak.
        A 429 and a completion timeout both mean the provider wants
        less concurrency now; shedding fast and re-earning slowly is
        the asymmetry that settles the point just under the real limit.

        ONCE PER CONGESTION EVENT, not once per report. The caller runs
        `current()` rows at a time and a provider burst refuses all of
        them, so halving per report would shed the whole width on a
        single event rather than halving it, and re-earning a slot
        costs CLIMB_STREAK clean rows.
        A report stamped with an older epoch still forfeits the streak,
        because it is a refusal and not evidence of headroom, but it
        does not shed again for a width already given up.

        THE WINDOW THIS OPENS, stated so the next reader does not have
        to rediscover it. The caller claims `current() - in_flight`
        rows, so after a shed it claims nothing new until the batch
        drains below the new point. Every report until then carries the
        old epoch, which means the controller is deaf to real
        congestion for that stretch: at 32 one refusal sheds to 16 and
        nothing can move it lower until about half the batch finishes,
        bounded by the per-row completion timeout.

        Bounding that window by counting stale throttles does NOT work,
        which is worth writing down. In-flight is capped by the point
        the batch launched under, so the count of stale refusals can
        never exceed that width from one event: a threshold at or below
        it sheds twice for a single burst, and one above it can never
        fire. The real backstop is elsewhere and is a hard one, the
        consecutive-transient breaker, which fails the fill outright
        when a provider keeps refusing."""
        with self._lock:
            self._streak = 0
            if generation < self._generation:
                return
            self._point = max(1, self._point // 2)
            self._generation += 1
