"""Reconcile the Kafka broker to the ingest topic catalog
(lists.ingest.topics): create each declared topic with its partitions,
replication, and config (max.message.bytes, retention). Idempotent, so it is
safe to run every start; a topic that already exists is left as is. Run at
setup so a topic is never born from broker auto-create with default config
(a deploy turns auto-create off).
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...ingest.topics import TOPICS


class Command(BaseCommand):
    help = "Create the ingest topics from the catalog with their declared config (idempotent)."

    def handle(self, *args, **options) -> None:
        if not settings.INGEST_KAFKA_BOOTSTRAP_SERVERS:
            raise CommandError("INGEST_KAFKA_BOOTSTRAP_SERVERS is not set; cannot provision topics.")
        from confluent_kafka import KafkaError, KafkaException
        from confluent_kafka.admin import AdminClient, NewTopic

        admin = AdminClient({"bootstrap.servers": settings.INGEST_KAFKA_BOOTSTRAP_SERVERS})
        new_topics = [
            NewTopic(
                spec.name,
                num_partitions=spec.partitions,
                replication_factor=spec.replication_factor,
                config=spec.kafka_config(),
            )
            for spec in TOPICS
        ]
        for name, future in admin.create_topics(new_topics).items():
            try:
                future.result()
                self.stdout.write(f"created topic {name}")
            except KafkaException as e:
                # An existing topic is the idempotent no-op; anything else is a
                # real provisioning failure and must not pass silently.
                if e.args[0].code() == KafkaError.TOPIC_ALREADY_EXISTS:
                    self.stdout.write(f"topic {name} already exists")
                else:
                    raise CommandError(f"provisioning topic {name} failed: {e}") from e
