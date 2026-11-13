"""The log poller against the signals service (a fake at its httpx seam):
a page of matches lands once as rows and moves the cursor; the queue is
served longest waiting first and a claim holds a log for one poller; each
failure leaves the log where the next turn can pick it up.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_poll_logs
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import httpx
from django.core.management import call_command
from django.test import TestCase

from sheets.constants import MAX_SHEET_ROWS
from sheets.models import Sheet
from sheets.services.sheets import SheetService
from signals.constants import (
    LOG_POLL_BACKOFF_SECONDS,
    LOG_POLL_INTERVAL_SECONDS,
    LOG_POLL_LEASE_SECONDS,
    LOG_POLL_PAGE,
    LOG_POLL_PARKED_SECONDS,
    LogStatus,
)
from signals.models import LogFeed, SignalsSyncKey
from signals.operations.log_poller import PollLogsOperation
from signals.operations.logs import CreateLogOperation, DeleteLogOperation
from signals.operations.poll_logs import PollLogOperation, PollResult
from signals.services.logs import LogService
from signals.services.sync_key import SyncKeyService

from .fakes import USER, FakeIdentity, FakeSignals, install, match_body, session_for

_NOW = datetime.datetime(2026, 11, 12, 12, 0, tzinfo=datetime.UTC)


def _seconds(n: int) -> datetime.timedelta:
    return datetime.timedelta(seconds=n)


class _PollTests(TestCase):
    def setUp(self) -> None:
        self.session = session_for()
        self.signals = FakeSignals()
        self.identity = FakeIdentity()
        install(self, self.signals, self.identity)
        self.logs = LogService(account_id=USER.account_id)

    def _log(self, label: str = "Seed raises", *, due: datetime.datetime = _NOW, autofill: bool = True) -> LogFeed:
        log = CreateLogOperation(self.session, label=label, signal="funding", filter={}, autofill=autofill).run()
        LogFeed.objects.filter(id=log.local_log.feed.id).update(next_poll_at=due)
        return LogFeed.objects.get(id=log.local_log.feed.id)

    def _matches(self, feed: LogFeed, companies: list[str], *, start: int = 1) -> None:
        held = self.signals.matches.setdefault(feed.listener_id, [])
        held.extend(match_body(start + n, company=name) for n, name in enumerate(companies))

    def _companies(self, feed: LogFeed) -> list[str]:
        sheet = Sheet.objects.get(id=feed.sheet_id)
        rows = SheetService(account_id=USER.account_id).rows_page(sheet, limit=MAX_SHEET_ROWS)
        return [row.data["company"] for row in rows]

    def _poll(self, feed: LogFeed, now: datetime.datetime = _NOW):
        fresh = LogFeed.objects.get(id=feed.id)
        return PollLogOperation(feed=fresh, now=now).run()


class PollPageTests(_PollTests):
    def test_a_page_lands_as_rows_newest_first_and_moves_the_cursor(self):
        feed = self._log()
        self._matches(feed, ["Acme", "Globex", "Initech"])
        outcome = self._poll(feed)
        self.assertEqual((outcome.result, outcome.rows), (PollResult.APPENDED, 3))
        self.assertEqual(self._companies(feed), ["Initech", "Globex", "Acme"])
        self.assertEqual(LogFeed.objects.get(id=feed.id).cursor, match_body(3, company="x")["id"])
        # A short page: nothing more is waiting, so it rests an interval.
        self.assertEqual(outcome.next_poll_at, _NOW + _seconds(LOG_POLL_INTERVAL_SECONDS))

    def test_the_next_page_starts_after_the_cursor(self):
        feed = self._log()
        self._matches(feed, ["Acme"])
        self._poll(feed)
        self._matches(feed, ["Globex"], start=2)
        later = self._poll(feed)
        self.assertEqual(later.rows, 1)
        self.assertEqual(self._companies(feed), ["Globex", "Acme"])

    def test_a_poll_reads_the_claimed_feed_once(self):
        # FAILS if the poll reads its feed again: the claim hands it back
        # fresh, so only the sheet and the account's key are read before the
        # signals call, and nothing is written when nothing is new.
        feed = self._log()
        claimed = LogFeed.objects.get(id=feed.id)
        with self.assertNumQueries(2):
            outcome = PollLogOperation(feed=claimed, now=_NOW).run()
        self.assertEqual(outcome.result, PollResult.CAUGHT_UP)

    def test_nothing_new_writes_nothing(self):
        feed = self._log()
        self._matches(feed, ["Acme"])
        self._poll(feed)
        again = self._poll(feed)
        self.assertEqual((again.result, again.rows), (PollResult.CAUGHT_UP, 0))
        self.assertEqual(self._companies(feed), ["Acme"])

    def test_a_full_page_is_due_again_at_once(self):
        # FAILS if a backlog rests an interval per page: a log with a
        # thousand matches would take minutes to catch up.
        feed = self._log()
        self._matches(feed, [f"Co{n}" for n in range(LOG_POLL_PAGE + 1)])
        first = self._poll(feed)
        self.assertEqual((first.rows, first.next_poll_at), (LOG_POLL_PAGE, _NOW))
        rest = self._poll(feed)
        self.assertEqual(rest.rows, 1)

    def test_arriving_rows_autofill_as_the_sheet_says(self):
        for autofill in (True, False):
            with self.subTest(autofill=autofill):
                feed = self._log(f"Log {autofill}", autofill=autofill)
                self._matches(feed, ["Acme"])
                with patch("sheets.operations.append_rows.WorkflowReactions", autospec=True) as reactions:
                    self._poll(feed)
                self.assertEqual(reactions.return_value.trigger.called, autofill)

    def test_a_page_lands_once(self):
        # FAILS if the cursor move is not a compare-and-set: a second poller
        # holding the same read (a lease that lapsed under a slow one)
        # would append the page again.
        feed = self._log()
        self._matches(feed, ["Acme", "Globex"])
        rival = []
        self.signals.on_matches = lambda: rival.append(self._poll(feed))
        lost = self._poll(feed)
        self.assertEqual((rival[0].result, lost.result), (PollResult.APPENDED, PollResult.LOST))
        self.assertEqual(self._companies(feed), ["Globex", "Acme"])

    def test_autofill_turned_off_mid_read_applies_to_that_page(self):
        # FAILS if the page lands under the autofill read before the fetch:
        # a person who just turned autofill off would still pay for a page
        # of runs.
        feed = self._log()
        self._matches(feed, ["Acme"])
        self.signals.on_matches = lambda: Sheet.objects.filter(id=feed.sheet_id).update(autofill=False)
        with patch("sheets.operations.append_rows.WorkflowReactions", autospec=True) as reactions:
            landed = self._poll(feed)
        self.assertEqual((landed.result, self._companies(feed)), (PollResult.APPENDED, ["Acme"]))
        self.assertFalse(reactions.return_value.trigger.called)

    def test_a_log_near_its_cap_takes_what_fits(self):
        # FAILS if the page is appended whole: a sheet two rows from its cap
        # holding a three-row page would take more than it has room for.
        feed = self._log()
        self._matches(feed, ["Acme", "Globex", "Initech"])
        Sheet.objects.filter(id=feed.sheet_id).update(row_count=MAX_SHEET_ROWS - 2)
        outcome = self._poll(feed)
        self.assertEqual(outcome.rows, 2)
        self.assertEqual(LogFeed.objects.get(id=feed.id).cursor, match_body(2, company="x")["id"])

    def test_a_sheet_that_refuses_the_page_parks_the_log_with_nothing_moved(self):
        # The sheet's own count is the truth: when it refuses the append
        # (SheetFull), the page rolls back and the log parks as full.
        feed = self._log()
        self._matches(feed, ["Acme", "Globex"])
        with patch("sheets.services.sheets.MAX_SHEET_ROWS", 1):
            outcome = self._poll(feed)
        self.assertEqual(
            (outcome.result, outcome.next_poll_at), (PollResult.FULL, _NOW + _seconds(LOG_POLL_PARKED_SECONDS))
        )
        self.assertEqual((LogFeed.objects.get(id=feed.id).cursor, self._companies(feed)), ("", []))


class PollFailureTests(_PollTests):
    def test_a_refused_key_waits_and_a_reconnect_resumes_from_the_cursor(self):
        # FAILS if the refused key is not marked: every turn would ask the
        # service again with a key it has refused.
        feed = self._log()
        self._matches(feed, ["Acme"])
        self._poll(feed)
        self._matches(feed, ["Globex"], start=2)
        self.signals.next["GET"] = httpx.Response(401, json={"error": "not_authenticated", "detail": "inactive"})
        refused = self._poll(feed)
        self.assertEqual(refused.result, PollResult.WAITING)
        # Parked: nothing can fill it until a person reconnects.
        self.assertEqual(refused.next_poll_at, _NOW + _seconds(LOG_POLL_PARKED_SECONDS))
        self.assertIsNotNone(SignalsSyncKey.objects.get().rejected_at)
        self.signals.calls.clear()
        waiting = self._poll(feed)
        self.assertEqual(waiting.result, PollResult.WAITING)
        self.assertEqual(self.signals.calls, [])
        SyncKeyService(account_id=USER.account_id).reconnect(self.session)
        resumed = self._poll(feed)
        self.assertEqual(resumed.rows, 1)
        self.assertEqual(self._companies(feed), ["Globex", "Acme"])

    def test_a_gone_listener_leaves_the_queue(self):
        feed = self._log()
        self.signals.listeners.clear()
        outcome = self._poll(feed)
        self.assertEqual((outcome.result, outcome.next_poll_at), (PollResult.GONE, None))
        self.assertEqual(self.logs.get(feed.sheet_id).status, LogStatus.LISTENER_GONE)
        self.assertEqual(LogService.Global.due(_NOW + _seconds(3600), limit=10), [])

    def test_a_404_the_listener_does_not_confirm_never_ends_the_log(self):
        # FAILS if any 404 on the matches read is taken as the listener's:
        # gone is final, so one 404 from a gateway would end the log.
        feed = self._log()
        self.signals.next["GET"] = httpx.Response(404, json={"error": "not_found", "detail": "no route"})
        with self.assertLogs("signals.operations.poll_logs", level="ERROR"):
            outcome = self._poll(feed)
        self.assertEqual(outcome.result, PollResult.BACKOFF)
        self.assertIsNone(LogFeed.objects.get(id=feed.id).listener_gone_at)

    def test_an_outage_backs_the_log_off_with_nothing_moved(self):
        feed = self._log()
        self._matches(feed, ["Acme"])
        self.signals.next["GET"] = httpx.Response(503)
        outcome = self._poll(feed)
        self.assertEqual(outcome.result, PollResult.BACKOFF)
        self.assertEqual(outcome.next_poll_at, _NOW + _seconds(LOG_POLL_BACKOFF_SECONDS))
        self.assertEqual((LogFeed.objects.get(id=feed.id).cursor, self._companies(feed)), ("", []))

    def test_a_refused_cursor_is_never_guessed_around(self):
        feed = self._log()
        LogFeed.objects.filter(id=feed.id).update(cursor="01MAT" + "9" * 21)
        with self.assertLogs("signals.operations.poll_logs", level="ERROR"):
            outcome = self._poll(feed)
        self.assertEqual(outcome.result, PollResult.BACKOFF)
        self.assertEqual(LogFeed.objects.get(id=feed.id).cursor, "01MAT" + "9" * 21)

    def test_a_feed_whose_sheet_is_gone_is_said_loudly(self):
        # FAILS if an orphan feed reads as a deleted log: it would be claimed
        # again every lease, silently, forever.
        feed = self._log()
        Sheet.objects.filter(id=feed.sheet_id).delete()
        with self.assertLogs("signals.operations.poll_logs", level="ERROR"):
            outcome = self._poll(feed)
        self.assertEqual(outcome.result, PollResult.BACKOFF)
        self.assertEqual(outcome.next_poll_at, _NOW + _seconds(LOG_POLL_BACKOFF_SECONDS))

    def test_a_log_deleted_while_the_page_was_out_is_left_alone(self):
        feed = self._log()
        self._matches(feed, ["Acme"])
        stale = LogFeed.objects.get(id=feed.id)
        DeleteLogOperation(self.session, feed.sheet_id).run()
        outcome = PollLogOperation(feed=stale, now=_NOW).run()
        self.assertEqual((outcome.result, outcome.next_poll_at), (PollResult.DELETED, None))


class PollDeleteRaceTests(_PollTests):
    def test_a_log_deleted_during_the_read_is_left_alone(self):
        # FAILS if marking the listener gone assumes the feed is still
        # there: the delete removes the listener first, so this read sees
        # its 404, and the rows are gone by the time the mark is written.
        feed = self._log()
        self.signals.on_matches = lambda: DeleteLogOperation(self.session, feed.sheet_id).run()
        outcome = self._poll(feed)
        self.assertEqual((outcome.result, outcome.next_poll_at), (PollResult.DELETED, None))

    def test_a_log_deleted_after_its_page_was_read_is_left_alone(self):
        # The page is out and the listener still answers; the log goes
        # before the rows land, so the append finds no sheet.
        feed = self._log()
        self._matches(feed, ["Acme"])
        local_log = self.logs.get(feed.sheet_id)
        self.signals.on_matches = lambda: self.logs.delete(local_log)
        outcome = self._poll(feed)
        self.assertEqual((outcome.result, outcome.next_poll_at), (PollResult.DELETED, None))


class PollQueueTests(_PollTests):
    def test_the_due_list_is_longest_waiting_first(self):
        # FAILS if the queue is served by id: the oldest log would always
        # go first and a busy one could hold the front.
        newest = self._log("A", due=_NOW - _seconds(1))
        oldest = self._log("B", due=_NOW - _seconds(3))
        middle = self._log("C", due=_NOW - _seconds(2))
        later = self._log("D", due=_NOW + _seconds(1))
        due = LogService.Global.due(_NOW, limit=10)
        self.assertEqual(due, [str(oldest.id), str(middle.id), str(newest.id)])
        self.assertNotIn(str(later.id), due)

    def test_a_claim_holds_a_log_for_one_poller_until_its_lease_lapses(self):
        # FAILS if the claim is not a compare-and-set on the lease: two
        # pollers would read the same log at once.
        feed = self._log()
        claims = LogService.Global
        first = claims.claim(str(feed.id), worker_id="a", now=_NOW, lease_seconds=LOG_POLL_LEASE_SECONDS)
        second = claims.claim(str(feed.id), worker_id="b", now=_NOW, lease_seconds=LOG_POLL_LEASE_SECONDS)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(claims.due(_NOW, limit=10), [])
        lapsed = _NOW + _seconds(LOG_POLL_LEASE_SECONDS + 1)
        taken = claims.claim(str(feed.id), worker_id="b", now=lapsed, lease_seconds=LOG_POLL_LEASE_SECONDS)
        self.assertIsNotNone(taken)
        # The first poller, back from a slow read, cannot release what is
        # no longer its own.
        claims.release(str(feed.id), worker_id="a", next_poll_at=_NOW, polled_at=None)
        self.assertEqual(LogFeed.objects.get(id=feed.id).leased_by, "b")

    def test_steps_drain_every_due_log_and_hand_each_back(self):
        # One step is one page: the busy log's second page waits behind the
        # quiet log, which was already waiting.
        busy = self._log("Busy", due=_NOW - _seconds(2))
        quiet = self._log("Quiet", due=_NOW - _seconds(1))
        self._matches(busy, [f"Co{n}" for n in range(LOG_POLL_PAGE + 1)])
        self._matches(quiet, ["Acme"])
        poller = PollLogsOperation(worker_id="w")
        steps = 0
        while poller.step(now=_NOW):
            steps += 1
        self.assertEqual(steps, 3)
        self.assertEqual(len(self._companies(busy)), LOG_POLL_PAGE + 1)
        self.assertEqual(self._companies(quiet), ["Acme"])
        for feed in (busy, quiet):
            after = LogFeed.objects.get(id=feed.id)
            self.assertEqual((after.leased_by, after.next_poll_at), ("", _NOW + _seconds(LOG_POLL_INTERVAL_SECONDS)))

    def test_a_full_page_goes_behind_the_logs_already_waiting(self):
        # FAILS if a log with a backlog keeps its turn: a busy log would hold
        # a poller and starve every quiet one behind it.
        busy = self._log("Busy", due=_NOW - _seconds(2))
        quiet = self._log("Quiet", due=_NOW - _seconds(1))
        self._matches(busy, [f"Co{n}" for n in range(LOG_POLL_PAGE + 1)])
        poller = PollLogsOperation(worker_id="w")
        poller.step(now=_NOW)
        self.assertEqual(LogService.Global.due(_NOW, limit=10), [str(quiet.id), str(busy.id)])

    def test_a_step_with_nothing_due_reports_none(self):
        self._log(due=_NOW + _seconds(1))
        self.assertFalse(PollLogsOperation(worker_id="w").step(now=_NOW))


class PollCrashTests(_PollTests):
    def test_one_logs_crash_backs_it_off_and_the_next_is_polled(self):
        # FAILS if a poll's crash escapes the step: the daemon would stop
        # the worker, and every account's logs with it.
        broken = self._log("Broken", due=_NOW - _seconds(2))
        fine = self._log("Fine", due=_NOW - _seconds(1))
        self._matches(fine, ["Acme"])
        run = PollLogOperation.run

        def crash_one(op):
            if op.feed.id == broken.id:
                raise ValueError("a bug in one log's cells")
            return run(op)

        poller = PollLogsOperation(worker_id="w")
        with (
            patch.object(PollLogOperation, "run", crash_one),
            self.assertLogs("signals.operations.log_poller", level="ERROR"),
        ):
            self.assertTrue(poller.step(now=_NOW))
        held = LogFeed.objects.get(id=broken.id)
        self.assertEqual((held.leased_by, held.next_poll_at), ("", _NOW + _seconds(LOG_POLL_BACKOFF_SECONDS)))
        self.assertTrue(poller.step(now=_NOW))
        self.assertEqual(self._companies(fine), ["Acme"])


class PollClockTests(_PollTests):
    def test_each_step_reads_the_clock(self):
        # FAILS if a step reuses an old clock read: a log would hold a short
        # lease and be due again too soon.
        feed = self._log(due=_NOW - _seconds(1))
        late = _NOW + _seconds(30)
        with patch("signals.operations.log_poller.timezone.now", return_value=late):
            PollLogsOperation(worker_id="w").step()
        self.assertEqual(LogFeed.objects.get(id=feed.id).next_poll_at, late + _seconds(LOG_POLL_INTERVAL_SECONDS))


class PollRecordTests(_PollTests):
    def test_a_read_through_is_recorded_and_a_failed_one_is_not(self):
        # FAILS if every turn stamps the time: a log failing upstream for
        # hours would still read as freshly polled.
        failing = self._log("Failing", due=_NOW - _seconds(2))
        quiet = self._log("Quiet", due=_NOW - _seconds(1))
        # The first read (the failing log's, longest waiting) answers 503.
        self.signals.next["GET"] = httpx.Response(503)
        poller = PollLogsOperation(worker_id="w")
        poller.step(now=_NOW)
        poller.step(now=_NOW)
        self.assertIsNone(LogFeed.objects.get(id=failing.id).last_polled_at)
        self.assertEqual(LogFeed.objects.get(id=quiet.id).last_polled_at, _NOW)


class PollCommandTests(_PollTests):
    def test_once_drains_the_due_logs_and_exits(self):
        feed = self._log(due=_NOW - _seconds(1))
        self._matches(feed, ["Acme"])
        call_command("poll_signal_logs", "--once")
        self.assertEqual(self._companies(feed), ["Acme"])

    def test_the_healthcheck_window_outlasts_a_slow_step(self):
        # Compose cannot import the constants, so its heartbeat window
        # restates a step's worst case by hand: the idle before it, then one
        # log (a matches read and the listener read that confirms a 404,
        # each a connect and a read at the HTTP timeout). FAILS if a slow
        # but healthy step would read as wedged.
        import re
        from pathlib import Path

        from django.conf import settings

        from signals.constants import LOG_POLL_IDLE_SECONDS

        compose = (Path(__file__).resolve().parents[4] / "docker-compose.yml").read_text()
        window = re.search(r"log_poller\.heartbeat.*-lt (\d+)", compose)
        worst = LOG_POLL_IDLE_SECONDS + 4 * settings.SIGNALS_HTTP_TIMEOUT_SECONDS
        self.assertGreater(int(window.group(1)), worst)
