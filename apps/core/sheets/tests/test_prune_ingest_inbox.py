"""The ingest inbox prune: age alone decides, paged, the command logs its
count, and the inbox outlasts the bus.

Run: DJANGO_ENV=test uv run python manage.py test sheets.tests.test_prune_ingest_inbox
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from ulid import ULID

from sheets.constants import INGEST_INBOX_MAX_AGE_SECONDS
from sheets.ingest.topics import SHEET_ROWS_INGESTED
from sheets.models import ProcessedIngestEvent
from sheets.operations.prune_ingest_inbox import PruneIngestInboxOperation

ACCOUNT = "01AC" + "A" * 22
SHEET = "01SH" + "A" * 22


def _entry(age_seconds: int) -> ProcessedIngestEvent:
    born = timezone.now() - timedelta(seconds=age_seconds)
    return ProcessedIngestEvent.objects.create(
        id=str(ULID.from_timestamp(born.timestamp())),
        account_id=ACCOUNT,
        event_id=str(ULID()),
        sheet_id=SHEET,
    )


class PruneIngestInboxTests(TestCase):
    def test_old_entries_go_and_young_entries_stay(self):
        old = _entry(INGEST_INBOX_MAX_AGE_SECONDS + 60)
        young = _entry(60)
        self.assertEqual(PruneIngestInboxOperation().run(), 1)
        self.assertFalse(ProcessedIngestEvent.objects.filter(id=old.id).exists())
        self.assertTrue(ProcessedIngestEvent.objects.filter(id=young.id).exists())

    def test_pages_through_a_backlog(self):
        for _ in range(3):
            _entry(INGEST_INBOX_MAX_AGE_SECONDS + 60)
        with patch("sheets.operations.prune_ingest_inbox.INGEST_INBOX_WRITE_BATCH", 2):
            self.assertEqual(PruneIngestInboxOperation().run(), 3)
        self.assertEqual(ProcessedIngestEvent.objects.count(), 0)

    def test_command_logs_the_count(self):
        _entry(INGEST_INBOX_MAX_AGE_SECONDS + 60)
        with self.assertLogs("sheets.management.commands.prune_ingest_inbox", level="INFO") as logs:
            call_command("prune_ingest_inbox")
        self.assertIn("purged 1", logs.output[0])


class InboxOutlastsTheBusTests(SimpleTestCase):
    def test_the_inbox_keeps_an_entry_longer_than_the_topic_keeps_its_event(self):
        # FAILS if the retention drops under the topic's: an event the bus
        # still holds could redeliver after its inbox entry is gone and
        # append twice.
        self.assertGreater(INGEST_INBOX_MAX_AGE_SECONDS * 1000, SHEET_ROWS_INGESTED.retention_ms)
