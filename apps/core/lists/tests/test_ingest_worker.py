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

from lists.ingest import IngestEvent, IngestPublishError, from_wire, get_ingest_publisher, to_wire
from lists.ingest.consumer import IngestConsumer, handle_ingest_event
from lists.ingest.publisher import KafkaIngestPublisher, LoggingIngestPublisher
from lists.ingest.topics import LIST_ROWS_INGESTED, TOPICS
from lists.models import ProcessedIngestEvent

_ACCT = "01JQ" + "B" * 22
_USER = "01JQ" + "A" * 22
_LIST = "01JQ" + "L" * 22


def _event(event_id="evt-1", account_id=_ACCT, rows=None) -> IngestEvent:
    return IngestEvent(
        event_id=event_id,
        list_id=_LIST,
        account_id=account_id,
        user_id=_USER,
        rows=rows if rows is not None else [{"domain": "acme.com"}],
        received_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    )


class HandleIngestEventTests(TestCase):
    def test_first_delivery_applies_and_records_the_inbox_row(self):
        self.assertEqual(handle_ingest_event(_event()), "applied")
        self.assertEqual(ProcessedIngestEvent.objects.filter(account_id=_ACCT, event_id="evt-1").count(), 1)

    def test_a_redelivery_is_skipped_not_reapplied(self):
        self.assertEqual(handle_ingest_event(_event()), "applied")
        self.assertEqual(handle_ingest_event(_event()), "skipped")  # same (account, event_id)
        self.assertEqual(ProcessedIngestEvent.objects.filter(account_id=_ACCT, event_id="evt-1").count(), 1)

    def test_a_distinct_event_id_applies(self):
        handle_ingest_event(_event(event_id="evt-1"))
        self.assertEqual(handle_ingest_event(_event(event_id="evt-2")), "applied")
        self.assertEqual(ProcessedIngestEvent.objects.filter(account_id=_ACCT).count(), 2)

    def test_dedupe_is_scoped_by_account(self):
        # The SAME caller-chosen key from two accounts must not collide.
        self.assertEqual(handle_ingest_event(_event(event_id="dup", account_id=_ACCT)), "applied")
        self.assertEqual(handle_ingest_event(_event(event_id="dup", account_id="01JQ" + "C" * 22)), "applied")


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
