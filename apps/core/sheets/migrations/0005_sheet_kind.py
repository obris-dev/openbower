# A sheet's kind and its autofill, each with a database default so every
# sheet that exists, and every insert from a process still on the code
# before these columns, reads "standard" and on: additive, no stop. A
# second account index carries the kind, for the listing that reads one
# kind of an account.

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
        migrations.AddField(
            model_name='sheet',
            name='autofill',
            field=models.BooleanField(db_default=True, default=True, verbose_name='autofill'),
        ),
        migrations.AddIndex(
            model_name='sheet',
            index=models.Index(fields=['account_id', 'kind', 'id'], name='sheet_account_kind_idx'),
        ),
    ]
