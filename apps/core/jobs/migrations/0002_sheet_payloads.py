# The sheet kinds' payloads name their sheet `sheet_id`, so every stored
# one, open or finished, is rewritten: a payload still saying `list_id`
# fails its kind's validation when the runner, the fill wire or the
# progress read parses it. Literals, not the kinds' constants, so a later
# rename cannot break a fresh migrate.

from django.db import migrations

_SHEET_KINDS = ("fill", "column_backfill", "rerank")


def _rekey(old, new):
    def run(apps, schema_editor):
        Job = apps.get_model("jobs", "Job")
        jobs = Job.objects.filter(kind__in=_SHEET_KINDS, payload__has_key=old).only("payload")
        for job in jobs.iterator():
            job.payload[new] = job.payload.pop(old)
            job.save(update_fields=["payload"])

    return run


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(_rekey("list_id", "sheet_id"), _rekey("sheet_id", "list_id")),
    ]
