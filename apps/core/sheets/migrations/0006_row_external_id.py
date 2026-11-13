# A row's sender id, with a database default so every row that exists, and
# every insert from a process still on the code before this column, reads
# blank: additive, no stop. A partial unique index holds one row per id on
# a sheet, comparing only rows that carry one.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sheets', '0005_sheet_kind'),
    ]

    operations = [
        migrations.AddField(
            model_name='sheetrow',
            name='external_id',
            field=models.CharField(blank=True, db_default='', default='', max_length=256, verbose_name='external id'),
        ),
        migrations.AddConstraint(
            model_name='sheetrow',
            constraint=models.UniqueConstraint(condition=models.Q(('external_id', ''), _negated=True), fields=('sheet_id', 'external_id'), name='sheet_row_external_uniq'),
        ),
    ]
