"""A signal's sources: every log feed becomes its hosted source on migrate,
a source's config is typed by its kind through the kind registry (guarded,
and held to the wire's literal), a kind the poller never reads is never
due, and a hosted row carries its match's id so a page read twice lands
once.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_signal_sources
"""

from __future__ import annotations

import datetime
import typing
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from openbower_schema import HostedSourceSummary
from openbower_schema.logs import SourceKind
from sheets.constants import MAX_SHEET_ROWS
from sheets.models import Sheet, SheetRow
from sheets.services.sheets import SheetService
from signals.constants import SOURCE_CURSOR_MAX_LENGTH, LogStatus
from signals.models import SignalSource
from signals.operations.logs import CreateLogOperation
from signals.operations.poll_logs import PollLogOperation, PollResult
from signals.services.logs import LogService, SignalSourceGlobal
from signals.sources import registry
from signals.sources.base import SourceConfig
from signals.sources.hosted import HostedSource
from signals.sources.registry import (
    HOSTED,
    UnknownSourceKind,
    all_kinds,
    parse_source_config,
    register,
    validate_source_kinds,
)

from .fakes import USER, FakeIdentity, FakeSignals, install, match_body, session_for

_NOW = datetime.datetime(2026, 11, 12, 12, 0, tzinfo=datetime.UTC)
_LATER = _NOW + datetime.timedelta(hours=1)
_BEFORE = [("signals", "0001_initial")]
_AFTER = [("signals", "0002_signal_sources")]


class FeedsBecomeSourcesTests(TransactionTestCase):
    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self) -> None:
        self._migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_a_log_feed_is_copied_as_its_hosted_source(self):
        # FAILS if the copy drops the account, the cursor, the due time or
        # the gone mark: a copied source would be unscoped, re-append every
        # match, poll a parked log at once, or poll a listener that is gone.
        old = self._migrate(_BEFORE)
        gone = datetime.datetime(2026, 11, 10, tzinfo=datetime.UTC)
        feed = old.get_model("signals", "LogFeed").objects.create(
            account_id=USER.account_id,
            sheet_id="01SH" + "A" * 22,
            listener_id="01LI" + "A" * 22,
            signal="funding",
            cursor="01MA" + "A" * 22,
            next_poll_at=_LATER,
            last_polled_at=_NOW,
            listener_gone_at=gone,
        )
        self._migrate(_AFTER)
        source = SignalSource.objects.get(id=feed.id)
        self.assertEqual(source.created_at, feed.created_at)
        self.assertEqual(
            (
                source.kind,
                source.config,
                source.account_id,
                source.sheet_id,
                source.cursor,
                source.next_poll_at,
                source.last_polled_at,
                source.gone_at,
            ),
            (
                HOSTED,
                HostedSource(signal="funding", listener_id="01LI" + "A" * 22),
                USER.account_id,
                feed.sheet_id,
                feed.cursor,
                _LATER,
                _NOW,
                gone,
            ),
        )


class _Quiet(SourceConfig):
    """A kind the poller never reads, registered only inside a test."""

    KIND: ClassVar[str] = "quiet"
    POLLED: ClassVar[bool] = False

    def status(self, source, context):
        return None

    def summary(self, source):
        return HostedSourceSummary(kind=HOSTED, signal="quiet", listener_id="")

    def tear_down(self, signals):
        return None


class SourceRegistryTests(SimpleTestCase):
    def setUp(self) -> None:
        self.roster = sorted(cls.KIND for cls in all_kinds())

    def tearDown(self) -> None:
        # Each refusal must leave the roster as boot built it.
        self.assertEqual(sorted(cls.KIND for cls in all_kinds()), self.roster)

    def test_the_roster_is_the_wire_s_kinds(self):
        # FAILS when a kind module is added without its name on the wire, or
        # the wire names a kind with no module.
        self.assertEqual(sorted(cls.KIND for cls in all_kinds()), sorted(typing.get_args(SourceKind)))

    def test_a_second_class_under_a_taken_kind_refuses_loud(self):
        class Rival(HostedSource):
            pass

        with self.assertRaisesMessage(ValueError, "already registered"):
            register(Rival)
        register(HostedSource)
        self.assertIs(registry.get(HOSTED), HostedSource)

    def test_a_kind_that_declares_no_polled_refuses(self):
        class Undeclared(SourceConfig):
            KIND: ClassVar[str] = "undeclared"

        with self.assertRaisesMessage(ValueError, "must declare POLLED"):
            register(Undeclared)

    def test_a_kind_that_leaves_a_seam_unanswered_refuses(self):
        # FAILS if a new kind can boot without deciding a seam: a log's
        # status, its wire, or its delete would skip it without a word.
        for seam in ("status", "summary", "tear_down"):
            members = {name: getattr(_Quiet, name) for name in ("status", "summary", "tear_down") if name != seam}
            members.update({"KIND": "unanswered", "POLLED": False, "__annotations__": {"KIND": ClassVar[str]}})
            silent = type("Silent", (SourceConfig,), members)
            with self.subTest(seam=seam), self.assertRaisesMessage(ValueError, f"must answer {seam}()"):
                register(silent)

    def test_a_polled_kind_must_answer_poll_and_only_a_polled_one_may(self):
        class Unread(_Quiet):
            KIND: ClassVar[str] = "unread"
            POLLED: ClassVar[bool] = True

        class Stray(_Quiet):
            KIND: ClassVar[str] = "stray"

            def poll(self, source, now):
                raise AssertionError("never polled")

        with self.assertRaisesMessage(ValueError, "is polled and must answer poll()"):
            register(Unread)
        with self.assertRaisesMessage(ValueError, "answers poll() but is not polled"):
            register(Stray)

    def test_a_status_with_no_rank_refuses(self):
        # FAILS if a status a kind says but nothing ranks is dropped: the log
        # would read active while its source said otherwise.
        class Loud(_Quiet):
            def status(self, source, context):
                return LogStatus.ACTIVE

        source = SimpleNamespace(config=Loud())
        sheet = SimpleNamespace(id="01SH" + "C" * 22, row_count=0)
        with self.assertRaisesMessage(ValueError, "no rank"):
            LogService(account_id=USER.account_id).status_of(sheet, (source,))

    def test_a_stored_config_parses_as_its_kind_s_class(self):
        config = parse_source_config('{"kind": "hosted", "signal": "funding", "listener_id": "l1"}')
        self.assertEqual(config, HostedSource(signal="funding", listener_id="l1"))
        self.assertEqual(config.model_dump(mode="json")["kind"], HOSTED)
        # FAILS if a config naming no kind escapes as a bare KeyError or
        # TypeError, which nothing can tell from a bug.
        for unknown in ({"kind": "nope"}, {}, None):
            with self.subTest(unknown=unknown), self.assertRaises(UnknownSourceKind):
                parse_source_config(unknown)

    def test_the_boot_gate_refuses_a_roster_without_hosted(self):
        with patch.dict(registry._REGISTRY, clear=True), self.assertRaisesMessage(ImproperlyConfigured, "hosted"):
            validate_source_kinds()


class UnpolledKindTests(TestCase):
    def tearDown(self) -> None:
        registry._REGISTRY.pop(_Quiet.KIND, None)

    def test_a_kind_the_poller_never_reads_is_never_due(self):
        # FAILS if an unpolled kind keeps a due time: the poller would claim
        # a source it has no read for, and a reconnect's wake would too.
        register(_Quiet)
        source = SignalSource(account_id=USER.account_id, sheet_id="01SH" + "B" * 22, config=_Quiet())
        source.save()
        self.assertIsNone(SignalSource.objects.get(id=source.id).next_poll_at)
        LogService(account_id=USER.account_id).wake()
        self.assertEqual(SignalSourceGlobal.due(_NOW + datetime.timedelta(days=1), limit=8), [])


class FirstDueTests(TestCase):
    def test_a_polled_source_written_past_save_is_never_due(self):
        # FAILS if the due time defaults to now: a row written without
        # save() (a bulk insert, a data migration) of a kind the poller
        # cannot read would be claimed and crash every lease.
        SignalSource.objects.bulk_create(
            [
                SignalSource(
                    account_id=USER.account_id,
                    sheet_id="01SH" + "E" * 22,
                    kind=HOSTED,
                    config=HostedSource(signal="funding", listener_id="l1"),
                )
            ]
        )
        self.assertEqual(SignalSourceGlobal.due(timezone.now() + datetime.timedelta(days=1), limit=8), [])

    def test_a_polled_source_saved_new_is_due_at_once(self):
        source = SignalSource(
            account_id=USER.account_id,
            sheet_id="01SH" + "F" * 22,
            config=HostedSource(signal="funding", listener_id="l1"),
        )
        source.save()
        self.assertEqual(SignalSourceGlobal.due(timezone.now() + datetime.timedelta(days=1), limit=8), [str(source.id)])


class CursorTests(TestCase):
    def test_a_cursor_holds_a_sender_s_opaque_token(self):
        # FAILS if the cursor is sized to a hosted match id: a polled
        # sender's page token or URL would not fit.
        token = "p" * SOURCE_CURSOR_MAX_LENGTH
        source = SignalSource(
            account_id=USER.account_id,
            sheet_id="01SH" + "G" * 22,
            config=HostedSource(signal="funding", listener_id="l1"),
            cursor=token,
        )
        source.save()
        self.assertEqual(SignalSource.objects.get(id=source.id).cursor, token)


class FullCleanTests(TestCase):
    def test_a_source_validates_through_its_typed_config(self):
        # FAILS if the field validates the config object as raw JSON: every
        # source would refuse full_clean() (the admin, a ModelForm).
        source = SignalSource(
            account_id=USER.account_id,
            sheet_id="01SH" + "D" * 22,
            kind=HOSTED,
            config=HostedSource(signal="funding", listener_id="l1"),
        )
        source.full_clean(exclude=["id"])


class HostedSourceTests(TestCase):
    def setUp(self) -> None:
        self.session = session_for()
        self.signals = FakeSignals()
        self.identity = FakeIdentity()
        install(self, self.signals, self.identity)

    def test_the_kind_column_is_the_config_s_own(self):
        log = CreateLogOperation(self.session, label="Raises", signal="funding", filter={}).run()
        source = SignalSource.objects.get(id=log.local_log.sources[0].id)
        self.assertEqual((source.kind, source.config.kind), (HOSTED, HOSTED))
        self.assertEqual(source.config.listener_id, log.listener.id)

    def test_a_page_read_twice_lands_once(self):
        # FAILS if a hosted row carries no external_id: a cursor that falls
        # behind (a copy, a lost write) would append the same matches again.
        log = CreateLogOperation(self.session, label="Raises", signal="funding", filter={}).run()
        source = log.local_log.sources[0]
        SignalSource.objects.filter(id=source.id).update(next_poll_at=_NOW)
        self.signals.matches[source.config.listener_id] = [match_body(1, company="Acme"), match_body(2, company="Beta")]
        claimed = SignalSource.objects.get(id=source.id)
        first = PollLogOperation(source=claimed, config=claimed.config, now=_NOW).run()
        SignalSource.objects.filter(id=source.id).update(cursor="")
        reclaimed = SignalSource.objects.get(id=source.id)
        again = PollLogOperation(source=reclaimed, config=reclaimed.config, now=_NOW).run()
        self.assertEqual(
            (first.result, first.rows, again.result, again.rows), (PollResult.APPENDED, 2, PollResult.APPENDED, 0)
        )
        sheet = Sheet.objects.get(id=source.sheet_id)
        rows = SheetService(account_id=USER.account_id).rows_page(sheet, limit=MAX_SHEET_ROWS)
        self.assertEqual(
            sorted(row.external_id for row in rows),
            sorted([match_body(1, company="x")["id"], match_body(2, company="x")["id"]]),
        )
        self.assertEqual(SheetRow.objects.filter(sheet_id=source.sheet_id).count(), 2)
