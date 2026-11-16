# What feeds a log, with a database default so every feed that exists, and
# every insert from a process still on the code before this column, reads
# "hosted": additive, no stop. A webhook feed names no listener, so the
# listener and signal fall blank by default and a constraint holds a hosted
# feed to its listener; the due index narrows to hosted feeds, the only
# ones the poller reads.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('signals', '0001_initial'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='logfeed',
            name='signals_log_feed_due_idx',
        ),
        migrations.AddField(
            model_name='logfeed',
            name='source',
            field=models.CharField(db_default='hosted', default='hosted', help_text='LogSource', max_length=16, verbose_name='source'),
        ),
        migrations.AlterField(
            model_name='logfeed',
            name='listener_id',
            field=models.CharField(blank=True, default='', max_length=26, verbose_name='listener id'),
        ),
        migrations.AlterField(
            model_name='logfeed',
            name='signal',
            field=models.CharField(blank=True, default='', max_length=32, verbose_name='signal'),
        ),
        migrations.AddIndex(
            model_name='logfeed',
            index=models.Index(condition=models.Q(('listener_gone_at__isnull', True), ('source', 'hosted')), fields=['next_poll_at', 'id'], name='signals_log_feed_due_idx'),
        ),
        migrations.AddConstraint(
            model_name='logfeed',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('source', 'hosted'), _negated=True), models.Q(('listener_id', ''), _negated=True), _connector='OR'), name='signals_log_feed_hosted_listener'),
        ),
    ]
