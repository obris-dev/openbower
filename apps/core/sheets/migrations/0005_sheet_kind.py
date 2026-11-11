# A sheet's kind, with a database default so every sheet that exists, and
# every insert from a process still on the code before this column, reads
# "standard": additive, no stop. The account index gains the kind (every
# listing reads one kind of an account); the new one is built before the
# old one goes.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sheets', '0004_retire_discover_origin'),
    ]

    operations = [
        migrations.AddField(
            model_name='sheet',
            name='kind',
            field=models.CharField(db_default='standard', default='standard', help_text='SheetKind', max_length=16, verbose_name='kind'),
        ),
        migrations.AddIndex(
            model_name='sheet',
            index=models.Index(fields=['account_id', 'kind', 'id'], name='sheet_account_kind_idx'),
        ),
        migrations.RemoveIndex(
            model_name='sheet',
            name='sheet_account_idx',
        ),
    ]
