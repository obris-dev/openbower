# A signal's sources: one row per thing that feeds it, its kind's config
# typed and the polling state beside it. Every log feed so far is a hosted
# signal, copied here as its hosted source with the same id, cursor and
# timing and creation time (a lease is not carried: the poller claims
# afresh). The log feed table stays, read by nothing, so a pod on the code
# before sources never meets a missing table; it is not kept consistent,
# so the release runs with every such pod down, and the next release
# reconciles it and drops it.

import itertools

import openbower_kernel.fields
from django.db import migrations, models


def copy_feeds(apps, schema_editor):
    LogFeed = apps.get_model("signals", "LogFeed")
    SignalSource = apps.get_model("signals", "SignalSource")
    # Chunked both ways: bulk_create lists its input, so an unchunked copy
    # holds every feed in memory at once.
    feeds = LogFeed.objects.order_by("id").iterator(chunk_size=1024)
    for chunk in itertools.batched(feeds, 1024):
        SignalSource.objects.bulk_create(
            [
                SignalSource(
                    id=feed.id,
                    account_id=feed.account_id,
                    sheet_id=feed.sheet_id,
                    kind="hosted",
                    config={"kind": "hosted", "signal": feed.signal, "listener_id": feed.listener_id},
                    cursor=feed.cursor,
                    next_poll_at=feed.next_poll_at,
                    last_polled_at=feed.last_polled_at,
                    gone_at=feed.listener_gone_at,
                )
                for feed in chunk
            ]
        )
    # The creation time in one statement: bulk_create stamps its own.
    feed_created = LogFeed.objects.filter(id=models.OuterRef("id")).values("created_at")[:1]
    SignalSource.objects.update(created_at=models.Subquery(feed_created))


class Migration(migrations.Migration):

    dependencies = [
        ('signals', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='SignalSource',
            fields=[
                ('id', openbower_kernel.fields.ULIDField(editable=False, max_length=26, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated at')),
                ('account_id', models.CharField(max_length=26, verbose_name='account id')),
                ('sheet_id', models.CharField(max_length=26, verbose_name='sheet id')),
                ('kind', models.CharField(help_text='A registered source kind', max_length=16, verbose_name='kind')),
                ('config', models.JSONField(help_text="The kind's config, signals.sources", verbose_name='config')),
                ('cursor', models.CharField(blank=True, default='', max_length=512, verbose_name='cursor')),
                ('next_poll_at', models.DateTimeField(blank=True, null=True, verbose_name='next poll at')),
                ('leased_until', models.DateTimeField(blank=True, null=True, verbose_name='leased until')),
                ('leased_by', models.CharField(blank=True, default='', max_length=128, verbose_name='leased by')),
                ('last_polled_at', models.DateTimeField(blank=True, null=True, verbose_name='last polled at')),
                ('gone_at', models.DateTimeField(blank=True, null=True, verbose_name='gone at')),
            ],
            options={
                'verbose_name': 'signal source',
                'verbose_name_plural': 'signal sources',
                'indexes': [models.Index(fields=['account_id', 'id'], name='signals_source_account_idx'), models.Index(condition=models.Q(('gone_at__isnull', True), ('next_poll_at__isnull', False)), fields=['next_poll_at', 'id'], name='signals_source_due_idx')],
                'constraints': [models.UniqueConstraint(fields=('sheet_id', 'kind'), name='signals_source_sheet_kind_uniq')],
            },
        ),
        migrations.RunPython(copy_feeds, migrations.RunPython.noop),
    ]
