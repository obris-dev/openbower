# A sheet saved from a lookalike run says origin "discover", a value the
# contract no longer carries, so the sheets read would fail its validation.
# Those sheets are plain sheets now: "manual", with the run id cleared (the
# run lives upstream and nothing reads it). The reverse is a no-op: which
# manual sheets came from a run is not recoverable, and none needs to be.

from django.db import migrations


def _retire(apps, schema_editor):
    Sheet = apps.get_model("sheets", "Sheet")
    Sheet.objects.filter(origin="discover").update(origin="manual", origin_ref="")


class Migration(migrations.Migration):
    dependencies = [
        ("sheets", "0003_sheet_column_help"),
    ]

    operations = [
        migrations.RunPython(_retire, migrations.RunPython.noop),
    ]
