"""The async ingest pipeline: the publisher selection + Kafka producer (the
broker mocked at its client boundary), the event codec, and the worker's
dedupe/apply handler (the idempotent inbox, no broker needed).

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_ingest_worker
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from lists.constants import ListOrigin
from lists.ingest import IngestEvent, IngestPublishError, from_wire, get_ingest_publisher, to_wire
from lists.ingest.consumer import IngestConsumer, handle_ingest_event
from lists.ingest.publisher import KafkaIngestPublisher, LoggingIngestPublisher
from lists.ingest.topics import LIST_ROWS_INGESTED, TOPICS
from lists.models import ProcessedIngestEvent
from lists.services.lists import ListService

_ACCT = "01JQ" + "B" * 22
_USER = "01JQ" + "A" * 22
_LIST = "01JQ" + "L" * 22


def _event(event_id="evt-1", account_id=_ACCT, rows=None, list_id=_LIST) -> IngestEvent:
    return IngestEvent(
        event_id=event_id,
        list_id=list_id,
        account_id=account_id,
        user_id=_USER,
        rows=rows if rows is not None else [{"domain": "acme.com"}],
        received_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    )


def _make_list(account_id=_ACCT) -> str:
    lst = ListService(account_id=account_id, user_id=_USER).create(
        label="ingest target",
        columns=[{"key": "domain", "label": "Domain", "type": "url"}],
        origin=ListOrigin.MANUAL,
    )
    return str(lst.id)


class HandleIngestEventTests(TestCase):
    def setUp(self) -> None:
        self.svc = ListService(account_id=_ACCT, user_id=_USER)
        self.list_id = _make_list()

    def _rows(self, n):
        return [{"domain": f"acme{i}.com"} for i in range(n)]

    def test_first_delivery_appends_the_rows_and_records_the_inbox(self):
        self.assertEqual(handle_ingest_event(_event(list_id=self.list_id, rows=self._rows(2))), "applied")
        self.assertEqual(ProcessedIngestEvent.objects.filter(account_id=_ACCT, event_id="evt-1").count(), 1)
        self.assertEqual(self.svc.get(self.list_id).row_count, 2)  # rows actually landed

    def test_a_redelivery_is_skipped_and_not_reappended(self):
        handle_ingest_event(_event(list_id=self.list_id, rows=self._rows(2)))
        self.assertEqual(handle_ingest_event(_event(list_id=self.list_id, rows=self._rows(2))), "skipped")
        self.assertEqual(self.svc.get(self.list_id).row_count, 2)  # appended once, not twice

    def test_a_distinct_event_appends_again(self):
        handle_ingest_event(_event(event_id="evt-1", list_id=self.list_id, rows=self._rows(2)))
        self.assertEqual(
            handle_ingest_event(_event(event_id="evt-2", list_id=self.list_id, rows=self._rows(3))), "applied"
        )
        self.assertEqual(self.svc.get(self.list_id).row_count, 5)

    def test_dedupe_is_scoped_by_account(self):
        # The same caller key from two accounts (each its own list) both apply.
        list_c = _make_list(account_id="01JQ" + "C" * 22)
        self.assertEqual(handle_ingest_event(_event(event_id="dup", account_id=_ACCT, list_id=self.list_id)), "applied")
        self.assertEqual(
            handle_ingest_event(_event(event_id="dup", account_id="01JQ" + "C" * 22, list_id=list_c)), "applied"
        )

    def test_a_missing_list_is_dropped_not_retried(self):
        # A list deleted after accept is terminal: mark processed, do not
        # redeliver forever.
        ev = _event(event_id="gone", list_id="01JQ" + "Z" * 22, rows=self._rows(1))
        self.assertEqual(handle_ingest_event(ev), "dropped")
        self.assertEqual(ProcessedIngestEvent.objects.filter(event_id="gone").count(), 1)


class EventCodecTests(TestCase):
    def test_round_trips_through_the_wire(self):
        event = _event(rows=[{"domain": "acme.com"}, {"domain": "example.io"}])
        restored = from_wire(json.loads(json.dumps(to_wire(event))))
        self.assertEqual(restored, event)  # frozen dataclass equality, received_at included


class PublisherSelectionTests(TestCase):
    @override_settings(INGEST_KAFKA_BOOTSTRAP_SERVERS="")
    def test_no_bus_selects_the_logging_publisher(self):
        self.assertIsInstance(get_ingest_publisher(), LoggingIngestPublisher)

    @override_settings(INGEST_KAFKA_BOOTSTRAP_SERVERS="kafka:9092")
    def test_a_bus_selects_the_kafka_publisher(self):
        # Constructed but not connected (the producer is lazy), so no broker.
        self.assertIsInstance(get_ingest_publisher(), KafkaIngestPublisher)


class KafkaPublisherTests(TestCase):
    def _publish(self, *, flush_remaining=0, delivery_error=None):
        producer = MagicMock()
        producer.flush.return_value = flush_remaining
        if delivery_error is not None:

            def _produce(topic, key, value, on_delivery):
                on_delivery(delivery_error, None)

            producer.produce.side_effect = _produce
        pub = KafkaIngestPublisher("kafka:9092", LIST_ROWS_INGESTED)
        with patch("confluent_kafka.Producer", return_value=producer):
            pub.publish(_event())
        return producer

    def test_publish_produces_the_serialized_event_keyed_by_list(self):
        producer = self._publish()
        producer.produce.assert_called_once()
        _, kwargs = producer.produce.call_args
        self.assertEqual(producer.produce.call_args.args[0], LIST_ROWS_INGESTED.name)
        self.assertEqual(kwargs["key"], _LIST.encode())
        self.assertEqual(json.loads(kwargs["value"])["event_id"], "evt-1")
        producer.flush.assert_called_once()

    def test_publish_raises_when_the_ack_times_out(self):
        with self.assertRaises(IngestPublishError):
            self._publish(flush_remaining=1)

    def test_publish_raises_on_a_delivery_error(self):
        with self.assertRaises(IngestPublishError):
            self._publish(delivery_error="broker down")


@override_settings(INGEST_KAFKA_BOOTSTRAP_SERVERS="kafka:9092")
class ConsumeLoopTests(TestCase):
    def test_the_loop_heartbeats_subscribes_and_closes_when_drained(self):
        consumer = MagicMock()
        consumer.poll.return_value = None  # empty bus
        with (
            patch("confluent_kafka.Consumer", return_value=consumer),
            patch("lists.ingest.consumer._touch_heartbeat") as heartbeat,
        ):
            IngestConsumer(worker_id="w", stop=threading.Event()).run(once=True)
        heartbeat.assert_called()  # the loop turned (liveness)
        consumer.subscribe.assert_called_once()
        consumer.close.assert_called_once()


@override_settings(INGEST_KAFKA_BOOTSTRAP_SERVERS="kafka:9092")
class ProvisionTopicsTests(TestCase):
    def test_creates_one_topic_per_catalog_entry(self):
        admin = MagicMock()
        future = MagicMock()
        future.result.return_value = None  # created cleanly
        admin.create_topics.return_value = {spec.name: future for spec in TOPICS}
        with patch("confluent_kafka.admin.AdminClient", return_value=admin):
            call_command("provision_topics")
        admin.create_topics.assert_called_once()
        created = admin.create_topics.call_args.args[0]
        self.assertEqual({t.topic for t in created}, {spec.name for spec in TOPICS})
