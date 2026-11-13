"""A log deleted while a poll is landing a page into it, on two real
connections: the delete and the poll take the sheet first and the feed
second, so one waits for the other rather than deadlock.

A TransactionTestCase, since the race is between two transactions: a
TestCase would run both inside one and hide the locks.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_poll_race
"""

from __future__ import annotations

import datetime
import threading
import time
from unittest.mock import patch

from django.db import connection
from django.test import TransactionTestCase

from sheets.models import Sheet
from signals.models import LogFeed
from signals.operations.logs import CreateLogOperation
from signals.operations.poll_logs import PollLogOperation, PollResult
from signals.services.logs import LogService

from .fakes import USER, FakeIdentity, FakeSignals, install, match_body, session_for

_NOW = datetime.datetime(2026, 11, 12, 12, 0, tzinfo=datetime.UTC)
# Long enough that the delete is waiting on its lock before the poll moves
# on; Postgres finds a deadlock after one second.
_HOLD_SECONDS = 2.0


class DeleteDuringPollTests(TransactionTestCase):
    def setUp(self) -> None:
        self.signals = FakeSignals()
        install(self, self.signals, FakeIdentity())
        session = session_for()
        log = CreateLogOperation(session, label="Seed raises", signal="funding", filter={}).run()
        self.feed = LogFeed.objects.get(id=log.local_log.feed.id)
        self.signals.matches[self.feed.listener_id] = [match_body(1, company="Acme")]

    def test_a_delete_waits_for_a_landing_page_rather_than_deadlock(self):
        # FAILS if the delete takes the feed before the sheet: the poll holds
        # the sheet (its append) and wants the feed (its cursor), the delete
        # holds the feed and wants the sheet, and Postgres aborts one.
        appended = threading.Event()
        go_on = threading.Event()
        errors: list[BaseException] = []
        outcome = []
        advance = LogService.advance

        def held_advance(service, feed, *, to_cursor):
            appended.set()
            go_on.wait(timeout=10)
            return advance(service, feed, to_cursor=to_cursor)

        def poll() -> None:
            try:
                fresh = LogFeed.objects.get(id=self.feed.id)
                outcome.append(PollLogOperation(feed=fresh, now=_NOW).run())
            except BaseException as e:
                errors.append(e)
            finally:
                connection.close()

        def delete() -> None:
            try:
                appended.wait(timeout=10)
                logs = LogService(account_id=USER.account_id)
                logs.delete(logs.get(self.feed.sheet_id))
            except BaseException as e:
                errors.append(e)
            finally:
                connection.close()

        with patch.object(LogService, "advance", held_advance):
            poller = threading.Thread(target=poll)
            deleter = threading.Thread(target=delete)
            poller.start()
            deleter.start()
            appended.wait(timeout=10)
            time.sleep(_HOLD_SECONDS)
            go_on.set()
            poller.join(timeout=20)
            deleter.join(timeout=20)

        self.assertEqual(errors, [])
        self.assertEqual([o.result for o in outcome], [PollResult.APPENDED])
        self.assertFalse(Sheet.objects.filter(id=self.feed.sheet_id).exists())
        self.assertFalse(LogFeed.objects.filter(id=self.feed.id).exists())
