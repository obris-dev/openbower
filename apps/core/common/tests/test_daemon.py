"""The kernel daemon loop: work is claimed back to back, and only an empty claim
sleeps.

Run: DJANGO_ENV=test uv run python manage.py test common.tests.test_daemon
"""

from __future__ import annotations

import signal
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management.base import OutputWrapper
from django.db import OperationalError
from django.test import SimpleTestCase

from openbower_kernel.daemon import Daemon


def _daemon(heartbeat: Path) -> Daemon:
    return Daemon(
        heartbeat=heartbeat, idle_seconds=30, stdout=OutputWrapper(StringIO()), stderr=OutputWrapper(StringIO())
    )


class DaemonTests(SimpleTestCase):
    def setUp(self) -> None:
        self.heartbeat = Path(tempfile.mkdtemp()) / "heartbeat"

    def test_work_runs_back_to_back_and_only_an_empty_claim_sleeps(self):
        daemon = _daemon(self.heartbeat)
        turns = iter([True, True, True, False, True, False])
        slept = []

        def step() -> bool:
            did_work = next(turns, None)
            if did_work is None:
                daemon.stopping = True
                return False
            return did_work

        def sleep(seconds: float) -> None:
            slept.append(seconds)

        with patch("openbower_kernel.daemon.time.sleep", side_effect=sleep):
            daemon.run(step, once=False)
        # One sleep per empty claim (the two Falses, and the stopping turn), none after work.
        self.assertEqual(slept, [30, 30, 30])
        self.assertTrue(self.heartbeat.exists())

    def test_once_returns_at_the_first_empty_claim_without_sleeping(self):
        turns = iter([True, True, False, True])
        with patch("openbower_kernel.daemon.time.sleep") as sleep:
            _daemon(self.heartbeat).run(lambda: next(turns), once=True)
        sleep.assert_not_called()
        self.assertEqual(next(turns), True)  # the unit after the empty claim never ran

    def test_a_database_error_is_reported_and_the_loop_goes_on(self):
        daemon = _daemon(self.heartbeat)
        turns = iter([OperationalError("server closed the connection"), True])

        def step() -> bool:
            turn = next(turns, None)
            if turn is None:
                daemon.stopping = True
                return False
            if isinstance(turn, Exception):
                raise turn
            return turn

        with (
            patch("openbower_kernel.daemon.time.sleep") as sleep,
            patch("openbower_kernel.daemon.close_old_connections") as reconnect,
        ):
            daemon.run(step, once=False)
        reconnect.assert_called_once()
        self.assertIn("database error (OperationalError", daemon.stderr._out.getvalue())
        # One idle after the error, none after the work, one on the stopping turn.
        self.assertEqual(sleep.call_count, 2)

    def test_an_unhealthy_pool_withholds_the_heartbeat(self):
        daemon = _daemon(self.heartbeat)
        health = iter([True, False, False, True])
        turns = iter([True, True, True, True])

        def step() -> bool:
            turn = next(turns, None)
            if turn is None:
                daemon.stopping = True
                return False
            return turn

        touched: list[bool] = []

        def touch() -> None:
            touched.append(True)

        with patch("openbower_kernel.daemon.time.sleep"), patch.object(daemon, "_touch", side_effect=touch):
            daemon.run(step, once=False, healthy=lambda: next(health, True))
        self.assertEqual(len(touched), 3)  # five turns, two of them withheld
        self.assertEqual(daemon.stderr._out.getvalue().count("heartbeat withheld"), 1)

    def test_the_signal_handlers_are_restored_and_the_stop_is_said_after_the_loop(self):
        daemon = _daemon(self.heartbeat)
        before = signal.getsignal(signal.SIGTERM)

        def step() -> bool:
            daemon._stop(signal.SIGTERM, None)  # as the signal would, mid-unit
            self.assertEqual(daemon.stdout._out.getvalue(), "")  # the handler writes nothing
            return True

        daemon.run(step, once=False)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)
        self.assertIn("stopped on a signal", daemon.stdout._out.getvalue())

    def test_a_systemic_failure_beside_a_database_error_still_stops_the_worker(self):
        daemon = _daemon(self.heartbeat)
        mixed = ExceptionGroup("units failed this turn", [OperationalError("db down"), ValueError("bad key")])

        def step() -> bool:
            raise mixed

        with (
            patch("openbower_kernel.daemon.time.sleep"),
            patch("openbower_kernel.daemon.close_old_connections"),
            self.assertRaises(ExceptionGroup) as raised,
        ):
            daemon.run(step, once=False)
        self.assertEqual([str(e) for e in raised.exception.exceptions], ["bad key"])
        self.assertIn("database error (OperationalError: db down)", daemon.stderr._out.getvalue())

    def test_a_turn_of_only_database_errors_is_logged_and_the_loop_goes_on(self):
        daemon = _daemon(self.heartbeat)
        turns = iter([ExceptionGroup("units failed this turn", [OperationalError("a"), OperationalError("b")])])

        def step() -> bool:
            turn = next(turns, None)
            if turn is None:
                daemon.stopping = True
                return False
            raise turn

        with (
            patch("openbower_kernel.daemon.time.sleep"),
            patch("openbower_kernel.daemon.close_old_connections") as reconnect,
        ):
            daemon.run(step, once=False)
        reconnect.assert_called_once()
        said = daemon.stderr._out.getvalue()
        self.assertIn("OperationalError: a", said)
        self.assertIn("OperationalError: b", said)
